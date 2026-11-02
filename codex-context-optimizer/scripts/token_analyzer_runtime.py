"""随插件分发的 Token 分析运行时，不依赖仓库外的 Python 包。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


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


def _context(paths: Iterable[Path]) -> tuple[int, Counter[str]]:
    counts: Counter[str] = Counter({key: 0 for key in _CATEGORIES})
    calls = 0
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            request = record.get("request") if isinstance(record, dict) else None
            if not isinstance(request, dict):
                continue
            calls += 1
            if "instructions" in request:
                counts["static_instructions"] += len(json.dumps(request["instructions"], ensure_ascii=False, sort_keys=True))
            if "persisted_dynamic_tools" in request:
                counts["tool_definitions"] += len(json.dumps(request["persisted_dynamic_tools"], ensure_ascii=False, sort_keys=True))
            items = request.get("input", [])
            for item in items if isinstance(items, list) else [items]:
                if isinstance(item, dict):
                    counts[_category(item)] += len(json.dumps(item, ensure_ascii=False, sort_keys=True))
                else:
                    counts["unattributable"] += len(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return calls, counts


def analyze(scope_expression: str | None, focus: str | None, sessions_root: Path, output_dir: Path, current_session_id: str | None, now: datetime) -> dict[str, object]:
    selected_focus = (focus or "full").lower()
    if selected_focus not in {"full", "context", "tools"}:
        raise AnalysisError("focus 必须是 full、context 或 tools。")
    scope = _scope(scope_expression, current_session_id, now)
    rollouts = _select(_rollouts(sessions_root), scope)
    exports = _select(_exports(sessions_root), scope)
    metadata = {"kind": scope[0], "description": scope[1], "matched_rollout_count": len(rollouts)}
    if selected_focus == "context":
        calls, categories = _context(exports)
        total = sum(categories.values())
        top = max(categories, key=categories.get, default="unattributable")
        report: dict[str, object] = {"analysis_scope": metadata, "scope": {"saved_request_call_count": calls, "measurement": "请求结构字符代理；不是精确 Token 账单"}, "top_category": {"category": top, "label": _LABELS[top], "share": round(categories[top] / total, 6) if total else None}, "categories": dict(categories)}
        summary = f"上下文构成：范围={scope[1]}；匹配 rollout={len(rollouts)}；最大类别={_LABELS[top]}。"
    else:
        input_tokens, output_tokens, cached_tokens, readable = _usage(rollouts)
        report = {"analysis_scope": metadata, "facts": {"trace_count": readable, "input_tokens": input_tokens, "output_tokens": output_tokens, "cached_input_tokens": cached_tokens}}
        label = "工具相关 Token 概览" if selected_focus == "tools" else "Token 概览"
        summary = f"{label}：范围={scope[1]}；匹配 rollout={len(rollouts)}；可读 trace={readable}；输入 Token={input_tokens}。"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"codex-token-{selected_focus}-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "report_path": str(destination)}
