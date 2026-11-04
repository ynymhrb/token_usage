"""跨日筛选、去重及工具关联的回归验证。"""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent / 'codex-context-optimizer/scripts'))
from record_analysis import inspect_records, classify, render_details
from token_analyzer_runtime import _context


class RecordAnalysisTests(unittest.TestCase):
    def test_tool_evidence_has_its_own_column(self):
        tools = {name: {'calls': 1, 'characters': 10, 'largest': 10} for name in (
            'Git（按调用代码推断）', '动态调用或组合工具（未能细分）', '多类工具混合返回')}
        report = render_details({'时间': {}, '会话': {}}, tools, 0, 0, {})
        self.assertIn('| 类别 | 来源依据 |', report)
        self.assertIn('| Git | 调用代码推断 |', report)
        self.assertIn('| 动态调用或组合工具 | 无法识别内层调用 |', report)
        self.assertNotIn('（按调用代码推断）', report)

    def test_combined_tools_are_not_misreported_as_shell(self):
        self.assertEqual(classify('exec'), '代码执行与组合工具')
        self.assertEqual(classify('functions.exec'), '代码执行与组合工具')
        self.assertEqual(classify('tools.exec_command'), '命令执行与日志')

    def test_request_samples_use_event_time_and_exclude_unknown_time(self):
        scope = ('time', '今天', datetime(2026,11,2,16,tzinfo=timezone.utc), datetime(2026,11,3,16,tzinfo=timezone.utc), None)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'rollout-2026-11-01-old.llm_calls.jsonl'
            rows = [{'timestamp': t, 'request': {'instructions': 'abc'}} for t in ['2026-11-02T15:59:59Z', '2026-11-02T16:00:00Z', '2026-11-03T16:00:00Z', None]]
            path.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
            self.assertEqual(_context([path], scope)[0], 1)
            self.assertEqual(_context([path])[0], 4)

    def test_cross_day_usage_dedup_and_tool_link(self):
        def record(t, payload, kind='token_usage_record'):
            return {'timestamp': t, 'type': kind, 'payload': payload}
        usage = {'thread_id': 'a', 'turn_id': 't', 'response_id': 'r', 'usage': {'input_tokens': 120, 'output_tokens': 10}}
        rows = [record('2026-11-02T12:00:00Z', {**usage, 'response_id': 'old'}), record('2026-11-03T01:00:00Z', usage), record('2026-11-03T01:00:00Z', usage), record('2026-11-02T12:00:00Z', {'type': 'function_call', 'name': 'exec_command', 'call_id': 'c'}, 'response_item'), record('2026-11-03T01:00:00Z', {'type': 'function_call_output', 'call_id': 'c', 'output': 'hello'}, 'response_item')]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'rollout-2026-11-02-old.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
            empty = Path(d) / 'empty.jsonl'
            empty.write_text('{}', encoding='utf-8')
            result = inspect_records([path, empty], ('time', '今天', datetime(2026,11,2,16,tzinfo=timezone.utc), datetime(2026,11,3,16,tzinfo=timezone.utc), None))
        totals, groups, tools, observed, used, _, _ = result
        self.assertEqual(totals['input_tokens'], 120)
        self.assertEqual(groups['回合']['a/t']['calls'], 1)
        self.assertEqual(tools['命令执行与日志']['characters'], 5)
        self.assertEqual((observed, used), (1, 1))
