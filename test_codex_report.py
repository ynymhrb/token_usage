import json
import tempfile
import unittest
from pathlib import Path

from codex_token.adapter import parse_rollout
from codex_token.report import build_report, render_markdown_report, write_json_report


FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "codex" / "rollout-valid.jsonl"


class CodexReportTests(unittest.TestCase):
    def test_report_separates_evidence_classes_and_redacts_payloads(self):
        report = build_report([parse_rollout(FIXTURE, identifier_salt="test")], [])
        rendered = json.dumps(report, ensure_ascii=False)

        self.assertEqual(
            tuple(report),
            ("facts", "derived_metrics", "estimates", "recommendations", "data_gaps"),
        )
        self.assertNotIn("private event payload", rendered)
        self.assertNotIn("private tool input", rendered)

    def test_unpriced_model_and_empty_input_are_explicit_gaps(self):
        report = build_report([parse_rollout(FIXTURE, identifier_salt="test")], [])
        empty = build_report([], [])

        self.assertEqual(report["estimates"], [])
        self.assertIn("unpriced_model", {gap["code"] for gap in report["data_gaps"]})
        self.assertIn("no_traces", {gap["code"] for gap in empty["data_gaps"]})

    def test_report_never_exposes_untrusted_model_or_tool_names(self):
        fixture = FIXTURE.read_text(encoding="utf-8").replace("gpt-test", "model-secret-key").replace("read_file", "tool-secret-key")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rollout.jsonl"
            path.write_text(fixture, encoding="utf-8")
            report = build_report([parse_rollout(path, identifier_salt="test")], [])
        self.assertNotIn("secret-key", json.dumps(report))

    def test_report_does_not_double_count_identical_appended_rollout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            content = FIXTURE.read_text(encoding="utf-8")
            first.write_text(content, encoding="utf-8")
            second.write_text(content, encoding="utf-8")
            report = build_report([
                parse_rollout(first, identifier_salt="test"),
                parse_rollout(second, identifier_salt="test"),
            ], [])
        self.assertEqual(report["derived_metrics"]["known_input_tokens"], 120)
        self.assertEqual(report["facts"]["trace_count"], 1)

    def test_json_and_markdown_writers_keep_only_report_data(self):
        report = build_report([parse_rollout(FIXTURE, identifier_salt="test")], [])
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            write_json_report(target, report)
            content = target.read_text(encoding="utf-8")

        self.assertEqual(json.loads(content), report)
        self.assertIn("# Codex Token 证据报告", render_markdown_report(report))


if __name__ == "__main__":
    unittest.main()
