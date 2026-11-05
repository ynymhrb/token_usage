"""验证各会话独立计数、重复携带与同批请求比较。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'plugins/codex-context-optimizer/scripts'))
from request_comparison import compare_requests, render_comparison


class ComparisonTests(unittest.TestCase):
    def test_sessions_repeat_independently_and_compare_only_matched_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / 'requests.jsonl'
            tool = {'type': 'function_call_output', 'call_id': 'same-id', 'output': 'abcd'}
            rows = [{'thread_id': session, 'response_id': response, 'request': {'input': [tool]}}
                    for session, response in [('a', 'r1'), ('a', 'r2'), ('b', 'r3'), ('a', 'r2')]]
            exports.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            history = root / 'history.jsonl'
            history.write_text('\n'.join(json.dumps({'type': 'token_usage_record', 'payload': {'response_id': response, 'usage': {'input_tokens': tokens}}}) for response, tokens in [('r1', 10), ('r2', 20), ('outside', 999)]), encoding='utf-8')
            totals, sessions, tools = compare_requests([exports], [history], ('all', '', None, None, None), len)
            self.assertEqual(totals['requests'], 3)
            self.assertEqual(totals['estimated'], 12)
            self.assertEqual(totals['matched_estimated'], 8)
            self.assertEqual(totals['actual'], 30)
            self.assertEqual(sessions['a']['estimated'], 8)
            self.assertEqual(sessions['b']['estimated'], 4)
            self.assertEqual(sorted(row['occurrences'] for row in tools.values()), [1, 2])

    def test_no_requests_does_not_invent_repetitions(self):
        report = render_comparison([], [], ('all', '', None, None, None))
        self.assertIn('暂不能统计', report)
        self.assertNotIn('0 Token', report)
