"""按已保存事件重放历史输入；结果是正文估算，不是完整请求副本。"""
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from content_analysis import collect_content, infer_tool, token_estimator, output_text
from native_tools import query_native_tools
from record_analysis import classify
from report_charts import render_stacked


def in_scope(row, scope):
    if scope[0] != 'time':
        return True
    try:
        stamp = datetime.fromisoformat(row.get('timestamp', '').replace('Z', '+00:00'))
        return stamp.tzinfo is not None and scope[2] <= stamp < scope[3]
    except (ValueError, TypeError, AttributeError):
        return False


def truncate_output(body, metadata, policy):
    """复刻原生文本首尾裁剪；预算以 UTF-8 字节近似，不使用分词器裁剪。"""
    if not isinstance(body, str):
        return body
    limit = metadata.get('fallback_token_limit_override', metadata.get('history_truncation_token_limit'))
    mode = 'tokens'
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        limit = policy.get('limit')
        mode = policy.get('mode', 'tokens')
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            return body
        limit = int(limit * 1.2)
    if mode not in {'tokens', 'bytes'}:
        return body
    budget = limit * 4 if mode == 'tokens' else limit
    raw = body.encode('utf-8')
    if len(raw) <= budget:
        return body
    left, right = budget // 2, budget - budget // 2
    prefix = raw[:left].decode('utf-8', errors='ignore')
    suffix = raw[len(raw) - right:].decode('utf-8', errors='ignore') if right else ''
    removed = (len(raw) - budget + 3) // 4 if mode == 'tokens' else len(body) - len(prefix) - len(suffix)
    return f'{prefix}…{removed} {mode if mode == "tokens" else "chars"} truncated…{suffix}'


@dataclass
class State:
    history: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    base: Counter = field(default_factory=Counter)
    policy: dict = field(default_factory=dict)
    model: str | None = None
    pending: tuple | None = None
    blocked: bool = False


