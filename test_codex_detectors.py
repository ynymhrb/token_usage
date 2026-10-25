import unittest

from codex_token.detectors import detect
from codex_token.models import CanonicalEvent, Confidence, DataGap, RunTrace, TokenUsage
from codex_token.recommendations import recommend


def event(kind, line, *, chars=0, tool=None, usage=None):
    return CanonicalEvent(kind, None, "id", None, None, None, usage, tool, chars, "x.jsonl", line, Confidence.MEDIUM)


def report_with_large_tool_output():
    return {
        "facts": {"findings": [{
            "rule_id": "large_tool_output", "severity": "medium", "summary": "工具输出过大",
            "evidence_lines": [14], "impact": {"payload_chars": 9000}, "confidence": "medium", "data_gaps": [],
        }]},
        "derived_metrics": {}, "estimates": [], "recommendations": [], "data_gaps": [],
    }


class CodexDetectorTests(unittest.TestCase):
    def test_five_diagnostic_rules_are_explainable(self):
        trace = RunTrace(
            trace_id="id",
            events=(
                event("response_item", 10, chars=9000, tool="read_file"),
                event("response_item", 11, chars=9000, tool="read_file"),
                event("token_usage_record", 12, usage=TokenUsage(input_tokens=10)),
                event("token_usage_record", 13, usage=TokenUsage(input_tokens=100)),
                event("event_msg", 14, tool="retry_error"),
            ),
            token_usage=TokenUsage(input_tokens=100),
            data_gaps=(DataGap("unknown_event", "未知", (15,)),),
        )
        findings = detect(trace)

        self.assertEqual(
            {finding.rule_id for finding in findings},
            {"large_tool_output", "repeated_tool", "context_growth", "failed_retry", "attribution_gap"},
        )
        self.assertTrue(all(finding.evidence_lines for finding in findings))

    def test_recommendation_requires_finding_evidence_and_does_not_claim_savings(self):
        recommendation = recommend(report_with_large_tool_output())[0]
        self.assertEqual(recommendation.evidence_lines, (14,))
        self.assertNotIn("节省", recommendation.summary)
        self.assertEqual(recommendation.evidence_kind.value, "recommendation")


if __name__ == "__main__":
    unittest.main()
