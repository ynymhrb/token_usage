"""验证原生定义补估的优先级、作用范围和失败隔离。"""
import json
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent / 'codex-context-optimizer/scripts'))
from content_analysis import collect_content, render_content
from native_tools import supplement_native_tools


class NativeToolTests(unittest.TestCase):
    def write(self, root, name, session, model='model-a', declarations=False, usage=True):
        rows = [{'type': 'session_meta', 'payload': {'id': session}},
                {'type': 'turn_context', 'payload': {'model': model}}]
        if declarations:
            rows.append({'type': 'response_item', 'payload': {'type': 'additional_tools', 'tools': [{'type': 'function', 'name': 'saved'}]}})
        if usage:
            rows.append({'type': 'token_usage_record', 'timestamp': '2026-11-05T01:00:00Z', 'payload': {'usage': {'input_tokens': 123}}})
        path = root / name
        path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
        return path

    def test_saved_definitions_counted_and_no_probe(self):
        contents = defaultdict(list)
        collect_content({'type': 'response_item', 'payload': {'type': 'additional_tools', 'tools': [{'name': 'saved'}]}}, contents)
        self.assertEqual(len(contents['工具定义']), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(Path(directory), 'saved.jsonl', 's', declarations=True)
            query = Mock()
            self.assertEqual(supplement_native_tools([path], ('all', '', None, None, None), contents, query), (0, 0))
            query.assert_not_called()

    def test_same_model_probed_once_and_session_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [self.write(root, name, session) for name, session in [('a', 's1'), ('b', 's2'), ('copy', 's1')]]
            query = Mock(return_value='[{"name":"native"}]')
            contents = defaultdict(list)
            self.assertEqual(supplement_native_tools(paths, ('all', '', None, None, None), contents, query), (2, 0))
            query.assert_called_once_with('model-a')
            self.assertEqual(len(contents['工具定义（原生补估）']), 2)

    def test_time_scope_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write(root, 'a', 's1')
            query = Mock(side_effect=ValueError('不可用'))
            contents = defaultdict(list)
            old_scope = ('time', '', datetime(2026, 11, 3, tzinfo=timezone.utc), datetime(2026, 11, 4, tzinfo=timezone.utc), None)
            self.assertEqual(supplement_native_tools([path], old_scope, contents, query), (0, 0))
            query.assert_not_called()
            self.assertEqual(supplement_native_tools([path], ('all', '', None, None, None), contents, query), (0, 1))
            self.assertFalse(contents)

    def test_duplicate_session_saved_catalog_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [self.write(root, 'a', 's'), self.write(root, 'b', 's', declarations=True)]
            query = Mock()
            supplement_native_tools(paths, ('all', '', None, None, None), defaultdict(list), query)
            query.assert_not_called()

    def test_base_instruction_note_is_short(self):
        report = render_content({'基础指令': ['说明']})
        self.assertIn('基础指令可能与实际发送版本存在一定误差', report)
        self.assertNotIn('不保证与实际模型的分词器一致', report)
