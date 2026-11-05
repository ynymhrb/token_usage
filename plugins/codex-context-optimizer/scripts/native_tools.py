"""通过隔离的原生 Codex 和本地模拟接口获取工具定义基线。"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from datetime import datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from account_usage import codex_command


def native_command():
    if os.environ.get('TOKEN_ANALYZER_CODEX'):
        return codex_command()
    desktop = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin'
    candidates = list(desktop.glob('*/codex.exe')) if desktop.is_dir() else []
    return [str(max(candidates, key=lambda path: path.stat().st_mtime))] if candidates else codex_command()


@lru_cache(maxsize=16)
def _probe(executable, modified, model):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16 * 1024 * 1024:
                    self.send_error(413)
                    return
                request = json.loads(self.rfile.read(length))
                definitions = list(request.get('tools') or [])
                for item in request.get('input', []):
                    if isinstance(item, dict) and item.get('type') == 'additional_tools':
                        definitions.extend(item.get('tools') or [])
                captured.extend(definitions)
                events = [
                    {'type': 'response.created', 'response': {'id': 'native_catalog'}},
                    {'type': 'response.output_item.done', 'item': {'type': 'message', 'id': 'catalog_done', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '完成'}]}},
                    {'type': 'response.completed', 'response': {'id': 'native_catalog', 'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}},
                ]
                data = ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n' for event in events).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ValueError, TypeError, OSError):
                return

    runtime = Path(os.environ.get('TOKEN_ANALYZER_RUNTIME', 'D:/ai_soft/tokenUsage/native-tools'))
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='catalog-', dir=runtime) as directory:
        home = Path(directory) / 'home'
        work = Path(directory) / 'work'
        home.mkdir()
        work.mkdir()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        listener = threading.Thread(target=server.serve_forever, daemon=True)
        listener.start()
        env = dict(os.environ, CODEX_HOME=str(home))
        # 不继承账户凭据；模拟服务只返回结束消息，不发出工具调用。
        for key in list(env):
            if any(word in key.upper() for word in ('API_KEY', 'AUTH_TOKEN', 'ACCESS_TOKEN')):
                env.pop(key)
        args = [executable, '-a', 'never', 'exec', '--ephemeral', '--skip-git-repo-check', '-s', 'read-only',
                '-m', model, '-c', 'model_provider="token_usage_catalog"',
                '-c', 'model_providers.token_usage_catalog.name="本地工具定义查询"',
                '-c', f'model_providers.token_usage_catalog.base_url="http://127.0.0.1:{server.server_port}/v1"',
                '-c', 'model_providers.token_usage_catalog.wire_api="responses"',
                '-c', 'model_providers.token_usage_catalog.requires_openai_auth=false',
                '-c', 'model_providers.token_usage_catalog.supports_websockets=false',
                '本地工具定义查询，无需执行任何工具。']
        try:
            result = subprocess.run(args, cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode or not captured:
                raise ValueError('原生工具定义查询未返回有效定义')
            # 模拟接口只用于取首轮声明，不使用其零用量进入真实报告。
            return json.dumps(captured, ensure_ascii=False, sort_keys=True)
        finally:
            server.shutdown()
            server.server_close()
            listener.join(timeout=1)


def query_native_tools(model):
    executable = native_command()[0]
    return _probe(executable, Path(executable).stat().st_mtime_ns, model)


def supplement_native_tools(paths, scope, contents, query=None):
    """只补有真实用量且未保存任何工具声明的会话，每个会话补一次。"""
    query = query or query_native_tools
    sessions = {}
    for path in paths:
        try:
            rows = []
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get('payload'), dict):
                        rows.append(row)
                except ValueError:
                    continue
        except (OSError, UnicodeError):
            continue
        has_definitions = any(row.get('type') == 'response_item' and row['payload'].get('type') == 'additional_tools' and row['payload'].get('tools') for row in rows)
        active = False
        model = None
        selected_model = None
        session = str(path)
        for row in rows:
            payload = row['payload']
            if row.get('type') == 'session_meta':
                session = payload.get('id') or payload.get('session_id') or session
            if row.get('type') == 'turn_context':
                model = payload.get('model') or model
            usage = payload.get('usage', payload)
            if row.get('type') != 'token_usage_record' or not isinstance(usage, dict) or not isinstance(usage.get('input_tokens'), int):
                continue
            if scope[0] == 'time':
                try:
                    stamp = datetime.fromisoformat(row.get('timestamp', '').replace('Z', '+00:00'))
                    if stamp.tzinfo is None or not scope[2] <= stamp < scope[3]:
                        continue
                except (ValueError, TypeError):
                    continue
            active = True
            selected_model = model
        entry = sessions.setdefault(session, {'has_definitions': False, 'model': None})
        entry['has_definitions'] = entry['has_definitions'] or bool(has_definitions)
        if active and selected_model:
            entry['model'] = selected_model
    missing = {key: entry['model'] for key, entry in sessions.items() if entry['model'] and not entry['has_definitions']}
    results = {}
    failures = 0
    for model in sorted(set(missing.values())):
        try:
            results[model] = query(model)
        except (OSError, ValueError, subprocess.SubprocessError):
            failures += sum(value == model for value in missing.values())
    for model in missing.values():
        if model in results:
            contents['工具定义（原生补估）'].append(results[model])
    return len(missing) - failures, failures
