"""分类、同名会话和离线降级的回归验证。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'plugins/codex-context-optimizer/scripts'))
from content_analysis import collect_content, infer_tool, load_titles, render_content
from record_analysis import classify, render_details


class ContentAnalysisTests(unittest.TestCase):
    def test_inner_tools_and_mixed_results(self):
        examples = [
            ('text(await tools.exec_command({cmd: "Get-Content a.py"}))', '文件读取与搜索'),
            ('text(await tools.exec_command({cmd: "python -m unittest"}))', '测试与构建日志'),
            ('text(await tools.exec_command({cmd: "git diff"}))', 'Git'),
            ('await tools.web__run({}); await tools.apply_patch("x")', '多类工具混合返回'),
            ('text(await tools[x.name]({}))', '动态调用或组合工具'),
        ]
        for source, expected in examples:
            with self.subTest(source=source):
                self.assertIn(expected, infer_tool({'name': 'exec', 'input': source}, classify))

    def test_explicit_blocks_are_not_counted_twice(self):
        counts = defaultdict(list)
        body = 'abc<skills_instructions>技能</skills_instructions><INSTRUCTIONS>规则</INSTRUCTIONS>xyz'
        collect_content({'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer', 'content': [{'text': body}]}}, counts)
        self.assertEqual(counts['开发者指令'], ['abcxyz'])
        self.assertEqual(sum(len(value) for values in counts.values() for value in values), len(body))
        with patch.dict('os.environ', {'TOKEN_ANALYZER_TOKENIZER': 'Z:/missing-tokenizer'}):
            report = render_content(counts)
            self.assertIn('字符粗估', report)
            self.assertIn('每条计一次', report)

    def test_readonly_database_titles_and_index_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            connection = sqlite3.connect(root / 'state_5.sqlite')
            connection.execute('CREATE TABLE threads (id TEXT, title TEXT, name TEXT)')
            connection.execute("INSERT INTO threads VALUES ('a', '首条消息', '数据库标题')")
            connection.commit()
            connection.close()
            (root / 'session_index.jsonl').write_text('\n'.join(json.dumps(r) for r in [{'id': 'a', 'thread_name': '旧标题'}, {'id': 'b', 'thread_name': '索引标题'}]), encoding='utf-8')
            self.assertEqual(load_titles(root), {'a': '数据库标题', 'b': '索引标题'})

    def test_same_titles_do_not_merge_sessions_or_expose_ids(self):
        groups = {'时间': {}, '会话': {'secret-a': Counter(input_tokens=10), 'secret-b': Counter(input_tokens=20)}, '回合': {'secret-a/turn-secret': Counter(input_tokens=10)}}
        report = render_details(groups, {}, 0, 0, {'secret-a': '同名', 'secret-b': '同名'})
        self.assertIn('"同名" : 20', report)
        self.assertIn('"同名（同名 2）" : 10', report)
        self.assertNotIn('secret', report)
        self.assertNotIn('按回合', report)
