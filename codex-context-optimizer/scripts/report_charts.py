"""生成无需报告文件的聊天内图表。"""
from collections import defaultdict
from datetime import datetime, timedelta

CHART_WIDTH = 640
PIE_WIDTH = 480
BAR_WIDTH = 32


def chart_config(width, *, bars=False):
    """限制图表尺寸；从柱子两侧等量裁剪，保持时间槽中心不变。"""
    css = f'& {{ width: 100% !important; max-width: {width}px !important; height: auto; }}'
    if bars:
        css += (f' .bar-plot-0 rect {{ clip-path: inset(0 max(0px, '
                f'calc((100% - {BAR_WIDTH}px) / 2))); }}')
    return ['```mermaid', '---', 'config:',
            f'  themeCSS: "{css}"']


def chart_label(value):
    return str(value).replace('&', '＆').replace('"', '＂').replace('`', '｀').replace('<', '＜').replace('>', '＞').replace('\\', '／').replace('\n', ' ').replace('\r', ' ')



def render_stacked(values):
    """用垂直堆积色块表示占比，微小类别合并。"""
    rows = [(str(name), value) for name, value in values if value > 0]
    total = sum(value for _, value in rows)
    if not total:
        return ['没有可绘制的分类数据。', '']
    rows.sort(key=lambda row: -row[1])
    major = [(name, value) for name, value in rows if value / total >= 0.05]
    minor = sum(value for _, value in rows if value / total < 0.05)
    if minor:
        major.append(('其他（各项不足 5%）', minor))
    if len(major) > 10:
        major = major[:9] + [('其他', sum(value for _, value in major[9:]))]
    raw = [value * 20 / total for _, value in major]
    widths = [int(value) for value in raw]
    for index in sorted(range(len(raw)), key=lambda i: raw[i] - widths[i], reverse=True)[:20 - sum(widths)]:
        widths[index] += 1
    # 最小一格保留每个图中类别，其他格按最大余数分配。
    for index, width in enumerate(widths):
        if width == 0:
            donor = max(range(len(widths)), key=lambda i: widths[i])
            widths[donor] -= 1
            widths[index] = 1
    colors = ['#2563eb', '#047857', '#a16207', '#7c3aed', '#dc2626', '#0e7490', '#be185d', '#4d7c0f', '#c2410c', '#475569']
    lines = [*chart_config(280), '  block:', '    padding: 0', '---', 'block-beta', '  columns 1']
    for index, ((name, value), width) in enumerate(zip(major, widths)):
        label = chart_label(f'{name} {value / total:.1%} · {value:,} Token')
        for unit in range(width):
            text = label if unit == (width - 1) // 2 else ' '
            lines.append(f'  s{index}_{unit}["{text}"]')
    for index, width in enumerate(widths):
        for unit in range(width):
            lines.append(f'  style s{index}_{unit} fill:{colors[index]},stroke:{colors[index]},color:#fff')
    lines.extend(['```', ''])
    if minor:
        lines.extend(['', '不足 5% 的分类：'])
        for name, value in rows:
            if value / total < 0.05:
                lines.append(f'- {name}：{value:,} Token（{value / total:.1%}）')
    lines.extend(['', '色块高度按 5% 为单位近似，最小类别至少占一格；图内文字为准确占比和估算值。', ''])
    return lines

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
            f'每柱 {step} 小时，横轴标注时段起点。', '',
            *chart_config(CHART_WIDTH, bars=True), '  themeVariables:', '    xyChart:',
            "      plotColorPalette: '#2563eb'", '  xyChart:', f'    width: {CHART_WIDTH}',
            '    height: 300', '    titleFontSize: 16',
            '    xAxis:', '      labelFontSize: 10', '---', 'xychart-beta',
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
        lines.extend([*chart_config(PIE_WIDTH), '  pie:', '    useMaxWidth: true', '---', 'pie showData', '    title 各会话累计输入 Token'])
        lines.extend(f'    "{chart_label(name)}" : {value}' for name, value in slices if value > 0)
        lines.extend(['```', ''])
    else:
        lines.append('没有可绘制的会话用量。')
    return lines
