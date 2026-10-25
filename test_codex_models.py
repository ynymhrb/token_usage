import unittest

from codex_token.models import (
    CanonicalEvent,
    Confidence,
    DataGap,
    EvidenceKind,
    RunTrace,
    TokenUsage,
)
from codex_token.privacy import sanitize_identifier


class CodexModelTests(unittest.TestCase):
    def test_sanitize_identifier_is_stable_and_does_not_reveal_source_value(self):
        masked = sanitize_identifier("session-secret", "test-salt")
        self.assertEqual(masked, sanitize_identifier("session-secret", "test-salt"))
        self.assertNotIn("session-secret", masked)

    def test_models_keep_missing_usage_unknown_and_classify_evidence(self):
        usage = TokenUsage(input_tokens=None, output_tokens=5)
        event = CanonicalEvent(
            event_kind="token_usage_record",
            timestamp=None,
            session_id="hashed-session",
            turn_id=None,
            cwd=None,
            model=None,
            usage=usage,
            tool_name=None,
            payload_chars=0,
            source_path="fixture.jsonl",
            source_line=3,
            confidence=Confidence.LOW,
        )
        trace = RunTrace(
            trace_id="hashed-session",
            events=(event,),
            token_usage=usage,
            data_gaps=(DataGap("missing_input_usage", "输入 Token 不可得", (3,)),),
        )

        self.assertIsNone(trace.token_usage.input_tokens)
        self.assertEqual(EvidenceKind.FACT.value, "fact")
        self.assertEqual(trace.events[0].confidence, Confidence.LOW)


if __name__ == "__main__":
    unittest.main()
