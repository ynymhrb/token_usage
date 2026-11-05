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
from report_advice import render_advice
from history_replay import render_replay, replay_history


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
    natural_day = re.fullmatch(r'(?:请)?(?:帮我)?(?:分析|统计|查看)?\s*(今天|昨天)(?:的)?(?:全部|所有)?(?:会话)?(?:的)?(?:\s*token)?(?:消耗|用量)?', value or '', re.I)
    if natural_day:
        value = natural_day.group(1)
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
        "## 用量总结", "",
        f"- 分析范围：{scope_description}；{session_count} 个有用量记录的会话。",
        f"- 累计输入：{input_tokens:,} Token；其中缓存输入 {cached_tokens:,}（{cache_percent}），非缓存输入 {uncached_tokens:,}。",
        f"- 输出总计：{output_tokens:,} Token。",
        "- 下方输入内容分类按多次请求中携带的内容累计估算。",
    ]
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
    sample_count, sample_contents = inspect_requests(exports, scope)
    diagnostic = _diagnostic_markdown(
        scope_description=scope[1],
        rollout_count=observed,
        readable_trace_count=readable,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        uncached_tokens=uncached_tokens,
        cache_ratio=cache_ratio,
        request_call_count=sample_count,
        export_rollout_count=0,
        categories=Counter(),
        session_count=len(groups['会话']),
    )
    titles = load_titles(sessions_root.parent)
    replayed = replay_history(rollouts, scope)
    replay = render_replay(rollouts, scope, titles, replayed=replayed, official_input=input_tokens)
    cumulative_tools = {}
    for row in replayed[3].values():
        entry = cumulative_tools.setdefault(row['category'], {'tokens': 0, 'calls': 0, 'largest_tokens': 0})
        entry['tokens'] += row['tokens'] * row['occurrences']
        entry['calls'] += row['occurrences']
        entry['largest_tokens'] = max(entry['largest_tokens'], row['tokens'])
    details = render_details(groups, cumulative_tools, missing_time, malformed, titles, cumulative=True)
    tools_detail, distribution = details.split('## 消耗分布', 1)
    if not replayed[0]['requests']:
        tools_detail = ''
    advice = render_advice(contents, tool_rows, replayed=replayed) if replayed[0]['requests'] else ''
    diagnostic = (diagnostic + '\n\n' + replay + '\n' + tools_detail
                  + '\n## 消耗分布' + distribution + '\n' + advice)
    return {"summary": diagnostic}
