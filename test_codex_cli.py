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


if __name__ == "__main__":
    unittest.main()
