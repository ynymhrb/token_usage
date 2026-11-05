"""官方账户查询的协议、空值、范围和失败隔离验证。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'plugins/codex-context-optimizer/scripts'))
from account_usage import query_account, render_account


class AccountUsageTests(unittest.TestCase):
    def test_protocol_reads_only_and_preserves_partial_success(self):
        script = '''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 m=r['method']
 assert m in ['initialize','initialized','account/rateLimits/read','account/usage/read']
 if m=='initialized': continue
 if m=='initialize': value={'result':{}}
 elif m=='account/rateLimits/read': value={'result':{'rateLimits':{'primary':{'usedPercent':0,'windowDurationMins':300,'resetsAt':0}}}}
 else:
  assert r['params']=={'threadId':'session-a'}
  value={'error':{'message':'secret'}}
 print(json.dumps({'id':r['id'],**value}),flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.py'
            path.write_text(script, encoding='utf-8')
            result = query_account('session-a', command=[sys.executable, str(path)], timeout=3)
        text = render_account(result)
        self.assertIn('已用 0%', text)
        self.assertIn('查询未成功，无法确定，不代表零', text)
        self.assertNotIn('secret', text)
        self.assertIn('usage_error', result)

    def test_null_is_not_zero_and_time_scope_is_not_session_cost(self):
        base = {'queried_at':'2026-11-04 00:00:00', 'session': True, 'usage': {'threadUsage': {'estimatedUsageCreditsMicros': 0, 'estimatedUsageUsdMicros': None}}}
        text = render_account(base)
        self.assertIn('累计估算积分：0.000000', text)
        self.assertIn('累计估算金额：未提供', text)
        base['session'] = False
        self.assertNotIn('累计估算积分', render_account(base))
        self.assertIn('非所选时间范围', render_account(base))

    def test_timeout_and_missing_executable_do_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sleep.py'
            path.write_text('import time\ntime.sleep(20)', encoding='utf-8')
            result = query_account(command=[sys.executable, str(path)], timeout=0.2)
            self.assertIn('超时', result['error'])
            result = query_account(command=[str(Path(directory) / 'missing.exe')])
            self.assertIn('error', result)
