import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codex_token.scope import ScopeError, filter_rollout_paths, resolve_scope


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)

    def test_no_expression_uses_current_session(self):
        scope = resolve_scope(None, session_id="thread-123", now=self.now)

        self.assertEqual(scope.kind, "session")
        self.assertEqual(scope.session_id, "thread-123")

    def test_no_expression_without_session_refuses_to_scan_history(self):
        with self.assertRaisesRegex(ScopeError, "当前会话"):
            resolve_scope(None, session_id=None, now=self.now)

    def test_recent_hours_is_time_bounded(self):
        scope = resolve_scope("最近 2 小时", session_id=None, now=self.now)

        self.assertEqual(scope.kind, "time")
        self.assertEqual(scope.start, datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(scope.end, self.now)

    def test_all_history_is_explicit_opt_in(self):
        scope = resolve_scope("全部历史", session_id=None, now=self.now)

        self.assertEqual(scope.kind, "all")

    def test_ambiguous_expression_fails_instead_of_expanding_scope(self):
        with self.assertRaisesRegex(ScopeError, "无法确定"):
            resolve_scope("最近一阵子", session_id="thread-123", now=self.now)

    def test_filter_paths_uses_session_identifier_or_rollout_start_time(self):
        paths = [
            Path("rollout-2026-11-02T17-30-00_thread-123.jsonl"),
            Path("rollout-2026-11-02T19-30-00_thread-456.jsonl"),
        ]
        by_session = filter_rollout_paths(paths, resolve_scope(None, session_id="thread-123", now=self.now))
        by_time = filter_rollout_paths(paths, resolve_scope("最近 2 小时", session_id=None, now=self.now))

        self.assertEqual(by_session, [paths[0]])
        self.assertEqual(by_time, [paths[1]])


if __name__ == "__main__":
    unittest.main()
