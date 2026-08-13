import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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


class ContextDistributionTests(unittest.TestCase):
    def test_percentiles_and_fixed_buckets_are_hand_checkable(self):
        calls = [
            profiler.ApiCall("main", str(i), i, cache_read=n)
            for i, n in enumerate([10_000, 100_000, 200_000, 500_000])
        ]

        result = profiler.context_distribution(calls, [100_000, 200_000, 500_000])

        self.assertEqual(
            result["percentiles"],
            {"p50": 150_000, "p75": 275_000, "p90": 410_000, "p95": 455_000},
        )
        self.assertEqual(result["buckets"]["gt_100000"]["calls"], 2)
        self.assertEqual(result["buckets"]["gt_200000"]["calls"], 1)
        self.assertEqual(result["buckets"]["gt_500000"]["calls"], 0)

    def test_measurements_have_no_severity(self):
        result = profiler.diagnosis_metrics([], profiler.collections.Counter(), [])

        self.assertNotIn("severity", result)

    def test_bucket_measurements_include_threshold_and_processed_shares(self):
        calls = [
            profiler.ApiCall("main", "a", 1, cache_read=100_000),
            profiler.ApiCall("main", "b", 2, cache_read=300_000),
        ]

        bucket = profiler.context_distribution(calls, [100_000])["buckets"]["gt_100000"]

        self.assertEqual(bucket, {
            "threshold": 100_000,
            "calls": 1,
            "call_pct": 50.0,
            "processed": 300_000,
            "processed_pct": 75.0,
        })

    def test_custom_observation_threshold_is_printed(self):
        calls = [profiler.ApiCall("main", "a", 1, cache_read=200_000)]
        metrics = profiler.diagnosis_metrics(calls, profiler.collections.Counter(), [])
        metrics["context_distribution"] = profiler.context_distribution(calls, [123_456])
        out = io.StringIO()

        with redirect_stdout(out):
            profiler.print_diagnosis(metrics, "en")

        self.assertIn(">123.5k context", out.getvalue())

    def test_cli_repeatable_thresholds_propagate_to_session_report(self):
        session_id = "test-session"
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "projects" / "project" / f"{session_id}.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(json.dumps({
                "type": "assistant",
                "timestamp": dt(18, 0).isoformat(),
                "message": {
                    "role": "assistant",
                    "usage": {"cache_read_input_tokens": 200_000},
                },
            }) + "\n", encoding="utf-8")
            out = io.StringIO()
            argv = [
                "profiler", "--lang", "en", "--claude-home", temp_dir,
                "--no-subagents", "--context-threshold", "123456",
                "--context-threshold", "234567", session_id,
            ]

            with patch.object(sys, "argv", argv), redirect_stdout(out):
                self.assertEqual(profiler.main(), 0)

        rendered = out.getvalue()
        self.assertIn(">123.5k context", rendered)
        self.assertIn(">234.6k context", rendered)
        self.assertNotIn(">100.0k context", rendered)


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
        self.assertIn("P50/P75/P90/P95", rendered)


    def test_period_summary_uses_fact_only_context_output(self):
        summary = {
            "status": "unknown",
            "start": dt(18, 0),
            "end": dt(23, 0),
            "context_distribution": {
                "percentiles": {"p50": 100_000, "p75": 200_000, "p90": 300_000, "p95": 400_000},
                "max": 500_000,
                "buckets": {},
            },
        }
        out = io.StringIO()

        with redirect_stdout(out):
            profiler.print_period_diagnosis(summary, "zh")

        rendered = out.getvalue()
        self.assertIn("P50/P75/P90/P95", rendered)
        self.assertNotIn(">150k", rendered)
        self.assertNotIn("500k+", rendered)
        self.assertNotIn("Severity", rendered)


