"""随插件分发的 Token 分析运行时，不依赖仓库外的 Python 包。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo
from record_analysis import inspect_records, render_details
from content_analysis import load_titles, render_content
from request_analysis import inspect_requests


SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPORT_SUFFIX = ".llm_calls.jsonl"
_RECENT_HOURS = re.compile(r"^最近\s*(\d+)\s*小时$")
_ABSOLUTE_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})\s*(?:至|到|[-—–])\s*(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})$")
_ROLLOUT_START = re.compile(r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})")
_CATEGORIES = ("static_instructions", "tool_definitions", "developer_messages", "user_messages", "assistant_history", "tool_arguments", "tool_results", "unattributable")
_LABELS = {
    "static_instructions": "静态指令",
    "tool_definitions": "工具定义",
    "developer_messages": "开发者消息",
    "user_messages": "用户消息",
    "assistant_history": "助手历史",
    "tool_arguments": "工具调用参数",
    "tool_results": "工具结果",
    "unattributable": "不可归因项",
}


class AnalysisError(ValueError):
    """插件无法安全执行用户请求。"""


def _scope(expression: str | None, session_id: str | None, now: datetime) -> tuple[str, str, datetime | None, datetime | None, str | None]:
    value = expression.strip() if expression else None
    if not value or value in {"当前会话", "这个会话"}:
        if not session_id:
            raise AnalysisError("无法取得当前会话标识；请明确指定范围，不能自动扫描历史。")
        return "session", "当前会话", None, None, session_id
    if value == "全部历史":
        return "all", "用户明确要求全部历史", None, None, None
    current_local = now.astimezone(SHANGHAI)
    if value in {"今天", "昨天"}:
        end_local = current_local if value == "今天" else current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_local = end_local.replace(hour=0, minute=0, second=0, microsecond=0) if value == "今天" else end_local - timedelta(days=1)
        return "time", f"{value}（上海时间）", start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), None
    recent = _RECENT_HOURS.match(value)
    if recent:
        hours = int(recent.group(1))
        if hours <= 0:
            raise AnalysisError("最近小时数必须为正数。")
        return "time", f"最近 {hours} 小时", now - timedelta(hours=hours), now, None
    absolute = _ABSOLUTE_RANGE.match(value)
    if absolute:
        try:
            start = datetime.strptime(absolute.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI).astimezone(timezone.utc)
            end = datetime.strptime(absolute.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI).astimezone(timezone.utc)
        except ValueError as error:
            raise AnalysisError("时间格式无效，请使用 YYYY-MM-DD HH:MM 至 YYYY-MM-DD HH:MM。") from error
        if end <= start:
            raise AnalysisError("结束时间必须晚于开始时间。")
        return "time", f"用户指定时间：{value}", start, end, None
    raise AnalysisError("无法确定分析范围；请使用“最近 2 小时”“今天”“昨天”“当前会话”或“全部历史”。")


def _start(path: Path) -> datetime | None:
    match = _ROLLOUT_START.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _select(paths: Iterable[Path], scope: tuple[str, str, datetime | None, datetime | None, str | None]) -> list[Path]:
    kind, _, start, end, session_id = scope
    if kind == "all":
        return list(paths)
    if kind == "session":
        return [path for path in paths if session_id and session_id in path.name]
    return [path for path in paths if (stamp := _start(path)) is not None and start is not None and end is not None and start <= stamp < end]


def _rollouts(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [path for path in root.rglob("*.jsonl") if path.is_file() and not path.name.endswith((".api_trace.jsonl", EXPORT_SUFFIX))]


def _exports(root: Path) -> list[Path]:
    return list(root.rglob(f"*{EXPORT_SUFFIX}")) if root.is_dir() else []


def _usage(paths: Iterable[Path]) -> tuple[int, int, int, int]:
    input_tokens = output_tokens = cached_tokens = readable = 0
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        readable += 1
        seen: set[str] = set()
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "token_usage_record":
                continue
            fingerprint = hashlib.sha256(line.encode("utf-8")).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            payload = record.get("payload", {})
            usage = payload.get("usage", payload) if isinstance(payload, dict) else {}
            if not isinstance(usage, dict):
                continue
            input_tokens += usage.get("input_tokens", 0) if isinstance(usage.get("input_tokens"), int) else 0
            output_tokens += usage.get("output_tokens", 0) if isinstance(usage.get("output_tokens"), int) else 0
            cached_tokens += usage.get("cached_input_tokens", 0) if isinstance(usage.get("cached_input_tokens"), int) else 0
    return input_tokens, output_tokens, cached_tokens, readable


def _category(item: dict[str, object]) -> str:
    item_type = item.get("type")
    if item_type == "message":
        return {"developer": "developer_messages", "user": "user_messages", "assistant": "assistant_history"}.get(item.get("role"), "unattributable")
    if isinstance(item_type, str) and item_type.endswith("_output"):
        return "tool_results"
    if item_type in {"function_call", "web_search_call", "tool_search_call", "computer_call", "custom_tool_call"}:
        return "tool_arguments"
    return "unattributable"


def _context(paths: Iterable[Path], scope=None) -> tuple[int, Counter[str]]:
    counts: Counter[str] = Counter({key: 0 for key in _CATEGORIES})
    calls, contents = inspect_requests(paths, scope or ('all', '', None, None, None))
    mapping = {'请求顶层指令': 'static_instructions', '系统指令': 'static_instructions', '基础指令': 'static_instructions', 'Skill 说明': 'static_instructions', 'Skill 正文（显式标记）': 'static_instructions', 'AGENTS 与工作区规则': 'static_instructions', '工具定义': 'tool_definitions', '开发者指令': 'developer_messages', '用户消息': 'user_messages', '助手消息': 'assistant_history', '工具参数与调用代码': 'tool_arguments', '工具结果': 'tool_results'}
    for category, bodies in contents.items():
        counts[mapping.get(category, 'unattributable')] += sum(map(len, bodies))
    return calls, counts


def _category_rows(categories: Counter[str]) -> list[tuple[str, int, float | None, str]]:
    total = sum(categories.values())
    actions = {
        "static_instructions": "暂不优先；先检查是否存在重复规则。",
        "tool_definitions": "按任务缩小常驻工具定义。",
        "developer_messages": "合并重复开发者规则。",
        "user_messages": "合并重复需求描述。",
        "assistant_history": "完成阶段后生成短摘要。",
        "tool_arguments": "避免重复传入大文本。",
        "tool_results": "优先摘要化、落文件并按需读取。",
        "unattributable": "先补采集，不能安全地下结论。",
    }
    return [
        (key, categories[key], round(categories[key] / total, 6) if total else None, actions[key])
        for key in _CATEGORIES
    ]


def _diagnostic_markdown(
    *,
    scope_description: str,
    rollout_count: int,
    readable_trace_count: int,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    uncached_tokens: int,
    cache_ratio: float | None,
    request_call_count: int,
    export_rollout_count: int,
    categories: Counter[str],
    session_count: int = 0,
) -> str:
    """生成聊天窗可读诊断；未知数据显式保留为缺口。"""
    cache_percent = f"{cache_ratio * 100:.1f}%" if cache_ratio is not None else "不可得"
    coverage = f"{export_rollout_count}/{rollout_count}" if rollout_count else "0/0"
    lines = [
        "# Codex 上下文与 Token 诊断", "",
        "## 结论", "",
        f"- 分析范围：{scope_description}；有用量记录的会话 {session_count} 个，涉及 {rollout_count} 份会话记录文件，其中 {readable_trace_count} 份包含有效用量。",
        f"- 输入 Token {input_tokens:,}；缓存命中 {cached_tokens:,}（{cache_percent}）；非缓存差额 {uncached_tokens:,}；输出 Token {output_tokens:,}。",
    ]
    observed_category_characters = sum(categories.values())
    if request_call_count and observed_category_characters:
        rows = _category_rows(categories)
        top = max(rows, key=lambda row: row[1])
        lines.append(f"- 已保存请求结构中最大类别为 {_LABELS[top[0]]}（{top[2]:.1%}）；这是字符代理样本，不是精确 Token 账单。")
    elif request_call_count:
        lines.append("- 已保存请求结构为空或没有可分类内容，不能判断系统提示、Skill、消息或工具结果哪个占主要部分。")
    else:
        lines.append("- 当前没有已保存请求结构，不能判断系统提示、Skill、消息或工具结果哪个占主要部分。")
    lines.extend([
        "", "## 覆盖率与可信度", "",
        "| 项目 | 数值 | 可信度 | 含义 |",
        "|---|---:|---|---|",
        f"| 有用量记录的会话 | {session_count} | 精确 | 按用量记录中的会话标识分组；标识缺失时按文件分别统计 |",
        f"| 会话记录文件 | {rollout_count} | 精确 | 当前范围内的本地文件数，同一会话可能有多份文件 |",
        f"| 含有效用量的文件 | {readable_trace_count} | 精确 | 可汇总输入、输出和缓存字段 |",
        f"| 完整请求样本文件 / 会话记录文件 | {coverage} | 精确 | 两类文件数量对照，不等于已匹配会话覆盖率 |",
        f"| 有完整请求结构的调用 | {request_call_count} | 精确 | 类别分析样本 |",
        "| 上下文类别结论 | 样本级 | 有限 | 已保存内容分类不代表完整请求占比 |",
        "", "## 精确 Token 总览", "",
        "| 指标 | Token | 说明 |",
        "|---|---:|---|",
        f"| 输入 Token | {input_tokens:,} | 本地 `token_usage_record` 汇总 |",
        f"| 输出 Token | {output_tokens:,} | 本地 `token_usage_record` 汇总 |",
        f"| 缓存读取 Token | {cached_tokens:,} | 本地记录字段 |",
        f"| 非缓存输入差额 | {uncached_tokens:,} | 输入减缓存；不等于订阅扣减或美元成本 |",
        "", "> 本地记录不能推出订阅额度、真实计费或服务端压缩细节。", "",
        "## 上下文构成", "",
    ])
    if request_call_count and observed_category_characters:
        lines.extend([
            "| 类别 | 占已观测请求结构 | 结论 | 可做动作 |",
            "|---|---:|---|---|",
        ])
        for key, _, share, action in _category_rows(categories):
            share_text = f"{share:.1%}" if share is not None else "不可得"
            lines.append(f"| {_LABELS[key]} | {share_text} | 请求结构字符代理 | {action} |")
    else:
        lines.append("完整请求级类别表不可得；下方仍可统计已保存内容的分类，不将它冒充完整请求上下文占比。")
    lines.extend([
        "", "## 工具结果细分", "",
        "当前本地导出尚未保存工具调用名称与结果的一对一归因表；不能诚实地拆成命令、文件、网页或子代理类别。",
        "", "## 消耗排行", "",
        "当前聊天窗导出只保证 Token 汇总，不保证每次调用都带可关联的时间、会话和用户回合字段；对应排行在字段缺失时不可得。",
        "", "## 可执行优化建议", "",
        "1. 若后续请求结构显示工具结果占比最高，优先采用“结论 + 文件引用 + 可展开详情”，不要先删 Skill。",
        "2. 对已完成阶段生成短状态摘要，避免整段历史工具输出反复进入后续请求。",
        "3. 仅在类别覆盖充分时再缩短静态指令、Skill 或工具 schema。",
        "", "## 数据缺口", "",
    ])
    if request_call_count and observed_category_characters:
        lines.append(f"- 完整请求样本文件 / 会话记录文件为 {coverage}；类别比例只代表已保存请求样本，不能推广到全部会话。")
    elif request_call_count:
        lines.append(f"- 完整请求样本文件 / 会话记录文件为 {coverage}，但请求内容为空或没有可分类字段，无法做请求级来源归因。")
    else:
        lines.append("- 已提供本地保存内容的分类与 Token 估算；缺少逐次完整请求，因此无法计算这些内容在实际请求中被重复携带多少次、分别贡献多少输入 Token。补齐需要额外采集逐次请求，无法仅靠现有历史记录恢复。")
    lines.extend([
        "- 已拆分可识别的 Skill 与 AGENTS 显式区块；未标注来源、混合存储或未记录的指令仍不能精确拆分。需要带来源标记的指令采集才能补齐。",
        "- 账户额度可由官方 /status 或 account/rateLimits/read 查询；account/usage/read 在支持时可返回会话估算积分/金额，空值不是零，也不是实际账单。本报告未自动查询账户接口。",
        "- 官方 contextCompaction 事件可表明发生过压缩，但不提供服务端内部压缩明细；不能用本地 Token 数反推这些细节。",
    ])
    return "\n".join(lines)


def analyze(scope_expression: str | None, focus: str | None, sessions_root: Path, current_session_id: str | None, now: datetime) -> dict[str, object]:
    selected_focus = (focus or "full").lower()
    if selected_focus not in {"full", "context", "tools"}:
        raise AnalysisError("focus 必须是 full、context 或 tools。")
    scope = _scope(scope_expression, current_session_id, now)
    rollouts = _rollouts(sessions_root)
    if scope[0] == 'session':
        rollouts = _select(rollouts, scope)
    exports = _exports(sessions_root)
    if scope[0] == 'session':
        exports = _select(exports, scope)
    contents = defaultdict(list)
    totals, groups, tool_rows, observed, readable, missing_time, malformed = inspect_records(rollouts, scope, contents)
    input_tokens = totals['input_tokens']
    output_tokens = totals['output_tokens']
    cached_tokens = totals['cached_input_tokens']
    uncached_tokens = max(0, input_tokens - cached_tokens)
    cache_ratio = round(cached_tokens / input_tokens, 6) if input_tokens else None
    calls, categories = _context(exports, scope)
    sample_count, sample_contents = inspect_requests(exports, scope)
    export_count = sum(_context([path], scope)[0] > 0 for path in exports)
    diagnostic = _diagnostic_markdown(
        scope_description=scope[1],
        rollout_count=observed,
        readable_trace_count=readable,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        uncached_tokens=uncached_tokens,
        cache_ratio=cache_ratio,
        request_call_count=calls,
        export_rollout_count=export_count,
        categories=categories,
        session_count=len(groups['会话']),
    )
    start = diagnostic.index('## 工具结果细分')
    end = diagnostic.index('## 可执行优化建议')
    samples = render_content(sample_contents, request_count=sample_count) + '\n' if sample_count and sample_contents else ''
    diagnostic = diagnostic[:start] + samples + render_content(contents) + '\n' + render_details(groups, tool_rows, missing_time, malformed, load_titles(sessions_root.parent)) + '\n' + diagnostic[end:]
    return {"summary": diagnostic}
