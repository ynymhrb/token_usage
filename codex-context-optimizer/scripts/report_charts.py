"""生成无需报告文件的聊天内图表。"""
from collections import defaultdict


def chart_label(value):
    return str(value).replace('&', '＆').replace('"', '＂').replace('`', '｀').replace('<', '＜').replace('>', '＞').replace('\\', '／').replace('\n', ' ').replace('\r', ' ')


def render_charts(groups, session_name):
    lines = ['', '## 消耗分布', '', '口径：累计输入 Token（包含缓存输入）。', '', '### 按小时', '']
    days = defaultdict(dict)
    for label, row in groups['时间'].items():
        try:
            day, hour = label.split(' ')
            days[day][int(hour[:2])] = row['input_tokens']
        except (ValueError, IndexError):
            continue
    for day, hours in sorted(days.items()):
        slots = range(min(hours), max(hours) + 1)
        labels = ', '.join(f'"{hour:02d}:00"' for hour in slots)
        values = ', '.join(str(hours.get(hour, 0)) for hour in slots)
        lines.extend(['```mermaid', 'xychart-beta', f'    title "{chart_label(day)} 每小时输入 Token"', f'    x-axis [{labels}]', f'    y-axis "输入 Token" 0 --> {max(1, max(hours.values()))}', f'    bar [{values}]', '```', ''])
    if not days:
        lines.append('没有可绘制的小时用量。')
    lines.extend(['### 按会话', ''])
    rows = sorted(groups['会话'].items(), key=lambda item: -item[1]['input_tokens'])
    slices = []
    duplicate_names = defaultdict(int)
    for key, row in rows[:8]:
        name = session_name(key)
        duplicate_names[name] += 1
        if duplicate_names[name] > 1:
            name += f'（同名 {duplicate_names[name]}）'
        slices.append((name, row['input_tokens']))
    if len(rows) > 8:
        slices.append(('其余会话（合计）', sum(row['input_tokens'] for _, row in rows[8:])))
    if sum(value for _, value in slices):
        lines.extend(['```mermaid', 'pie showData', '    title 各会话累计输入 Token'])
        lines.extend(f'    "{chart_label(name)}" : {value}' for name, value in slices if value > 0)
        lines.extend(['```', ''])
    else:
        lines.append('没有可绘制的会话用量。')
    return lines
