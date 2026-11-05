"""通过官方 Codex App Server 读取账户用量；不发送推理请求。"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo


def codex_command():
    explicit = os.environ.get('TOKEN_ANALYZER_CODEX')
    if explicit:
        return [explicit]
    executable = shutil.which('codex.exe') or shutil.which('codex')
    if executable and Path(executable).suffix.lower() not in {'.cmd', '.ps1', '.bat'}:
        return [executable]
    shim = executable or shutil.which('codex.cmd')
    if shim:
        root = Path(shim).parent / 'node_modules/@openai/codex/node_modules'
        candidates = list(root.glob('@openai/codex-*/vendor/*/codex/codex.exe'))
        candidates += list(root.glob('@openai/codex-*/vendor/*/bin/codex.exe'))
        if candidates:
            return [str(candidates[0])]
    raise FileNotFoundError('未找到 Codex 可执行文件')


def query_account(session_id=None, *, timeout=15, command=None):
    """每次查询独立连接；超时清理自己启动的进程，保留部分成功结果。"""
    result = {'queried_at': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'), 'session': bool(session_id)}
    process = None
    incoming = queue.Queue()
    reader = None
    try:
        args = (command or codex_command()) + ['app-server', '--stdio']
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        def read():
            try:
                for line in process.stdout:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            incoming.put(value)
                    except ValueError:
                        pass
            finally:
                incoming.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def send(value):
            process.stdin.write(json.dumps(value) + '\n')
            process.stdin.flush()
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'token_usage_analyzer', 'version': '1'}, 'capabilities': {'experimentalApi': True}}})
        pending = {2: 'limits', 3: 'usage'}
        deadline = time.monotonic() + timeout
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result['error'] = '官方账户查询超时'
                break
            try:
                response = incoming.get(timeout=remaining)
            except queue.Empty:
                result['error'] = '官方账户查询超时'
                break
            if response is None:
                result['error'] = '官方账户连接提前结束'
                break
            if response.get('id') == 1:
                if 'error' in response:
                    result['error'] = '官方账户接口初始化失败'
                    break
                send({'method': 'initialized'})
                send({'id': 2, 'method': 'account/rateLimits/read'})
                send({'id': 3, 'method': 'account/usage/read', 'params': {'threadId': session_id} if session_id else {}})
            elif response.get('id') in pending:
                key = pending.pop(response['id'])
                if isinstance(response.get('result'), dict):
                    result[key] = response['result']
                else:
                    # 不回显服务端原始错误，避免把认证信息或内部响应带入报告。
                    result[key + '_error'] = '官方接口未返回有效数据（可能未登录、无权限或版本不支持）'
    except (OSError, ValueError):
        result['error'] = '无法启动或连接本地 Codex 官方账户接口'
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if reader:
                reader.join(timeout=1)
            if process.stdout:
                process.stdout.close()
    return result


def render_account(result):
    lines = ['## 官方账户用量', '', f"查询时间：{result['queried_at']}（上海时间）。账户额度为实时共享快照，不受上方本地分析时间范围限制。", '']
    limits = result.get('limits', {})
    buckets = limits.get('rateLimitsByLimitId') or {'codex': limits.get('rateLimits')}
    if not isinstance(buckets, dict):
        buckets = {}
    found = False
    for name, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for key in ('primary', 'secondary'):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            percent = window.get('usedPercent')
            minutes = window.get('windowDurationMins')
            reset = window.get('resetsAt')
            try:
                reset_text = datetime.fromtimestamp(reset, ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M') if reset is not None else '未提供'
            except (ValueError, TypeError, OSError, OverflowError):
                reset_text = '未提供'
            label = {300: '五小时', 10080: '一周'}.get(minutes, f'{minutes} 分钟' if minutes is not None else '时长未提供')
            safe_name = str(name).replace('\n', ' ').replace('|', '\\|')
            lines.append(f'- {safe_name} / {label}额度：已用 {percent}%；重置时间 {reset_text}。' if percent is not None else f'- {safe_name} / {label}额度：使用比例未提供；重置时间 {reset_text}。')
            found = True
    if not found:
        lines.append('- 额度：未取得有效窗口数据。')
    usage = result.get('usage', {})
    if result['session']:
        thread = usage.get('threadUsage')
        if isinstance(thread, dict):
            credits = thread.get('estimatedUsageCreditsMicros')
            usd = thread.get('estimatedUsageUsdMicros')
            lines.append('- 当前会话累计估算积分：' + (f'{credits / 1_000_000:,.6f}' if isinstance(credits, (int, float)) else '未提供') + '。')
            lines.append('- 当前会话累计估算金额：' + (f'{usd / 1_000_000:,.6f} 美元' if isinstance(usd, (int, float)) else '未提供') + '。')
        else:
            if 'usage' not in result:
                lines.append('- 当前会话估算积分/金额：查询未成功，无法确定，不代表零。')
            else:
                lines.append('- 当前会话估算积分/金额：服务方未提供，不代表零。')
    else:
        summary = usage.get('summary') or {}
        lifetime = summary.get('lifetimeTokens')
        lines.append('- 账户累计活动 Token（非所选时间范围）：' + (f'{lifetime:,}' if isinstance(lifetime, int) else '未提供') + '。')
        lines.append('- 多会话或时间范围分析不把当前会话费用当作整个范围的费用。')
    for key in ('error', 'limits_error', 'usage_error'):
        if result.get(key):
            label = {'limits_error': '额度查询：', 'usage_error': '活动/费用查询：'}.get(key, '')
            lines.append('- ' + label + result[key] + '；本地分析仍有效。')
    lines.extend(['', '积分/金额是官方估算字段，不等于订阅额度逐条扣减或实际账单；空值与零严格区分。查询只读取账户接口，不调用模型，不保存账户响应。', ''])
    return '\n'.join(lines)
