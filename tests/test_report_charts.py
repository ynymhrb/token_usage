"""图表排序、补零与占比总量验证。"""
import sys
import unittest
import re
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).parent.parent / 'plugins/codex-context-optimizer/scripts'))
from report_charts import render_charts, render_stacked


class ChartTests(unittest.TestCase):
    def test_stacked_percent_chart_totals_and_small_groups(self):
        report = '\n'.join(render_stacked([('工具', 60), ('指令', 30), ('消息', 9), ('其他内容', 1)]))
        self.assertIn('block-beta', report)
        self.assertIn('工具 60.0% · 60 Token', report)
        self.assertIn('其他（各项不足 5%） 1.0% · 1 Token', report)
        self.assertEqual(len(re.findall(r'^  s\d+_\d+\[', report, re.MULTILINE)), 20)
        self.assertIn('columns 1', report)
        self.assertIn('没有可绘制', '\n'.join(render_stacked([])))

    def test_compact_chart_sizes_and_bar_cap(self):
        groups = {'时间': {'2026-11-05 01:00': Counter(input_tokens=5000000)},
                  '会话': {'a': Counter(input_tokens=5000000)}}
        report = '\n'.join(render_charts(groups, str))
        self.assertIn('width: 640', report)
        self.assertIn('height: 300', report)
        self.assertIn('max-width: 480px', report)
        self.assertIn('clip-path: inset(0 max(0px, calc((100% - 32px) / 2)))', report)
        self.assertNotIn('960', report)

    def test_adaptive_hour_bins_preserve_totals(self):
        for span in (1, 12, 13, 21, 24, 25, 168, 8761):
            with self.subTest(span=span):
                start = datetime(2026, 11, 4)
                hours = {(start + timedelta(hours=i)).strftime('%Y-%m-%d %H:00'): Counter(input_tokens=i + 1) for i in range(span)}
                report = '\n'.join(render_charts({'时间': hours, '会话': {}}, str))
                bars = re.findall(r'bar \[([^]]+)\]', report)
                self.assertEqual(len(bars), 1)
                values = [int(value) for value in bars[0].split(',')]
                self.assertLessEqual(len(values), 12)
                self.assertEqual(sum(values), span * (span + 1) // 2)
                self.assertIn(f'每 {(span + 11) // 12} 小时', report)
                self.assertIn("plotColorPalette: '#2563eb'", report)

    def test_cross_day_short_labels_and_empty_bins(self):
        groups = {'时间': {'2026-11-04 23:00': Counter(input_tokens=7), '2026-11-05 02:00': Counter(input_tokens=11)}, '会话': {}}
        report = '\n'.join(render_charts(groups, str))
        self.assertIn('bar [7, 0, 0, 11]', report)
        self.assertIn('"04日23时", "05日00时", "05日01时", "05日02时"', report)

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
