"""以可手算历史验证重放边界、重复次数、压缩与截断。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'plugins/codex-context-optimizer/scripts'))
from history_replay import replay_history, truncate_output, render_replay, normalize_input_categories, INPUT_CATEGORIES

ALL = ('all', '', None, None, None)


def item(kind, **fields):
    return {'type': 'response_item', 'payload': {'type': kind, **fields}}


def message(role, text):
    return item('message', role=role, content=[{'type': 'input_text', 'text': text}])


def usage(response, stamp='2026-11-05T01:00:00Z', session='s'):
    return {'type': 'token_usage_record', 'timestamp': stamp,
            'payload': {'thread_id': session, 'response_id': response, 'usage': {'input_tokens': 100}}}


def history():
    return [{'type': 'session_meta', 'payload': {'id': 's'}}, message('user', 'hi'),
            item('function_call', name='read_file', call_id='c', arguments='{}'), usage('r1'),
            item('function_call_output', call_id='c', output='abcd'),
            message('assistant', 'one'), usage('r2'),
            message('assistant', 'two'), usage('r3')]


class ReplayTests(unittest.TestCase):
    def test_recorded_base_instructions_replace_session_metadata_base(self):
        rows = history()
        rows[0]['payload']['base_instructions'] = {'text': 'BASE'}
        tagged = message('developer', 'BASE')
        tagged['payload']['internal_chat_message_metadata_passthrough'] = {
            'content_item_kinds': ['model.base_instructions']}
        rows.insert(1, tagged)
        totals, counts, _, _ = self.run_rows(rows)
        self.assertEqual(totals['requests'], 3)
        self.assertEqual(counts['基础指令'], 12)
        self.assertEqual(counts.get('开发者指令', 0), 0)

    def test_untagged_identical_developer_message_is_not_discarded(self):
        rows = history()
        rows[0]['payload']['base_instructions'] = {'text': 'BASE'}
        rows.insert(1, message('developer', 'BASE'))
        _, counts, _, _ = self.run_rows(rows)
        self.assertEqual(counts['基础指令'], 12)
        self.assertEqual(counts['开发者指令'], 12)

    def test_recorded_base_survives_compaction_without_reintroducing_metadata_base(self):
        rows = history()
        rows[0]['payload']['base_instructions'] = {'text': 'BASE'}
        tagged = message('developer', 'BASE')['payload']
        tagged['internal_chat_message_metadata_passthrough'] = {'content_item_kinds': ['model.base_instructions']}
        rows.insert(1, {'type': 'compacted', 'payload': {'replacement_history': [tagged]}})
        _, counts, _, _ = self.run_rows(rows)
        self.assertEqual(counts['基础指令'], 12)
        self.assertEqual(counts.get('开发者指令', 0), 0)

    def test_input_categories_use_stable_names_and_preserve_totals(self):
        result = normalize_input_categories({'工具定义': 10, '工具定义（原生补估）': 20,
            'Skill 正文（显式标记）': 3, 'Skill 说明': 5, '未认识的内容': 7})
        self.assertEqual(result['工具定义'], 30)
        self.assertEqual(result['Skill 说明'], 8)
        self.assertEqual(result['其他内容'], 7)
        self.assertEqual(sum(result.values()), 45)
        self.assertTrue(set(result).issubset(set(INPUT_CATEGORIES)))

    def test_report_classifies_cumulative_input_without_per_result_ranking(self):
        result = self.run_rows(history())
        totals, counts, _, tools = result
        self.assertEqual(counts['工具结果'], 8)
        self.assertEqual(sum(row['tokens'] * row['occurrences'] for row in tools.values()), 8)
        report = render_replay([], ALL, replayed=result)
        self.assertIn('## 输入内容分类', report)
        self.assertIn('工具结果：8 Token', report)
        self.assertIn('官方累计输入 300 Token', report)
        self.assertIn('未归因与估算差额', report)
        overestimated = render_replay([], ALL, replayed=result, official_input=1)
        self.assertIn('分类估算合计超过官方记录', overestimated)
        self.assertIn('官方累计输入 1 Token', overestimated)
        self.assertNotIn('工具结果重复携带排行', report)
        self.assertNotIn('原始内容分类', report)

    def test_multiple_sessions_do_not_share_tool_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for session in ['a', 'b']:
                rows = history()
                rows[0]['payload']['id'] = session
                for r in rows:
                    if r['type'] == 'token_usage_record':
                        r['payload']['thread_id'] = session
                p = Path(directory) / f'{session}.jsonl'
                p.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
                paths.append(p)
            totals, _, sessions, tools = replay_history(paths, ALL, estimate=len)
        self.assertEqual(totals['requests'], 6)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sorted(r['requests'] for r in tools.values()), [2, 2])

    def test_earlier_fragment_supplies_history_for_selected_fragment(self):
        rows = history()
        rows[3]['timestamp'] = '2026-11-04T23:00:00Z'
        scope = ('time', '', datetime(2026,11,5,tzinfo=timezone.utc), datetime(2026,11,6,tzinfo=timezone.utc), None)
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i, part in enumerate([rows[:5], [rows[0]] + rows[5:]]):
                p = Path(directory) / f'{i}.jsonl'
                p.write_text('\n'.join(map(json.dumps, part)), encoding='utf-8')
                paths.append(p)
            totals, counts, _, tools = replay_history(paths, scope, estimate=len)
        self.assertEqual(totals['requests'], 2)
        self.assertEqual(counts['工具结果'], 8)
        self.assertEqual(next(iter(tools.values()))['requests'], 2)

    def test_parallel_results_are_carried_once_each(self):
        rows = history()[:3] + [item('function_call', name='read_file', call_id='d', arguments='{}'), usage('r1'),
            item('function_call_output', call_id='c', output='abc'), item('function_call_output', call_id='d', output='de'),
            message('assistant','done'), usage('r2')]
        totals, counts, _, tools = self.run_rows(rows)
        self.assertEqual(totals['requests'], 2)
        self.assertEqual(counts['工具结果'], 5)
        self.assertEqual(sorted(r['requests'] for r in tools.values()), [1, 1])

    def test_missing_previous_usage_does_not_leak_response_boundary(self):
        rows = history()
        del rows[3]
        totals, counts, _, _ = self.run_rows(rows)
        self.assertEqual(totals['requests'], 2)
        self.assertEqual(counts['工具结果'], 8)

    def test_saved_tools_take_precedence_over_native_probe(self):
        rows = history()
        rows[1:1] = [{'type':'turn_context','payload':{'model':'test'}},
                     item('additional_tools', tools=[{'name':'test_tool'}])]
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 's.jsonl'
            p.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            def forbidden(model):
                raise AssertionError('已有声明不应查询原生工具')
            totals, counts, _, _ = replay_history([p], ALL, estimate=len, native_query=forbidden)
        self.assertEqual(totals['requests'], 3)
        self.assertGreater(counts['工具定义'], 0)

    def run_rows(self, rows, scope=ALL, copies=1):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i in range(copies):
                path = Path(directory) / f'{i}.jsonl'
                path.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
                paths.append(path)
            return replay_history(paths, scope, estimate=len)

    def test_response_output_is_only_in_later_requests(self):
        totals, categories, _, tools = self.run_rows(history())
        self.assertEqual(totals['requests'], 3)
        self.assertEqual(categories['用户消息'], 6)
        self.assertEqual(categories['工具参数与调用代码'], 4)
        self.assertEqual(categories['工具结果'], 8)
        self.assertEqual(categories['助手消息'], 3)
        self.assertEqual(totals['estimated'], 21)
        self.assertEqual(totals['actual'], 300)
        self.assertEqual(next(iter(tools.values()))['requests'], 2)

    def test_time_scope_keeps_earlier_history_and_deduplicates_files(self):
        rows = history()
        rows[3]['timestamp'] = '2026-11-04T23:00:00Z'
        rows[6]['timestamp'] = '2026-11-04T23:01:00Z'
        scope = ('time', '', datetime(2026,11,5,tzinfo=timezone.utc), datetime(2026,11,6,tzinfo=timezone.utc), None)
        totals, categories, _, tools = self.run_rows(rows, scope, copies=2)
        self.assertEqual(totals['requests'], 1)
        self.assertEqual(categories['工具结果'], 4)
        self.assertEqual(categories['助手消息'], 3)
        self.assertEqual(next(iter(tools.values()))['requests'], 1)

    def test_compaction_replaces_old_tool_history(self):
        rows = history()[:7] + [{'type': 'compacted', 'payload': {'replacement_history': [message('user','summary')['payload']] }},
            message('assistant','after'), usage('r3')]
        totals, categories, _, tools = self.run_rows(rows)
        self.assertEqual(totals['requests'], 3)
        self.assertEqual(categories['工具结果'], 4)
        self.assertEqual(next(iter(tools.values()))['requests'], 1)

    def test_missing_checkpoint_blocks_until_valid_checkpoint(self):
        rows = history()[:4] + [{'type': 'compacted', 'payload': {'message': 'summary'}},
            message('assistant', 'unknown'), usage('r2'),
            {'type': 'compacted', 'payload': {'replacement_history': []}}, message('assistant', 'known'), usage('r3')]
        totals, _, _, _ = self.run_rows(rows)
        self.assertEqual(totals['requests'], 2)
        self.assertEqual(totals['skipped_requests'], 1)

    def test_no_response_boundary_does_not_guess_input(self):
        totals, _, _, _ = self.run_rows([{'type':'session_meta','payload':{'id':'s'}}, usage('r1')])
        self.assertEqual(totals['requests'], 0)
        self.assertEqual(totals['skipped_requests'], 1)

    def test_compaction_model_usage_is_not_a_normal_request(self):
        rows = history()[:4] + [message('assistant','summary'), usage('compact'),
            {'type':'compacted','payload':{'compaction_response_id':'compact','replacement_history':[]}}]
        self.assertEqual(self.run_rows(rows)[0]['requests'], 1)

    def test_rollback_removes_last_turn(self):
        rows = [{'type':'session_meta','payload':{'id':'s'}},
            {'type':'event_msg','payload':{'type':'task_started'}}, message('user','keep'), message('assistant','ok'), usage('r1'),
            {'type':'event_msg','timestamp':'later','payload':{'type':'task_started'}}, message('user','remove'), message('assistant','gone'), usage('r2'),
            {'type':'event_msg','payload':{'type':'thread_rolled_back','num_turns':1}}, message('assistant','new'), usage('r3')]
        totals, categories, _, _ = self.run_rows(rows)
        self.assertEqual(totals['requests'], 3)
        self.assertEqual(categories['用户消息'], 4 + 10 + 4)
        self.assertEqual(categories['助手消息'], 0 + 2 + 2)

    def test_native_utf8_truncation_and_override(self):
        self.assertEqual(truncate_output('abcdefghij', {'fallback_token_limit_override':1}, {}), 'ab…2 tokens truncated…ij')
        self.assertEqual(truncate_output('你好世界', {'fallback_token_limit_override':1}, {}), '…2 tokens truncated…')
        self.assertEqual(truncate_output('abcdef', {}, {'mode':'bytes','limit':2}), 'a…4 chars truncated…f')

    def test_media_encoding_is_not_counted_as_input_text(self):
        rows = history()
        rows[4]['payload']['output'] = [{'type':'input_text','text':'abc'}, {'type':'input_image','image_url':'data:image/png;base64,'+'A'*10000}]
        _, counts, _, _ = self.run_rows(rows)
        self.assertEqual(counts['工具结果'], 6)

    def test_checkpoint_preserves_tool_identity_when_item_id_changes(self):
        rows = history()[:7]
        replacement = [rows[2]['payload'], {**rows[4]['payload'], 'id':'restored'}]
        rows += [{'type':'compacted','payload':{'replacement_history':replacement}}, message('assistant','after'),usage('r3')]
        _, counts, _, tools = self.run_rows(rows)
        self.assertEqual(len(tools), 1)
        self.assertEqual(next(iter(tools.values()))['requests'], 2)
        self.assertEqual(counts['工具结果'], 8)

    def test_failed_native_probe_is_not_retried_per_request(self):
        rows = history()
        rows.insert(1, {'type':'turn_context','payload':{'model':'test'}})
        calls = []
        def fail(model):
            calls.append(model)
            raise ValueError('不可用')
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'s.jsonl'
            p.write_text('\n'.join(map(json.dumps,rows)),encoding='utf-8')
            totals, _, _, _ = replay_history([p], ALL, estimate=len, native_query=fail)
        self.assertEqual(totals['requests'], 3)
        self.assertEqual(calls, ['test'])

    def test_truncated_tool_body_drives_cumulative_tokens(self):
        rows = history()
        rows[4]['metadata'] = {'fallback_token_limit_override': 1}
        rows[4]['payload']['output'] = 'abcdefghij'
        _, categories, _, tools = self.run_rows(rows)
        n = len('ab…2 tokens truncated…ij')
        self.assertEqual(categories['工具结果'], n * 2)
        self.assertEqual(next(iter(tools.values()))['tokens'], n)
