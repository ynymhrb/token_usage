"""从本次可观察输入正文中选择优化对象，不推算节省额度。"""
from content_analysis import token_estimator
from record_analysis import tool_breakdown


def render_advice(contents, tools):
    estimate, _ = token_estimator()
    sizes = {name: sum(estimate(body) for body in bodies) for name, bodies in contents.items()}
    actions = {
        '工具定义': '检查是否常驻了本次任务未使用的工具说明，支持按需加载时再缩减。',
        '工具定义（原生补估）': '先核对实际启用的工具，再决定是否减少常驻工具说明。',
        '开发者指令': '检查重复规则与重复注入，保留任务约束。',
        '基础指令': '检查重复或过时说明，先验证精简后行为是否一致。',
        '工具参数与调用代码': '减少重复传入大段正文，优先传文件路径和必要参数。',
        'Skill 说明': '检查是否加载了与本次任务无关的技能说明。',
        'AGENTS 与工作区规则': '合并重复规则，保留项目约束。',
        '助手消息': '阶段结束时整理已确认结论，减少重复复述。',
        '用户消息': '保留需求与限制；长篇重复材料可以用引用代替。',
    }
    merged = tool_breakdown(tools)
    if merged:
        name, row = max(merged.items(), key=lambda item: item[1]['tokens'])
        actions['工具结果'] = (f'其中“{name}”正文最多（约 {row["tokens"]:,} Token）。'
                              '优先检查大返回值，缩小读取范围或仅返回关键结果。')
    candidates = [(name, size) for name, size in sizes.items() if size > 0 and name in actions]
    if not candidates:
        return ''
    lines = ['## 优先优化项', '', '按本次正文大小排序；这是排查顺序，不是预计节省量。', '']
    for index, (name, size) in enumerate(sorted(candidates, key=lambda item: -item[1])[:3], 1):
        lines.append(f'{index}. **{name}：约 {size:,} Token。** {actions[name]}')
    return '\n'.join(lines) + '\n'
