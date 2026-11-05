import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codex_token.plugin_service import PluginAnalysisError, run_plugin_analysis


class PluginServiceTests(unittest.TestCase):
    def _write_rollout(self, root: Path, name: str) -> None:
        fixture = Path(__file__).parent / "tests" / "fixtures" / "codex" / "rollout-valid.jsonl"
        (root / name).write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    def test_default_scope_analyzes_only_current_session_and_writes_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            output = Path(temporary) / "reports"
            root.mkdir()
            self._write_rollout(root, "rollout-2026-11-02T12-00-00_thread-123.jsonl")
            self._write_rollout(root, "rollout-2026-11-02T12-00-00_other-thread.jsonl")

            result = run_plugin_analysis(
                scope=None,
                focus=None,
                sessions_root=root,
                output_dir=output,
                current_session_id="thread-123",
                now=datetime(2026, 11, 2, 13, tzinfo=timezone.utc),
            )

            report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(result["matched_rollout_count"], 1)
        self.assertEqual(report["analysis_scope"]["description"], "当前会话")
        self.assertNotIn("private tool input", result["summary"])

    def test_context_focus_uses_same_scope_and_returns_top_category_only(self):
        record = {"request": {"input": [{"type": "function_call_output", "output": "私密工具输出"}]}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            output = Path(temporary) / "reports"
            root.mkdir()
            (root / "rollout-2026-11-02T12-00-00_thread-123.llm_calls.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            result = run_plugin_analysis(
                scope=None,
                focus="context",
                sessions_root=root,
                output_dir=output,
                current_session_id="thread-123",
                now=datetime(2026, 11, 2, 13, tzinfo=timezone.utc),
            )

        self.assertEqual(result["focus"], "context")
        self.assertIn("工具结果", result["summary"])
        self.assertNotIn("私密工具输出", result["summary"])

    def test_invalid_focus_and_missing_current_session_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "reports"
            with self.assertRaisesRegex(PluginAnalysisError, "focus"):
                run_plugin_analysis("全部历史", "everything", root, output, None, datetime.now(timezone.utc))
            with self.assertRaisesRegex(PluginAnalysisError, "当前会话"):
                run_plugin_analysis(None, None, root, output, None, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
