"""对实际存在的请求样本归因；不从历史消息猜测请求。"""
import json
from collections import defaultdict
from datetime import datetime
from content_analysis import collect_content, split_instructions, text_of


def inspect_requests(paths, scope, observations=None):
    contents = defaultdict(list)
    count = 0
    seen = set()
    for path in paths:
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not isinstance(row.get('request'), dict):
                continue
            if scope[0] == 'time':
                try:
                    timestamp = datetime.fromisoformat(row.get('timestamp', '').replace('Z', '+00:00'))
                    if timestamp.tzinfo is None or not scope[2] <= timestamp < scope[3]:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
            identity = (str(path), row.get('response_id') or row.get('request_id'))
            if identity[1] and identity in seen:
                continue
            seen.add(identity)
            request = row['request']
            count += 1
            aggregate = contents
            contents = defaultdict(list)
            if request.get('instructions'):
                split_instructions(text_of(request['instructions']), '请求顶层指令', contents, allow_agents=True)
            for field in ('tools', 'persisted_dynamic_tools'):
                if request.get(field):
                    contents['工具定义'].append(json.dumps(request[field], ensure_ascii=False, sort_keys=True))
            items = request.get('input', [])
            if isinstance(items, str):
                items = [{'role': 'user', 'content': items}]
            if not isinstance(items, list):
                items = [items]
            for item in items:
                if isinstance(item, dict):
                    item = dict(item)
                    if 'role' in item:
                        item.setdefault('type', 'message')
                    before = sum(len(values) for values in contents.values())
                    collect_content({'type': 'response_item', 'payload': item}, contents)
                    if sum(len(values) for values in contents.values()) > before:
                        continue
                contents['其他请求内容'].append(json.dumps(item, ensure_ascii=False, sort_keys=True))
            if observations is not None:
                observations.append({'session': str(row.get('thread_id') or row.get('session_id') or path),
                                     'response_id': row.get('response_id'), 'contents': contents,
                                     'tools': [item for item in items if isinstance(item, dict) and str(item.get('type', '')).endswith('_call_output')]})
            for category, values in contents.items():
                aggregate[category].extend(values)
            contents = aggregate
    return count, contents
