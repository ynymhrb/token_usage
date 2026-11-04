"""图表排序、补零与占比总量验证。"""
import sys
import unittest
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).parent / 'codex-context-optimizer/scripts'))
from report_charts import render_charts


class ChartTests(unittest.TestCase):
    def test_hours_chronological_and_missing_hour_zero(self):
        groups = {'时间': {'2026-11-04 03:00': Counter(input_tokens=3), '2026-11-04 01:00': Counter(input_tokens=5)}, '会话': {}}
        report = '\n'.join(render_charts(groups, str))
        self.assertIn('bar [5, 0, 3]', report)
        self.assertIn('x-axis ["01:00", "02:00", "03:00"]', report)

    def test_small_sessions_are_combined_without_losing_tokens(self):
        groups = {'时间': {}, '会话': {str(i): Counter(input_tokens=i) for i in range(1, 11)}}
        report = '\n'.join(render_charts(groups, str))
        self.assertIn('"其余会话（合计）" : 3', report)
        self.assertIn('pie showData', report)
