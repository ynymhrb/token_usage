"""按事件时间统计本地记录；内容字符量不等于请求 Token 用量。"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from content_analysis import infer_tool, collect_content, token_estimator, output_text
from report_charts import render_charts, render_stacked


def inspect_records(paths, scope, contents=None):
    totals = Counter()
    groups = {key: defaultdict(Counter) for key in ('时间', '会话', '回合')}
    tools = defaultdict(Counter)
    seen_usage, seen_outputs, observed, used = set(), set(), set(), set()
    missing_time = malformed = 0
    seen_content = set()
    estimate, _ = token_estimator()
    for path in paths:
        try:
            records = []
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except ValueError:
                    malformed += 1
        except (OSError, UnicodeError):
            continue
        names = {}
        for r in records:
            p = r.get('payload', {})
            if isinstance(p, dict) and p.get('call_id') and p.get('name'):
                names[p['call_id']] = p
        for r in records:
            p = r.get('payload', {})
            if not isinstance(p, dict):
                continue
            try:
                stamp = datetime.fromisoformat(r.get('timestamp', '').replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    stamp = None
            except (ValueError, TypeError, AttributeError):
                stamp = None
            if scope[0] == 'time':
                if stamp is None:
                    missing_time += 1
                    continue
                if not scope[2] <= stamp < scope[3]:
                    continue
            observed.add(path)
            is_tool_output = r.get('type') == 'response_item' and str(p.get('type', '')).endswith('_call_output')
            if is_tool_output:
                identity = (path.stem, p.get('call_id'), json.dumps(p, sort_keys=True))
                if identity in seen_outputs:
                    continue
                seen_outputs.add(identity)
            if contents is not None:
                identity = (path.stem, p.get('id') or json.dumps(r, sort_keys=True))
                if is_tool_output or identity not in seen_content:
                    seen_content.add(identity)
                    collect_content(r, contents)
            if r.get('type') == 'token_usage_record':
                usage = p.get('usage', p)
                if not isinstance(usage, dict) or not isinstance(usage.get('input_tokens'), int):
                    continue
                session = str(p.get('thread_id') or p.get('session_id') or path.stem)
                identity = (session, p['response_id']) if p.get('response_id') else json.dumps(r, sort_keys=True)
                if identity in seen_usage:
                    continue
                seen_usage.add(identity)
                used.add(path)
                values = Counter({k: v for k, v in usage.items() if isinstance(v, int) and not isinstance(v, bool) and v >= 0})
                totals.update(values)
                hour = stamp.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:00') if stamp else '时间缺失'
                turn = session + '/' + str(p.get('turn_id') or '回合缺失')
                for dimension, label in [('时间', hour), ('会话', session), ('回合', turn)]:
                    groups[dimension][label].update(values)
                    groups[dimension][label]['calls'] += 1
            if is_tool_output:
                call = names.get(p.get('call_id'), {})
                category = infer_tool(call, classify)
                body = p.get('output', '')
                body = output_text(body)
                chars = len(body)
                tokens = estimate(body)
                tools[category]['calls'] += 1
                tools[category]['characters'] += chars
                tools[category]['largest'] = max(tools[category]['largest'], chars)
                tools[category]['tokens'] += tokens
                tools[category]['largest_tokens'] = max(tools[category]['largest_tokens'], tokens)
    return totals, groups, tools, len(observed), len(used), missing_time, malformed


def classify(name):
    name = name.lower()
    if name.rsplit('.', 1)[-1] in {'exec', 'wait'}:
        return '代码执行与组合工具'
    if any(x in name for x in ('exec_command', 'write_stdin', 'run_command')):
        return '命令执行与日志'
    if any(x in name for x in ('read_file', 'view_image')):
        return '文件读取'
    if any(x in name for x in ('web', 'search', 'browse')):
        return '检索与网页'
    if 'git' in name:
        return 'Git'
    if any(x in name for x in ('agent', 'mcp', 'collaboration')):
        return 'Agent 与 MCP'
    return '其他或无法识别'


TOOL_CATEGORIES = (
    '文件读取与搜索', '命令执行与日志', '测试与构建日志', 'Git',
    '网页与网络', '文件修改', '子代理与 MCP', '混合或无法识别',
)

_TOOL_CATEGORY_ALIASES = {
    '文件读取': '文件读取与搜索',
    '检索与网页': '网页与网络',
    '网络请求': '网页与网络',
    'Agent 与 MCP': '子代理与 MCP',
    '混合命令输出': '混合或无法识别',
    '多类工具混合返回': '混合或无法识别',
    '动态调用或组合工具（未能细分）': '混合或无法识别',
    '代码执行与组合工具': '混合或无法识别',
    '其他或无法识别': '混合或无法识别',
}


def tool_breakdown(tools):
    """合并同义类别；来源依据独立于类别和统计数值。"""
    merged = {}
    for name, row in tools.items():
        basis = '工具名称或命令参数'
        if name.endswith('（按调用代码推断）'):
            name, basis = name.removesuffix('（按调用代码推断）'), '调用代码推断'
        if name in {'混合命令输出', '多类工具混合返回'}:
            basis = '包含多类操作，无法可靠拆分'
        elif name in {'动态调用或组合工具（未能细分）', '代码执行与组合工具', '其他或无法识别'}:
            basis = '无法识别具体类别'
        name = _TOOL_CATEGORY_ALIASES.get(name, name)
        if name not in TOOL_CATEGORIES:
            name, basis = '混合或无法识别', '无法识别具体类别'
        entry = merged.setdefault(name, {'tokens': 0, 'calls': 0, 'largest_tokens': 0, 'sources': set()})
        entry['tokens'] += row.get('tokens', 0)
        entry['calls'] += row['calls']
        entry['largest_tokens'] = max(entry['largest_tokens'], row.get('largest_tokens', 0))
        entry['sources'].add(basis)
    return merged


def render_details(groups, tools, missing_time, malformed, titles=None, cumulative=False):
    titles = titles or {}
    unknown = {}
    def session_name(session):
        if session in titles:
            name = ' '.join(str(titles[session]).split())
            return name if len(name) <= 80 else name[:79] + '…'
        if session not in unknown:
            unknown[session] = f'未命名会话 {len(unknown) + 1}'
        return unknown[session]
    lines = ['### 输入中的工具结果分类', '', '对应上表“工具结果”一项，使用相同的 Token 估算口径；每条返回计一次。', '', '| 类别 | 来源依据 | 返回次数 | 估算 Token | 占工具结果 | 最大单次 Token |', '|---|---|---:|---:|---:|---:|']
    merged = tool_breakdown(tools)
    total_tokens = sum(row['tokens'] for row in merged.values())
    if cumulative:
        lines = ['### 输入中的工具结果分类', '', '按历史重放估算累计输入；同一结果每次携带均计入。', '']
        lines.extend(render_stacked([(name, row['tokens']) for name, row in merged.items()]))
        if merged:
            lines.append('来源依据：' + '；'.join(name + '：' + '、'.join(sorted(row['sources'])) for name, row in sorted(merged.items(), key=lambda item: -item[1]['tokens'])) + '。')
            lines.append('')
    else:
        for name, row in sorted(merged.items(), key=lambda x: -x[1]['tokens']):
            share = f"{row['tokens'] / total_tokens:.1%}" if total_tokens else '—'
            basis = '、'.join(sorted(row['sources']))
            lines.append(f"| {name} | {basis} | {row['calls']} | {row['tokens']:,} | {share} | {row['largest_tokens']:,} |")
        if merged:
            lines.append(f"| 合计 | | {sum(row['calls'] for row in merged.values())} | {total_tokens:,} | {'100.0%' if total_tokens else '—'} | |")
        if not tools:
            lines.append('未找到可统计的工具返回记录。')
    lines.extend(render_charts(groups, session_name))
    if missing_time or malformed:
        issues = []
        if missing_time:
            issues.append(f'{missing_time} 条记录缺少有效时间，无法纳入时间范围统计')
        if malformed:
            issues.append(f'{malformed} 行记录无法解析')
        lines.extend(['', '统计异常：' + '；'.join(issues) + '。', ''])
    return '\n'.join(lines)
