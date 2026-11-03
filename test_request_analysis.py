"""验证请求采样口径与指令来源边界。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent / 'codex-context-optimizer/scripts'))
from request_analysis import inspect_requests
from content_analysis import collect_content, render_content


class RequestAnalysisTests(unittest.TestCase):
    def test_samples_repeat_across_calls_but_deduplicate_response_ids(self):
        request = {'instructions': 'base<skills_instructions>catalog</skills_instructions>', 'tools': [{'type': 'function', 'name': 'test'}], 'input': [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hello'}]}
        rows = [{'request_id': key, 'request': request} for key in ['a', 'a', 'b']]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.llm_calls.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            count, contents = inspect_requests([path], ('all', '', None, None, None))
        self.assertEqual(count, 2)
        self.assertEqual(contents['请求顶层指令'], ['base', 'base'])
        self.assertEqual(contents['系统指令'], ['system', 'system'])
        self.assertEqual(len(contents['工具定义']), 2)
        self.assertIn('来自 2 次已保存请求', render_content(contents, count))

    def test_assistant_and_unmarked_user_quotes_are_not_rules(self):
        for role in ['assistant', 'user']:
            contents = defaultdict(list)
            collect_content({'type': 'response_item', 'payload': {'type': 'message', 'role': role, 'content': '<INSTRUCTIONS>quoted</INSTRUCTIONS>'}}, contents)
            self.assertNotIn('AGENTS 与工作区规则', contents)

    def test_base_instruction_blocks_are_split_without_loss(self):
        contents = defaultdict(list)
        body = 'base<skills_instructions>catalog</skills_instructions><skill name="demo">body</skill>'
        collect_content({'type': 'session_meta', 'payload': {'base_instructions': {'text': body}}}, contents)
        self.assertEqual(contents['基础指令'], ['base'])
        self.assertEqual(sum(len(x) for values in contents.values() for x in values), len(body))
        self.assertIn('Skill 正文（显式标记）', contents)
