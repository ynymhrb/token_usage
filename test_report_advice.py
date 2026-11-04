"""建议必须随输入热点变化，不能给空数据套用固定建议。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'codex-context-optimizer/scripts'))
from report_advice import render_advice


class AdviceTests(unittest.TestCase):
    def test_empty_data_has_no_invented_advice(self):
        self.assertEqual(render_advice({}, {}), '')

    def test_instruction_hotspot_does_not_recommend_tool_output_first(self):
        report = render_advice({'开发者指令': ['规则 ' * 100], '用户消息': ['需求']}, {})
        self.assertIn('1. **开发者指令', report)
        self.assertNotIn('若后续', report)
        self.assertNotIn('工具结果', report)

    def test_tool_hotspot_names_actual_largest_category(self):
        report = render_advice({'工具结果': ['返回内容' * 100]}, {
            'Git（按调用代码推断）': {'tokens': 50, 'calls': 1},
            '文件读取': {'tokens': 100, 'calls': 2}})
        self.assertIn('文件读取与搜索', report)
        self.assertIn('100 Token', report)
