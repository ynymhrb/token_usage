"""对保存的内容做可复核分类；不重建服务端请求。"""
import json
import math
import re
import sqlite3
import os
import hashlib
import base64
from pathlib import Path
from collections import Counter


def text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(text_of(item) for item in value)
    if isinstance(value, dict):
        return text_of(value.get('text', value.get('content', '')))
    return ''


def output_text(value):
    """工具多模态返回只统计文本；图片和音频编码不属于可见正文。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, dict) and 'type' in part for part in value):
        return '\n'.join(text_of(part) for part in value if part.get('type') in {'text', 'input_text', 'output_text'})
    return json.dumps(value, ensure_ascii=False)


def load_titles(root):
    result = {}
    named_ids = set()
    databases = sorted(root.glob('state_*.sqlite'), key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1, reverse=True)
    if databases:
        try:
            connection = sqlite3.connect(databases[0].as_uri() + '?mode=ro', uri=True, timeout=1)
            try:
                columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
                query = 'SELECT id, title, name FROM threads' if 'name' in columns else 'SELECT id, title, NULL FROM threads'
                for key, title, name in connection.execute(query):
                    if name or title:
                        result[key] = name or title
                    if name:
                        named_ids.add(key)
            finally:
                connection.close()
        except (sqlite3.Error, ValueError):
            pass
    try:
        for line in (root / 'session_index.jsonl').read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
                if row.get('id') and row.get('thread_name'):
                    if row['id'] not in named_ids:
                        result[row['id']] = row['thread_name']
            except (ValueError, AttributeError):
                continue
    except (OSError, UnicodeError):
        pass
    return result


def command_category(command):
    """只推断明确的命令类型，无法拆分的混合命令保留混合类别。"""
    categories = set()
    patterns = {
        'Git': r'\bgit\s+',
        '文件读取与搜索': r'\b(?:Get-Content|Get-ChildItem|rg|cat|head|tail)\b',
        '测试与构建日志': r'\b(?:pytest|unittest|npm\s+test|cargo\s+test|build)\b',
        '网络请求': r'\b(?:curl|Invoke-WebRequest|wget)\b',
    }
    for name, pattern in patterns.items():
        if re.search(pattern, command, re.I):
            categories.add(name)
    return next(iter(categories)) if len(categories) == 1 else ('混合命令输出' if categories else '命令执行与日志')


def infer_tool(call, classify):
    name = str(call.get('name', '未关联工具'))
    raw = call.get('input', call.get('arguments', ''))
    source = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    if name.rsplit('.', 1)[-1] not in {'exec', 'exec_command', 'run_command'}:
        return classify(name)
    if name.rsplit('.', 1)[-1] in {'exec_command', 'run_command'}:
        try:
            args = json.loads(source)
            return command_category(str(args.get('cmd', args.get('command', ''))))
        except (ValueError, AttributeError):
            return classify(name)
    inner = re.findall(r'\btools\.([A-Za-z_][A-Za-z_0-9]*)\s*\(', source)
    categories = set()
    for tool in inner:
        if tool in {'exec_command', 'run_command'}:
            # 字符串命令只能作为类型线索，不据此猜测各内层结果大小。
            categories.add(command_category(source))
        elif tool == 'apply_patch':
            categories.add('文件修改')
        else:
            categories.add(classify(tool))
    if not categories:
        return '动态调用或组合工具（未能细分）'
    return next(iter(categories)) + '（按调用代码推断）' if len(categories) == 1 else '多类工具混合返回'


def split_instructions(body, fallback, counts, allow_agents=False):
    """只拆显式来源区块；保留区块外正文，避免重复计数。"""
    tags = [('skills_instructions', 'Skill 说明'), ('skill', 'Skill 正文（显式标记）')]
    if allow_agents or '# AGENTS.md instructions' in body:
        tags.append(('INSTRUCTIONS', 'AGENTS 与工作区规则'))
    for tag, category in tags:
        pattern = rf'<{tag}(?:\s[^>]*)?>[\s\S]*?</{tag}>'
        for match in re.findall(pattern, body):
            counts[category].append(match)
        body = re.sub(pattern, '', body)
    if body:
        counts[fallback].append(body)


def collect_content(record, counts):
    payload = record.get('payload', {})
    kind = record.get('type')
    if kind == 'session_meta':
        body = text_of(payload.get('base_instructions', ''))
        if body:
            split_instructions(body, '基础指令', counts, allow_agents=True)
    if kind != 'response_item':
        return
    item_type = payload.get('type', '')
    if item_type == 'additional_tools':
        if payload.get('tools'):
            counts['工具定义'].append(json.dumps(payload['tools'], ensure_ascii=False, sort_keys=True))
        return
    if item_type == 'message':
        body = text_of(payload.get('content', ''))
        category = {'user': '用户消息', 'assistant': '助手消息', 'developer': '开发者指令', 'system': '系统指令'}.get(payload.get('role'), '其他消息')
        metadata = payload.get('internal_chat_message_metadata_passthrough')
        if (payload.get('role') == 'developer' and isinstance(metadata, dict)
                and metadata.get('content_item_kinds') == ['model.base_instructions']):
            category = '基础指令'
        if payload.get('role') in {'developer', 'system'} or (payload.get('role') == 'user' and '# AGENTS.md instructions' in body):
            split_instructions(body, category, counts, allow_agents=payload.get('role') in {'developer', 'system'})
            return
    elif item_type.endswith('_call_output'):
        category = '工具结果'
        value = payload.get('output', '')
        body = output_text(value)
    elif item_type in {'function_call', 'custom_tool_call'}:
        category = '工具参数与调用代码'
        value = payload.get('input', payload.get('arguments', ''))
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    else:
        return
    if body:
        counts[category].append(body)


def token_estimator():
    # 无本地分词依赖时也提供显式粗估，绝不联网下载或把估算称作账单。
    def estimate(body):
        non_ascii = sum(ord(c) > 127 for c in body)
        return math.ceil((len(body) - non_ascii) / 4 + non_ascii)
    method = '粗估公式：ASCII 字符数 ÷ 4 + 非 ASCII 字符数；中文、代码等内容的误差不同。'
    dictionary = Path(os.environ.get('TOKEN_ANALYZER_TOKENIZER', 'D:/ai_soft/tokenUsage/o200k_base.tiktoken'))
    if dictionary.is_file():
        try:
            import tiktoken
            data = dictionary.read_bytes()
            if hashlib.sha256(data).hexdigest() != '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d':
                raise ValueError('本地词表校验失败')
            # 使用固定分词规则，避免执行源码或依赖动态求值。
            pat = r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?|[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|\s+(?!\S)|\s+"
            ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in data.splitlines() if line)}
            encoder = tiktoken.Encoding('local_o200k_base', pat_str=pat, mergeable_ranks=ranks, special_tokens={})
            estimate = lambda body: len(encoder.encode_ordinary(body))
            method = '使用本地 o200k_base 分词计数；这是可见正文的分词估算，不保证与实际模型的分词器一致。'
        except (ImportError, OSError, ValueError):
            method = '本地分词器不可用；' + method
    return estimate, method


def render_content(contents, request_count=0):
    estimate, method = token_estimator()
    rows = {key: sum(estimate(body) for body in values) for key, values in contents.items()}
    total = sum(rows.values())
    title = '### 请求样本中的输入分类' if request_count else '## 原始内容分类（不累计重复携带）'
    basis = f'来自 {request_count} 次已保存请求，按请求累计。' if request_count else '以下是日志中可识别的输入正文大小，每条计一次，未累计重复携带；占比仅针对本表合计。助手历史属于后续输入。'
    note = '基础指令可能与实际发送版本存在一定误差。' if any(key in contents for key in ('基础指令', '请求顶层指令')) else ''
    method_note = '本地分词器不可用，使用字符粗估。' if '粗估公式' in method else ''
    lines = [title, '', basis, '', '| 类别 | 估算 Token | 占正文合计 | 来源依据 |', '|---|---:|---:|---|']
    sources = {'基础指令': '基础指令字段', '请求顶层指令': 'request.instructions', '工具定义': 'request.tools / persisted_dynamic_tools', '系统指令': 'system 角色', '开发者指令': 'developer 角色', 'Skill 说明': 'skills_instructions 显式区块', 'Skill 正文（显式标记）': 'skill 显式区块', 'AGENTS 与工作区规则': '规则消息中的 INSTRUCTIONS 区块', '工具结果': '调用返回记录', '工具参数与调用代码': '调用参数字段', '用户消息': 'user 角色', '助手消息': 'assistant 角色'}
    sources['工具定义'] = '已保存的工具声明'
    sources['工具定义（原生补估）'] = '当前原生工具定义基线'
    sources.update({'开发者指令': '开发者消息', '用户消息': '用户消息', '助手消息': '助手历史消息',
                    '系统指令': '系统消息', 'Skill 说明': '技能说明区块',
                    'AGENTS 与工作区规则': '工作区规则区块', '请求顶层指令': '请求中的基础指令'})
    for key, value in sorted(rows.items(), key=lambda item: -item[1]):
        share = f'{value / total:.1%}' if total else '不可得'
        lines.append(f'| {key} | {value:,} | {share} | {sources.get(key, "未识别结构，保留原类别")} |')
    if not total:
        lines.append('范围内没有可分类的已保存正文。')
    else:
        lines.append(f'| 合计 | {total:,} | 100.0% | |')
    if note or method_note:
        lines.extend(['', note + method_note])
    lines.append('')
    return '\n'.join(lines)
