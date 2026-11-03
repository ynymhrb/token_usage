"""按事件时间统计本地记录；内容字符量不等于请求 Token 用量。"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from content_analysis import infer_tool, collect_content


def inspect_records(paths, scope, contents=None):
    totals = Counter()
    groups = {key: defaultdict(Counter) for key in ('时间', '会话', '回合')}
    tools = defaultdict(Counter)
    seen_usage, seen_outputs, observed, used = set(), set(), set(), set()
    missing_time = malformed = 0
    seen_content = set()
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
            if contents is not None:
                identity = (path.stem, p.get('id') or json.dumps(r, sort_keys=True))
                if identity not in seen_content:
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
            if r.get('type') == 'response_item' and str(p.get('type', '')).endswith('_call_output'):
                identity = (path.stem, p.get('call_id'), json.dumps(p, sort_keys=True))
                if identity in seen_outputs:
                    continue
                seen_outputs.add(identity)
                call = names.get(p.get('call_id'), {})
                category = infer_tool(call, classify)
                body = p.get('output', '')
                chars = len(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
                tools[category]['calls'] += 1
                tools[category]['characters'] += chars
                tools[category]['largest'] = max(tools[category]['largest'], chars)
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


def render_details(groups, tools, missing_time, malformed, titles=None):
    titles = titles or {}
    unknown = {}
    def session_name(session):
        if session in titles:
            name = ' '.join(str(titles[session]).split())
            return name if len(name) <= 80 else name[:79] + '…'
        if session not in unknown:
            unknown[session] = f'未命名会话 {len(unknown) + 1}'
        return unknown[session]
    lines = ['## 工具结果细分', '', '口径：本地已保存的工具返回内容字符数，按工具名称分类；不是累计输入 Token，也不代表完整请求上下文占比。', '', '| 类别 | 返回次数 | 字符数 | 占工具返回字符 | 最大单次字符数 |', '|---|---:|---:|---:|---:|']
    total_chars = sum(row['characters'] for row in tools.values())
    for name, row in sorted(tools.items(), key=lambda x: -x[1]['characters']):
        share = f"{row['characters'] / total_chars:.1%}" if total_chars else '不可得'
        lines.append(f"| {name} | {row['calls']} | {row['characters']:,} | {share} | {row['largest']:,} |")
    if not tools:
        lines.append('未找到可统计的工具返回记录。')
    lines.extend(['', '## 消耗排行', '', '按累计输入 Token 排序，每个维度展示前 10 项；同一上下文在多次请求中会重复计入输入。'])
    for dimension, rows in groups.items():
        heading = {'时间': '时间段', '会话': '会话名称', '回合': '会话名称 / 回合'}[dimension]
        lines.extend(['', f'### 按{dimension}', '', f'| {heading} | 输入 Token | 输出 Token | 调用数 |', '|---|---:|---:|---:|'])
        if not rows:
            lines.append('未找到可统计的用量记录。')
        for label, row in sorted(rows.items(), key=lambda x: -x[1]['input_tokens'])[:10]:
            if dimension == '会话':
                label = session_name(label)
            elif dimension == '回合':
                session, turn = label.split('/', 1)
                turns = [key.split('/', 1)[1] for key in rows if key.startswith(session + '/')]
                label = session_name(session) + ' / ' + (f'范围内第 {turns.index(turn) + 1} 回合' if turn != '回合缺失' else '回合缺失')
            label = label.replace('|', '\\|').replace('\n', ' ')
            lines.append(f"| {label} | {row['input_tokens']:,} | {row['output_tokens']:,} | {row['calls']} |")
    lines.extend(['', '组合工具按调用代码推断类别；混合返回单列，不把整段输出强行分配给某一个内层工具。会话缺名时显示稳定的报告内序号；同名会话仍分别统计。', f'范围检查：{missing_time} 条记录缺少有效时间，时间范围统计时已排除；{malformed} 行 JSON 无法解析。', ''])
    return '\n'.join(lines)
