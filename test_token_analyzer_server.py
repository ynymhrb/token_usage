import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SERVER = Path(__file__).parent / "codex-context-optimizer" / "scripts" / "token_analyzer_server.py"
FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "codex" / "rollout-valid.jsonl"


def invoke(requests: list[dict[str, object]], env: dict[str, str]) -> list[dict[str, object]]:
    payload = "\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n"
    completed = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env={**os.environ, **env},
        check=False,
    )
    return [json.loads(line) for line in completed.stdout.splitlines() if line]


class TokenAnalyzerServerTests(unittest.TestCase):
    def test_tools_list_exposes_only_analyze(self):
        responses = invoke([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], {})

        tools = responses[0]["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["analyze"])

    def test_analyze_returns_compact_summary_and_writes_fixed_report(self):
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
            reports = list(output.glob("*.json"))
        self.assertIn("当前会话", text)
        self.assertNotIn("private tool input", text)
        self.assertEqual(len(reports), 1)

    def test_invalid_focus_returns_mcp_error_without_report(self):
        responses = invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "analyze", "arguments": {"focus": "bad"}}},
        ], {})

        self.assertEqual(responses[0]["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
