"""生成无需报告文件的聊天内图表。"""
from collections import defaultdict
from datetime import datetime, timedelta


def chart_label(value):
    return str(value).replace('&', '＆').replace('"', '＂').replace('`', '｀').replace('<', '＜').replace('>', '＞').replace('\\', '／').replace('\n', ' ').replace('\r', ' ')


def render_charts(groups, session_name):
    lines = ['', '## 消耗分布', '', '口径：累计输入 Token（包含缓存输入）。', '', '### 按小时', '']
    hours = defaultdict(int)
    for label, row in groups['时间'].items():
        try:
            hour = datetime.strptime(label, '%Y-%m-%d %H:%M').replace(minute=0)
            hours[hour] += row['input_tokens']
        except (ValueError, TypeError):
            continue
    if hours:
        start, end = min(hours), max(hours)
        span = (end - start) // timedelta(hours=1) + 1
        step = (span + 11) // 12
        values = [0] * ((span + step - 1) // step)
        for hour, value in hours.items():
            values[((hour - start) // timedelta(hours=1)) // step] += value
        slots = [start + timedelta(hours=i * step) for i in range(len(values))]
        if start.date() == end.date():
            label_format = '%H:%M'
        elif (start.year, start.month) == (end.year, end.month):
            label_format = '%d日%H时'
        else:
            label_format = '%m/%d %H时'
        labels = ', '.join(f'"{hour.strftime(label_format)}"' for hour in slots)
        period = str(start.date()) if start.date() == end.date() else f'{start.date()} 至 {end.date()}'
        lines.extend([
            f'每 {step} 小时合并一柱；横轴为时段起点，末段不足 {step} 小时按实际记录汇总。', '',
            '```mermaid', '---', 'config:', '  themeVariables:', '    xyChart:',
            "      plotColorPalette: '#2563eb'", '  xyChart:', '    width: 960',
            '    xAxis:', '      labelFontSize: 12', '---', 'xychart-beta',
            f'    title "{period} 每 {step} 小时输入 Token"',
            f'    x-axis [{labels}]', f'    y-axis "输入 Token" 0 --> {max(1, max(values))}',
            f'    bar [{", ".join(str(value) for value in values)}]', '```', '',
        ])
    else:
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
