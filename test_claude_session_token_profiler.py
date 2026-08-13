import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import claude_session_token_profiler as profiler


TZ8 = timezone(timedelta(hours=8))


def dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 13, hour, minute, second, tzinfo=TZ8)


class LimitEventTests(unittest.TestCase):
    def test_repeated_429s_for_same_reset_are_one_exhaustion_incident(self):
        events = [
            {
                "time": dt(22, 40, 36),
                "kind": "five_hour_limit",
                "reset": dt(23, 0),
                "session_id": "b4017db4-full",
            },
            {
                "time": dt(22, 42, 2),
                "kind": "five_hour_limit",
                "reset": dt(23, 0),
                "session_id": "b4017db4-full",
            },
        ]

        incidents = profiler.collapse_limit_events(events)

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["time"], dt(22, 40, 36))
        self.assertEqual(incidents[0]["last_time"], dt(22, 42, 2))
        self.assertEqual(incidents[0]["attempts"], 2)


class PeriodDiagnosisTests(unittest.TestCase):
    def test_summary_identifies_responsible_session_and_rewrite_proximity(self):
        start, end = dt(18, 0), dt(23, 0)
        limit_at = dt(22, 40, 36)
        rewrite = profiler.ApiCall(
            actor="session:b4017db4",
            request_id="rewrite",
            line=1,
            timestamp=dt(22, 39, 44).isoformat(),
            model="claude-opus-4-8",
            cache_write=571_300,
        )
        other = profiler.ApiCall(
            actor="session:other",
            request_id="other",
            line=2,
            timestamp=dt(19, 0).isoformat(),
            model="deepseek-v4-pro",
            cache_read=100_000,
        )
        sessions = [
            {
                "session_id": "b4017db4-full",
                "processed": 571_300,
                "thinking": 71_000,
            },
            {"session_id": "other-full", "processed": 100_000, "thinking": 19_400},
        ]
        metrics = profiler.diagnosis_metrics(
            [rewrite, other],
            profiler.collections.Counter({"assistant:thinking": 90_400}),
            [{"kind": "five_hour_limit"}],
        )

        summary = profiler.build_period_diagnosis(
            start=start,
            end=end,
            status="exhausted",
            limit_at=limit_at,
            sessions=session_rows if (session_rows := sessions) else [],
            calls=[rewrite, other],
            metrics=metrics,
        )

        self.assertEqual(summary["responsible_session_id"], "b4017db4-full")
        self.assertAlmostEqual(summary["responsible_processed_pct"], 85.1, places=1)
        self.assertEqual(summary["latest_rewrite_seconds_before_limit"], 52)
        self.assertEqual(summary["limit_elapsed_seconds"], 16_836)

    def test_chinese_summary_is_printed_as_the_first_report_section(self):
        summary = {
            "status": "exhausted",
            "start": dt(18, 0),
            "end": dt(23, 0),
            "limit_at": dt(22, 40, 36),
            "limit_elapsed_seconds": 16_836,
            "responsible_session_id": "b4017db4-full",
            "responsible_processed_pct": 85.8,
            "peak_context": 571_300,
            "long_context_call_pct": 50.0,
            "long_context_processed_pct": 85.8,
            "full_rewrite_count": 2,
            "full_rewrite_tokens": 1_039_000,
            "full_rewrite_cache_write_pct": 92.0,
            "latest_rewrite_at": dt(22, 39, 44),
            "latest_rewrite_context": 571_300,
            "latest_rewrite_seconds_before_limit": 52,
            "thinking_estimate": 90_400,
            "responsible_thinking": 71_000,
        }
        out = io.StringIO()

        with redirect_stdout(out):
            profiler.print_period_diagnosis(summary, "zh")

        rendered = out.getvalue()
        self.assertTrue(rendered.startswith("[周期诊断]"))
        self.assertIn("结果：5小时额度已耗尽", rendered)
        self.assertIn("周期：18:00 → 23:00", rendered)
        self.assertIn("耗尽耗时：4h40m36s", rendered)
        self.assertIn("距额度耗尽仅52秒", rendered)
        self.assertIn("优先 compact/clear b4017db4", rendered)


class RewriteMetricTests(unittest.TestCase):
    def test_rewrite_details_include_ratio_and_cache_ttl(self):
        call = profiler.ApiCall(
            actor="main",
            request_id="r1",
            line=1,
            cache_write=140_000,
            cache_write_1h=140_000,
            cache_read=5_000,
            input_tokens=5_000,
        )

        details = profiler.cache_rewrite_details(call)

        self.assertTrue(details["is_full_rewrite"])
        self.assertAlmostEqual(details["rewrite_ratio"], 140_000 / 150_000)
        self.assertEqual(details["ttl"], "1h")
        self.assertEqual(details["likely_cause"], "1h-cache-miss-or-expiry")

    def test_call_timeline_record_exposes_exact_usage_and_rewrite_evidence(self):
        call = profiler.ApiCall(
            actor="session:b4017db4/agent:x",
            request_id="r2",
            line=7,
            timestamp=dt(22, 39, 44).isoformat(),
            model="claude-opus-4-8",
            input_tokens=2,
            cache_read=0,
            cache_write=571_300,
            output_tokens=11_800,
            cache_write_1h=571_300,
        )

        record = profiler.call_timeline_record(call)

        self.assertEqual(record["session_id"], "b4017db4")
        self.assertEqual(record["context_input_tokens"], 571_302)
        self.assertEqual(record["cache_ttl"], "1h")
        self.assertTrue(record["full_cache_rewrite"])
        self.assertEqual(record["measurement"], "exact-jsonl-usage")


if __name__ == "__main__":
    unittest.main()
