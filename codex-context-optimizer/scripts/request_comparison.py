"""只对可观察请求累计内容，并与可关联的实际输入用量比较。"""
import hashlib
import json
from collections import Counter, defaultdict

from content_analysis import token_estimator
from request_analysis import inspect_requests


def compare_requests(exports, rollouts, scope, estimate=None):
    observations = []
    inspect_requests(exports, scope, observations)
    if not observations:
        return Counter(), {}, {}
    if estimate is None:
        estimate, _ = token_estimator()
    usage = {}
    ambiguous = set()
    for path in rollouts:
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get('type') != 'token_usage_record':
                continue
            payload = row.get('payload', {})
            if not isinstance(payload, dict):
                continue
            key = payload.get('response_id')
            values = payload.get('usage', payload)
            value = values.get('input_tokens') if isinstance(values, dict) else None
            if not key or not isinstance(value, int) or isinstance(value, bool) or value < 0:
                continue
            if key in usage and usage[key] != value:
                ambiguous.add(key)
            usage[key] = value
    totals = Counter(requests=len(observations))
    tools = {}
    sessions = defaultdict(Counter)
    matched_ids = set()
    for observation in observations:
        session = observation['session']
        value = sum(estimate(body) for bodies in observation['contents'].values() for body in bodies)
        totals['estimated'] += value
        sessions[session]['estimated'] += value
        sessions[session]['requests'] += 1
        response_id = observation['response_id']
        if response_id in usage and response_id not in ambiguous and response_id not in matched_ids:
            matched_ids.add(response_id)
            totals['matched_requests'] += 1
            totals['matched_estimated'] += value
            totals['actual'] += usage[response_id]
        seen_in_request = set()
        for item in observation['tools']:
            output = item.get('output', '')
            body = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            # 无调用标识不能断言相同正文属于同一条结果。
            call_id = item.get('call_id')
            if not call_id:
                totals['unidentified_tools'] += 1
                continue
            key = (session, call_id, hashlib.sha256(body.encode('utf-8')).hexdigest())
            entry = tools.setdefault(key, {'session': session, 'tokens': estimate(body), 'occurrences': 0, 'requests': 0})
            entry['occurrences'] += 1
            if key not in seen_in_request:
                entry['requests'] += 1
                seen_in_request.add(key)
    return totals, sessions, tools


def render_comparison(exports, rollouts, scope, titles=None):
    totals, sessions, tools = compare_requests(exports, rollouts, scope)
    titles = titles or {}
    names = {session: next((title for key, title in titles.items() if key == session or key in session), f'未命名会话 {index}') for index, session in enumerate(sessions, 1)}
    lines = ['## 累计上下文与实际输入对照', '']
    if not totals['requests']:
        return '\n'.join(lines + ['未保存逐次请求，暂不能统计工具结果的重复携带次数和累计上下文估算；上方合计仅为保存正文的大小。', ''])
    lines.extend([f"已保存请求 {totals['requests']:,} 次，正文累计估算 {totals['estimated']:,} Token。各会话分别累计后汇总。", ''])
    if totals['matched_requests']:
        estimated, actual = totals['matched_estimated'], totals['actual']
        ratio = f'{estimated / actual:.1%}' if actual else '—'
        lines.extend([f"以下仅比较已关联实际用量的 {totals['matched_requests']:,} 次请求：", '',
                      '| 累计正文估算 Token | 实际输入 Token（含缓存） | 估算减实际 | 估算 / 实际 |',
                      '|---:|---:|---:|---:|', f'| {estimated:,} | {actual:,} | {estimated - actual:+,} | {ratio} |', ''])
    else:
        lines.extend(['请求样本未关联到实际用量，暂不计算差值。', ''])
    if tools:
        lines.extend(['### 工具结果重复携带', '', '按累计估算量展示前 10 项；重复次数不含首次出现，仅统计已保存请求。', '',
                      '| 会话 / 工具结果 | 单次估算 Token | 出现请求数 | 重复出现次数 | 累计估算 Token |', '|---|---:|---:|---:|---:|'])
        ordered = sorted(tools.values(), key=lambda row: -row['tokens'] * row['occurrences'])
        for index, row in enumerate(ordered[:10], 1):
            name = ' '.join(str(names[row['session']]).split()).replace('|', '｜')[:60]
            lines.append(f"| {name} / 结果 {index} | {row['tokens']:,} | {row['requests']:,} | {row['occurrences'] - 1:,} | {row['tokens'] * row['occurrences']:,} |")
        lines.extend(['', f"已关联工具结果累计估算：{sum(row['tokens'] * row['occurrences'] for row in ordered):,} Token。", ''])
    if totals['unidentified_tools']:
        lines.append(f"另有 {totals['unidentified_tools']} 条工具结果缺少调用标识，计入正文总量，但无法关联重复次数。")
    return '\n'.join(lines)