class RewriteMetricTests(unittest.TestCase):
    def test_first_large_write_is_initial_population_not_rewrite(self):
        call = profiler.ApiCall(
            actor="main", request_id="1", line=1,
            cache_write=200_000, cache_write_1h=200_000,
        )

        flag = profiler.classify_cache_creation(call, previous_call=None)

        self.assertTrue(profiler.is_large_cache_creation(call))
        self.assertTrue(profiler.is_full_rewrite(call))
        self.assertEqual(flag["kind"], "initial-cache-population")
        self.assertEqual(flag["confidence"], "high")
        self.assertEqual(flag["cache_write_tokens"], 200_000)
        self.assertEqual(flag["ttl"], "1h")

    def test_zero_read_after_similar_cached_context_is_possible_recreation(self):
        previous = profiler.ApiCall(
            actor="main", request_id="1", line=1,
            timestamp=dt(21, 37).isoformat(), cache_read=190_000,
        )
        call = profiler.ApiCall(
            actor="main", request_id="2", line=2,
            timestamp=dt(22, 39).isoformat(), cache_write=200_000,
            cache_write_1h=200_000,
        )

        flag = profiler.classify_cache_creation(
            call, previous_call=previous, idle_seconds=3700,
        )

        self.assertEqual(flag["kind"], "possible-cache-recreation")
        self.assertIn(flag["confidence"], {"medium", "low"})
        self.assertIn("prior-cache-read", flag["evidence"])
        self.assertIn("read-to-write-transition", flag["evidence"])
        self.assertIn("idle-exceeds-declared-ttl", flag["heuristic_flags"])

    def test_timeline_uses_prior_same_actor_evidence_for_recreation(self):
        first = profiler.ApiCall(
            actor="main", request_id="1", line=1,
            timestamp=dt(18, 5).isoformat(), cache_write=200_000,
            cache_write_1h=200_000,
        )
        prior = profiler.ApiCall(
            actor="main", request_id="2", line=2,
            timestamp=dt(21, 37).isoformat(), cache_read=200_000,
        )
        recreation = profiler.ApiCall(
            actor="main", request_id="3", line=3,
            timestamp=dt(22, 39).isoformat(), cache_write=200_000,
            cache_write_1h=200_000,
        )

        flags = profiler.classify_cache_creations([first, prior, recreation])
        record = profiler.call_timeline_record(recreation, flags[id(recreation)])

        self.assertEqual(flags[id(first)]["kind"], "initial-cache-population")
        self.assertEqual(record["cache_creation_classification"]["kind"], "possible-cache-recreation")
        self.assertTrue(record["large_cache_creation"])
        self.assertTrue(record["full_cache_rewrite"])

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
        self.assertEqual(details["likely_cause"], "initial-cache-population")

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
        self.assertTrue(record["large_cache_creation"])
        self.assertEqual(record["cache_creation_classification"]["kind"], "initial-cache-population")
        self.assertTrue(record["full_cache_rewrite"])
        self.assertTrue(record["full_cache_rewrite_deprecated"])
        self.assertEqual(record["measurement"], "exact-jsonl-usage")

    def test_diagnosis_exposes_new_metric_and_deprecated_compatibility_fields(self):
        metrics = profiler.diagnosis_metrics(
            [profiler.ApiCall("main", "1", 1, cache_write=200_000)],
            profiler.collections.Counter(), [],
        )

        self.assertEqual(metrics["large_cache_creation_count"], 1)
        self.assertEqual(metrics["large_cache_creation_tokens"], 200_000)
        self.assertTrue(metrics["full_rewrite_metrics_deprecated"])
        self.assertEqual(metrics["full_rewrite_count"], 1)


class ConversationTurnTests(unittest.TestCase):
    def test_user_title_uses_text_only_normalizes_space_and_limits_to_100_chars(self):
        row = {
            "type": "user",
            "timestamp": dt(18, 0).isoformat(),
            "message": {"role": "user", "content": [
                {"type": "text", "text": "  请分析\n\n这段 " + "很长" * 60},
                {"type": "tool_result", "content": "不要作为标题"},
            ]},
        }

        title = profiler.user_turn_title(row)

        self.assertEqual(len(title), 100)
        self.assertTrue(title.startswith("请分析 这段 很长"))
        self.assertTrue(title.endswith("…"))
        self.assertNotIn("不要作为标题", title)

    def test_user_title_extracts_embedded_co_creator_input_from_dispatch_context(self):
        row = {
            "type": "user",
            "timestamp": dt(18, 0).isoformat(),
            "message": {"role": "user", "content": (
                "## Dispatch Mission Context\nmission: 选题\n历史内容\n"
                "[0001786631788171-000074] [14:36 UTC co-creator] "
                "@架构喵 你们再深入讨论5轮，看能否发现亮点\n[/对话历史]"
            )},
        }

        self.assertEqual(
            profiler.user_turn_title(row),
            "@架构喵 你们再深入讨论5轮，看能否发现亮点",
        )

    def test_turns_group_main_and_subagent_calls_under_latest_main_user_input(self):
        rows = [
            {"type": "user", "timestamp": dt(18, 0).isoformat(), "message": {"role": "user", "content": "第一问"}},
            {"type": "user", "timestamp": dt(18, 30).isoformat(), "message": {"role": "user", "content": "第二问"}},
            {"type": "user", "timestamp": dt(18, 31).isoformat(), "sourceToolAssistantUUID": "x", "message": {"role": "user", "content": [{"type": "tool_result", "content": "工具结果"}]}},
        ]
        calls = [
            profiler.ApiCall(actor="session:main", request_id="m1", line=1, timestamp=dt(18, 5).isoformat(), cache_read=100),
            profiler.ApiCall(actor="session:main/agent:a", request_id="a1", line=2, timestamp=dt(18, 10).isoformat(), cache_read=200),
            profiler.ApiCall(actor="session:main", request_id="m2", line=3, timestamp=dt(18, 35).isoformat(), cache_write=300, output_tokens=20),
        ]

        turns = profiler.aggregate_conversation_turns(rows, calls, dt(18, 0), dt(19, 0))

        self.assertEqual([t["title"] for t in turns], ["第一问", "第二问"])
        self.assertEqual(turns[0]["calls"], 2)
        self.assertEqual(turns[0]["processed"], 300)
        self.assertEqual(turns[1]["calls"], 1)
        self.assertEqual(turns[1]["cache_write"], 300)

    def test_call_before_first_visible_user_input_uses_resume_bucket(self):
        calls = [
            profiler.ApiCall(actor="session:main", request_id="m1", line=1, timestamp=dt(18, 5).isoformat(), cache_read=100),
        ]

        turns = profiler.aggregate_conversation_turns([], calls, dt(18, 0), dt(19, 0))

        self.assertEqual(turns[0]["title"], "会话恢复/后台继续")
        self.assertEqual(turns[0]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
