import tempfile
import unittest
from pathlib import Path

from codex_token.adapter import discover_rollouts, parse_rollout


FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "codex"


class CodexAdapterTests(unittest.TestCase):
    def test_parse_rollout_keeps_known_events_when_unknown_and_bad_lines_exist(self):
        trace = parse_rollout(FIXTURES / "rollout-mixed.jsonl", identifier_salt="test")

        self.assertEqual(trace.token_usage.input_tokens, 120)
        self.assertEqual(trace.token_usage.output_tokens, 20)
        self.assertGreaterEqual({gap.code for gap in trace.data_gaps}, {"unknown_event", "invalid_json"})
        self.assertEqual(len(trace.events), 5)

    def test_duplicate_usage_is_not_counted_twice_and_payload_is_not_retained(self):
        trace = parse_rollout(FIXTURES / "rollout-mixed.jsonl", identifier_salt="test")

        self.assertEqual(trace.token_usage.total_tokens, 140)
        self.assertNotIn("private tool input", repr(trace))
        self.assertNotIn("do-not-store", repr(trace))

    def test_missing_timezone_and_unsupported_compression_become_gaps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plain.jsonl").write_text(
                '{"type":"session_meta","timestamp":"2026-10-25T10:00:00","payload":{"id":"x"}}\n',
                encoding="utf-8",
            )
            (root / "old.jsonl.zst").write_bytes(b"not decoded")

            traces = [parse_rollout(path, identifier_salt="test") for path in discover_rollouts(root)]

        codes = {gap.code for trace in traces for gap in trace.data_gaps}
        self.assertIn("missing_timezone", codes)
        self.assertIn("unsupported_compression", codes)


if __name__ == "__main__":
    unittest.main()