def replay_history(paths, scope, estimate=None, native_query=None):
    estimate = estimate or token_estimator()[0]
    native_query = native_query or query_native_tools
    states, cached_items, catalogs = {}, {}, {}
    seen_events, seen_requests = set(), set()
    totals, categories, sessions, tool_stats = Counter(), Counter(), defaultdict(Counter), {}
    paths = sorted(paths)
    file_sessions = Counter()
    for path in paths:
        try:
            with path.open(encoding='utf-8') as stream:
                first = json.loads(next(stream, '{}'))
            owner = first.get('payload', {}).get('id') if first.get('type') == 'session_meta' else None
            file_sessions[owner or str(path)] += 1
        except (OSError, UnicodeError, ValueError, AttributeError):
            file_sessions[str(path)] += 1

    def entry(item, metadata, state, session):
        item = dict(item)
        kind = str(item.get('type', ''))
        if item.get('role') == 'system' or kind in {'reasoning', 'configuration_update'}:
            return None
        if kind.endswith('_call_output'):
            body = item.get('output', '')
            if isinstance(body, list):
                # 图片/音频的编码不是输入正文，不能拿其字符数当模型 Token。
                body = output_text(body)
            item['output'] = truncate_output(body, metadata, state.policy)
        packed = json.dumps(item, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(packed.encode()).hexdigest()
        if digest not in cached_items:
            content = defaultdict(list)
            collect_content({'type': 'response_item', 'payload': item}, content)
            cached_items[digest] = Counter({key: sum(estimate(body) for body in values) for key, values in content.items()})
        body_digest = hashlib.sha256(json.dumps(item.get('output'), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {'item': item, 'counts': cached_items[digest], 'key': (session, item.get('call_id'), body_digest)}

    def snapshot(state, session):
        counts = state.base.copy()
        calls = {e['item'].get('call_id'): e['item'] for e in state.history
                 if e['item'].get('type') in {'function_call', 'custom_tool_call'}}
        outputs = []
        has_tools = False
        for e in state.history:
            item = e['item']
            if str(item.get('type', '')).endswith('_call_output'):
                if not item.get('call_id') or item['call_id'] not in calls:
                    continue
                outputs.append((e, calls[item['call_id']]))
            counts.update(e['counts'])
            has_tools |= bool(item.get('type') == 'additional_tools' and item.get('tools'))
        if not has_tools and state.model:
            if state.model not in catalogs:
                try:
                    catalogs[state.model] = estimate(native_query(state.model))
                except (OSError, ValueError, subprocess.SubprocessError):
                    catalogs[state.model] = 0
            counts['工具定义（原生补估）'] += catalogs[state.model]
        return counts, outputs

    for path in paths:
        rows = []
        try:
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            rows.append(row)
                    except ValueError:
                        rows.append({'type': 'unreadable'})
        except (OSError, UnicodeError):
            totals['unreadable_files'] += 1
            continue
        session = next((r['payload'].get('id') for r in rows if r.get('type') == 'session_meta' and isinstance(r.get('payload'), dict) and r['payload'].get('id')), str(path))
        if scope[0] == 'session' and scope[4] and scope[4] != session and scope[4] not in str(path):
            continue
        state = states.setdefault(session, State())
        compaction_ids = {r.get('payload', {}).get('compaction_response_id') for r in rows
                          if r.get('type') == 'compacted' and isinstance(r.get('payload'), dict)} - {None}
        selected = [i for i, r in enumerate(rows) if r.get('type') == 'token_usage_record' and in_scope(r, scope)]
        if not selected and file_sessions[session] == 1:
            # 分片可能为后续文件提供历史，因此只跳过独立完整会话。
            continue
        checkpoints = [i for i, r in enumerate(rows[:selected[0]]) if r.get('type') == 'compacted'] if selected else []
        if checkpoints:
            checkpoint = checkpoints[-1]
            prefix = []
            for record_type in ('session_meta', 'turn_context'):
                candidates = [r for r in rows[:checkpoint] if r.get('type') == record_type]
                if candidates:
                    prefix.append(candidates[-1])
            rows = prefix + rows[checkpoint:]
        for row in rows:
            kind, payload = row.get('type'), row.get('payload', {})
            if not isinstance(payload, dict):
                continue
            fingerprint = hashlib.sha256(json.dumps({k: row.get(k) for k in ('type', 'payload', 'metadata', 'timestamp')}, sort_keys=True).encode()).digest()
            identity = (session, fingerprint)
            if identity in seen_events:
                continue
            seen_events.add(identity)
            if kind == 'unreadable':
                state.blocked = True
            elif kind == 'session_meta':
                content = defaultdict(list)
                collect_content(row, content)
                state.base = Counter({k: sum(estimate(body) for body in values) for k, values in content.items()})
            elif kind == 'turn_context':
                state.model = payload.get('model') or state.model
                state.policy = payload.get('truncation_policy') or state.policy
            elif kind == 'compacted':
                state.pending = None
                replacement = payload.get('replacement_history')
                state.history, state.turns = [], []
                state.blocked = not isinstance(replacement, list)
                metadata = payload.get('replacement_history_metadata') or []
                if not state.blocked:
                    for index, item in enumerate(replacement):
                        if not isinstance(item, dict):
                            state.blocked = True
                            break
                        meta = metadata[index] if index < len(metadata) and isinstance(metadata[index], dict) else {}
                        if isinstance(item.get('item'), dict):
                            meta, item = item.get('metadata') or meta, item['item']
                        e = entry(item, meta, state, session)
                        if e:
                            state.history.append(e)
            elif kind == 'event_msg':
                if payload.get('type') == 'task_started':
                    state.pending = None
                    state.turns.append(len(state.history))
                elif payload.get('type') in {'task_complete', 'turn_aborted'}:
                    state.pending = None
                elif payload.get('type') == 'thread_rolled_back':
                    n = payload.get('num_turns')
                    state.pending = None
                    if isinstance(n, int) and 0 < n <= len(state.turns):
                        state.history = state.history[:state.turns[-n]]
                        state.turns = state.turns[:-n]
                    elif n != 0:
                        state.blocked = True
            elif kind == 'response_item':
                meta = row.get('metadata') or {}
                if payload.get('role') == 'user' or str(payload.get('type', '')).endswith('_call_output'):
                    # 上次响应没有用量记录时，不能把其边界沿用到下一次调用。
                    state.pending = None
                output = (payload.get('type') in {'reasoning', 'function_call', 'custom_tool_call'}
                          or payload.get('role') == 'assistant') and not meta.get('client_authored', False)
                if output and state.pending is None:
                    state.pending = snapshot(state, session)
                e = entry(payload, meta, state, session)
                if e:
                    state.history.append(e)
            elif kind == 'token_usage_record':
                response = payload.get('response_id')
                request_key = (session, response or fingerprint)
                if request_key in seen_requests:
                    continue
                seen_requests.add(request_key)
                pending, state.pending = state.pending, None
                usage = payload.get('usage', payload)
                actual = usage.get('input_tokens') if isinstance(usage, dict) else None
                if not in_scope(row, scope) or response in compaction_ids:
                    continue
                if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0:
                    continue
                if payload.get('thread_id') and session != str(path) and payload['thread_id'] != session:
                    continue
                totals['observed_requests'] += 1
                if state.blocked or pending is None:
                    totals['skipped_requests'] += 1
                    continue
                counts, outputs = pending
                value = sum(counts.values())
                totals.update(requests=1, estimated=value, actual=actual)
                categories.update(counts)
                sessions[session].update(requests=1, estimated=value, actual=actual)
                included = set()
                for e, call in outputs:
                    key = e['key']
                    stat = tool_stats.setdefault(key, {'session': session, 'call_id': e['item'].get('call_id'),
                        'category': infer_tool(call, classify),
                        'tokens': e['counts'].get('工具结果', 0), 'requests': 0, 'occurrences': 0})
                    stat['occurrences'] += 1
                    if key not in included:
                        stat['requests'] += 1
                        included.add(key)
    return totals, categories, sessions, tool_stats


def render_replay(paths, scope, titles=None, replayed=None, official_input=None):
    totals, categories, sessions, tools = replayed if replayed is not None else replay_history(paths, scope)
    official = totals['actual'] if official_input is None else official_input
    if not totals['requests']:
        return ('## 输入内容分类\n\n无法重建输入分类；官方记录的输入全部列为未归因。\n\n'
                '| 输入类别 | 累计 Token |\n|---|---:|\n'
                f'| 未归因输入 | {official:,} |\n| 合计（官方记录） | {official:,} |\n')
    residual = official - totals['estimated']
    values = [(name, value) for name, value in categories.most_common() if value > 0]
    if residual > 0:
        values.append(('未归因与估算差额', residual))
    lines = ['## 输入内容分类', '',
             f"根据日志重建 {totals['requests']:,} 次调用；分类为估算，官方累计输入 {official:,} Token。", '']
    if residual < 0:
        lines.extend([f'分类估算合计超过官方记录 {-residual:,} Token，图中百分比以分类估算合计为基数。', ''])
    else:
        lines.extend(['图中百分比以官方累计输入为基数；差额包含未归因内容与估算误差。', ''])
    lines.extend(render_stacked(values))
    lines.extend(['分类基于历史重放，不是官方精确分类。', ''])
    if totals['skipped_requests']:
        lines.append(f"另有 {totals['skipped_requests']} 次调用缺少可重建的历史或输出边界，其输入包含在差额中。")
    return '\n'.join(lines) + '\n'
