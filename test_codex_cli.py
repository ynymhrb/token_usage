import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_token.cli import main


class CodexCliTests(unittest.TestCase):
    def test_analyze_defaults_to_current_session_and_prints_compact_summary(self):
        fixture = Path(__file__).parent / "tests" / "fixtures" / "codex" / "rollout-valid.jsonl"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rollout-2026-11-02T19-00-00_thread-123.jsonl").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            (root / "rollout-2026-11-02T11-00-00_other-thread.jsonl").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            output = io.StringIO()
            with patch.dict("os.environ", {"CODEX_THREAD_ID": "thread-123"}, clear=False), redirect_stdout(output):
                code = main(["analyze", "--sessions-root", str(root)])

        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertNotIn("private tool input", rendered)
        summary = json.loads(rendered)
        self.assertEqual(summary["scope"]["matched_rollout_count"], 1)
        self.assertEqual(summary["scope"]["description"], "当前会话")

    def test_analyze_without_scope_or_current_session_rejects_history_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            errors = io.StringIO()
            with patch.dict("os.environ", {"CODEX_THREAD_ID": "", "CODEX_SESSION_ID": ""}, clear=False), redirect_stdout(output), redirect_stderr(errors):
                code = main(["analyze", "--sessions-root", temporary])

        self.assertEqual(code, 2)
        self.assertIn("当前会话", errors.getvalue())

    def test_scope_overrides_current_session_and_full_report_requires_output(self):
        fixture = Path(__file__).parent / "tests" / "fixtures" / "codex" / "rollout-valid.jsonl"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "report.json"
            (root / "rollout-2026-11-02T11-00-00_other-thread.jsonl").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            output = io.StringIO()
            with patch.dict("os.environ", {"CODEX_THREAD_ID": "thread-123"}, clear=False), redirect_stdout(output):
                code = main([
                    "analyze", "--sessions-root", str(root), "--scope", "2026-11-02 18:00 至 2026-11-02 20:00",
                    "--output", str(destination),
                ])
            report = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertNotIn("private tool input", output.getvalue())
        self.assertEqual(report["analysis_scope"]["description"], "用户指定时间：2026-11-02 18:00 至 2026-11-02 20:00")
        self.assertEqual(report["analysis_scope"]["matched_rollout_count"], 1)

    def test_recommend_rejects_raw_jsonl(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["recommend", "tests/fixtures/codex/rollout-valid.jsonl"])
        self.assertNotEqual(code, 0)

    def test_context_breakdown_reports_structure_without_request_body(self):
        record = {
            "request": {
                "instructions": {"text": "静态规则"},
                "persisted_dynamic_tools": [{"name": "exec"}],
                "input": [
                    {"type": "message", "role": "user", "content": "私密用户问题"},
                    {"type": "function_call_output", "output": "私密工具输出"},
                ],
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "context.json"
            (root / "sample.llm_calls.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "context-breakdown", "--sessions-root", str(root), "--scope", "全部历史",
                    "--output", str(destination),
                ])

            written = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertNotIn("私密用户问题", rendered)
        self.assertNotIn("私密工具输出", rendered)
        self.assertEqual(written["scope"]["saved_request_call_count"], 1)
        self.assertEqual(written["scope"]["exact_token_attribution"], "不可得：当前请求结构导出没有可验证的一对一 Token 使用记录。")
        self.assertTrue(written["recommendations"])

    def test_experiment_plan_is_read_only(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["experiment", "plan", "experiments/token-optimization-v1.json"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["execute"])

    def test_experiment_run_requires_execute_without_starting_process(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["experiment", "run", "experiments/token-optimization-v1.json", "--budget-usd", "1"])
        self.assertEqual(code, 2)

    def test_experiment_run_refuses_unpriced_dollar_budget_before_starting_process(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["experiment", "run", "experiments/token-optimization-v1.json", "--execute", "--budget-usd", "1"])
        self.assertEqual(code, 2)

    def test_compare_emits_quality_regression_from_saved_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            baseline.write_text(json.dumps([{"task_passed": True, "quality_passed": True, "input_tokens": 100, "total_tokens": 100, "wall_seconds": 1}] * 3), encoding="utf-8")
            candidate.write_text(json.dumps([{"task_passed": False, "quality_passed": False, "input_tokens": 10, "total_tokens": 10, "wall_seconds": 1}] * 3), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["compare", str(baseline), str(candidate)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "quality_regression")


if __name__ == "__main__":
    unittest.main()
