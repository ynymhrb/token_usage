import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import copytree


SERVER = Path(__file__).parent.parent / "plugins" / "codex-context-optimizer" / "scripts" / "token_analyzer_server.py"
FIXTURE = Path(__file__).parent / "fixtures" / "codex" / "rollout-valid.jsonl"


def invoke(requests: list[dict[str, object]], env: dict[str, str]) -> list[dict[str, object]]:
    payload = "\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n"
    completed = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env={**os.environ, 'TOKEN_ANALYZER_ACCOUNT_QUERY': '0', **env},
        check=False,
    )
    return [json.loads(line) for line in completed.stdout.splitlines() if line]


class TokenAnalyzerServerTests(unittest.TestCase):
    def test_internal_failure_keeps_id_and_next_request_still_works(self):
        launcher = "import sys,runpy; sys.path.insert(0,sys.argv[1]); import token_analyzer_runtime as r; r.analyze=lambda *a: 1/0; runpy.run_path(sys.argv[2],run_name='__main__')"
        requests = [dict(jsonrpc='2.0', id='failure', method='tools/call', params={'name': 'analyze'}),
                    dict(jsonrpc='2.0', id='next', method='tools/list')]
        result = subprocess.run([sys.executable, '-c', launcher, str(SERVER.parent), str(SERVER)],
            input='\n'.join(map(json.dumps, requests))+'\n', text=True, encoding='utf-8', capture_output=True, timeout=10)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row['id'] for row in rows], ['failure', 'next'])
        self.assertEqual(rows[0]['error']['code'], -32603)
        self.assertIn('result', rows[1])

    def test_companion_server_errors_also_keep_request_id(self):
        requests = [dict(jsonrpc='2.0', id='bad', method='unknown'),
                    dict(jsonrpc='2.0', method='notifications/cancelled'),
                    dict(jsonrpc='2.0', id=0, method='tools/list')]
        result = subprocess.run([sys.executable, str(SERVER.with_name('mcp_server.py'))],
            input='\n'.join(map(json.dumps, requests))+'\n', text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual([json.loads(line)['id'] for line in result.stdout.splitlines()], ['bad', 0])

    def test_invalid_scope_keeps_request_id_and_service_continues(self):
        responses = invoke([
            {'jsonrpc': '2.0', 'id': 'bad-scope', 'method': 'tools/call',
             'params': {'name': 'analyze', 'arguments': {'scope': '今天或昨天'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 'bad-scope'}},
            {'jsonrpc': '2.0', 'id': 0, 'method': 'unknown'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        ], {})
        self.assertEqual([r['id'] for r in responses], ['bad-scope', 0, 2])
        self.assertIn('error', responses[0])
        self.assertIn('result', responses[-1])

    def test_natural_today_scope_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = invoke([{'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                'params': {'name': 'analyze', 'arguments': {'scope': '分析今天会话'}}}],
                {'CODEX_SESSIONS_ROOT': directory})
        self.assertEqual(responses[0]['id'], 7)
        self.assertIn('今天（上海时间）', responses[0]['result']['content'][0]['text'])

    def test_initialize_negotiates_the_client_protocol_version(self):
        responses = invoke([{
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }], {})

        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")

    def test_tools_list_exposes_only_analyze(self):
        responses = invoke([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], {})

        tools = responses[0]["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["analyze"])

    def test_analyze_returns_full_chat_diagnostic_without_writing_a_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            output = Path(temporary) / "reports"
            root.mkdir()
            (root / "rollout-2026-11-02T12-00-00_thread-123.jsonl").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            responses = invoke([
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "analyze", "arguments": {}}},
            ], {
                "CODEX_THREAD_ID": "thread-123",
                "CODEX_SESSIONS_ROOT": str(root),
                "TOKEN_ANALYZER_OUTPUT_DIR": str(output),
            })

            text = responses[0]["result"]["content"][0]["text"]
            self.assertIn("# Codex 上下文与 Token 诊断", text)
            self.assertIn("## 用量总结", text)
            self.assertNotIn("## 覆盖率与可信度", text)
            self.assertNotIn("## 官方账户用量", text)
            self.assertNotIn("### 按回合", text)
            self.assertNotIn("## 精确 Token 总览", text)
            self.assertIn("## 输入内容分类", text)
            sections = ['## 用量总结', '## 输入内容分类', '### 输入中的工具结果分类', '## 消耗分布']
            positions = [text.index(section) for section in sections]
            self.assertEqual(positions, sorted(positions))
            self.assertNotIn('本地 `token_usage_record` 汇总', text)
            self.assertNotIn('## 上下文构成', text)
            self.assertNotIn("## 数据缺口", text)
            self.assertIn("缓存输入", text)
            self.assertNotIn("会话记录文件", text)
            self.assertNotIn("需要带来源标记", text)
            self.assertNotIn("rollout", text)
            self.assertNotIn("trace", text)
            self.assertNotIn("静态指令为合并字段", text)
            self.assertNotIn("private tool input", text)
            self.assertNotIn("完整报告", text)
            self.assertNotIn("脱敏报告", text)
            self.assertFalse(output.exists())

    def test_invalid_focus_returns_mcp_error_without_report(self):
        responses = invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "analyze", "arguments": {"focus": "bad"}}},
        ], {})

        self.assertEqual(responses[0]["error"]["code"], -32602)

    def test_empty_request_structure_keeps_diagnostic_available_for_all_focuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            output = Path(temporary) / "reports"
            root.mkdir()
            (root / "rollout-2026-11-02T12-00-00_thread-123.jsonl").write_text(
                FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (root / "rollout-2026-11-02T12-00-00_thread-123.llm_calls.jsonl").write_text(
                json.dumps({"request": {}}) + "\n", encoding="utf-8"
            )
            responses = invoke([
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "analyze", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "analyze", "arguments": {"focus": "context"}}},
            ], {
                "CODEX_THREAD_ID": "thread-123",
                "CODEX_SESSIONS_ROOT": str(root),
                "TOKEN_ANALYZER_OUTPUT_DIR": str(output),
            })

            self.assertEqual(len(responses), 2)
            for response in responses:
                text = response["result"]["content"][0]["text"]
                self.assertIn("# Codex 上下文与 Token 诊断", text)
                self.assertIn("## 输入内容分类", text)
                self.assertNotIn("不能判断系统提示", text)

    def test_server_runs_when_plugin_is_copied_to_an_install_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "codex-context-optimizer"
            copytree(Path(__file__).parent.parent / "plugins" / "codex-context-optimizer", cache_root)
            root = Path(temporary) / "sessions"
            output = Path(temporary) / "reports"
            root.mkdir()
            (root / "rollout-2026-11-02T12-00-00_thread-123.jsonl").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            global SERVER
            original_server = SERVER
            SERVER = cache_root / "scripts" / "token_analyzer_server.py"
            try:
                responses = invoke([
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "analyze", "arguments": {}}},
                ], {
                    "CODEX_THREAD_ID": "thread-123",
                    "CODEX_SESSIONS_ROOT": str(root),
                    "TOKEN_ANALYZER_OUTPUT_DIR": str(output),
                })
            finally:
                SERVER = original_server

        self.assertIn("result", responses[0])


if __name__ == "__main__":
    unittest.main()
