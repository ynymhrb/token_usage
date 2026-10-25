import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from codex_token.cli import main


class CodexCliTests(unittest.TestCase):
    def test_analyze_reads_only_selected_root_and_hides_payload(self):
        fixture = Path(__file__).parent / "tests" / "fixtures" / "codex" / "rollout-valid.jsonl"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.jsonl").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["analyze", "--sessions-root", str(root)])

        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertNotIn("private tool input", rendered)
        self.assertEqual(json.loads(rendered)["facts"]["trace_count"], 1)

    def test_recommend_rejects_raw_jsonl(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["recommend", "tests/fixtures/codex/rollout-valid.jsonl"])
        self.assertNotEqual(code, 0)

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
