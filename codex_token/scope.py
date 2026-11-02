"""本地、可审计的 Codex 分析范围解析与 rollout 筛选。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal
from zoneinfo import ZoneInfo


ScopeKind = Literal["session", "time", "all"]
SHANGHAI = ZoneInfo("Asia/Shanghai")
_RECENT_HOURS = re.compile(r"^最近\s*(\d+)\s*小时$")
_ABSOLUTE_RANGE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})\s*(?:至|到|[-—–])\s*(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})$"
)
_ROLLOUT_START = re.compile(r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})")


class ScopeError(ValueError):
    """用户表达不能安全映射为本地分析范围。"""


@dataclass(frozen=True)
class AnalysisScope:
    kind: ScopeKind
    description: str
    session_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None


def _shanghai_range(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _parse_absolute_range(expression: str) -> tuple[datetime, datetime] | None:
    match = _ABSOLUTE_RANGE.match(expression)
    if not match:
        return None
    try:
        start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
        end = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
    except ValueError as error:
        raise ScopeError("时间格式无效，请使用 YYYY-MM-DD HH:MM 至 YYYY-MM-DD HH:MM。") from error
    if end <= start:
        raise ScopeError("结束时间必须晚于开始时间。")
    return _shanghai_range(start, end)


def resolve_scope(expression: str | None, *, session_id: str | None, now: datetime) -> AnalysisScope:
    """解析明确中文范围；缺省时仅允许当前会话。"""
    normalized = expression.strip() if expression else None
    if not normalized or normalized in {"当前会话", "这个会话"}:
        if not session_id:
            raise ScopeError("无法取得当前会话标识；请传入 --session，不能自动扫描历史。")
        return AnalysisScope("session", "当前会话", session_id=session_id)
    if normalized == "全部历史":
        return AnalysisScope("all", "用户明确要求全部历史")

    absolute_range = _parse_absolute_range(normalized)
    if absolute_range:
        return AnalysisScope("time", f"用户指定时间：{normalized}", start=absolute_range[0], end=absolute_range[1])

    current_local = now.astimezone(SHANGHAI)
    if normalized == "今天":
        start = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc, end_utc = _shanghai_range(start, current_local)
        return AnalysisScope("time", "今天（上海时间）", start=start_utc, end=end_utc)
    if normalized == "昨天":
        end = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        start_utc, end_utc = _shanghai_range(start, end)
        return AnalysisScope("time", "昨天（上海时间）", start=start_utc, end=end_utc)

    recent_match = _RECENT_HOURS.match(normalized)
    if recent_match:
        hours = int(recent_match.group(1))
        if hours <= 0:
            raise ScopeError("最近小时数必须为正数。")
        return AnalysisScope("time", f"最近 {hours} 小时", start=now - timedelta(hours=hours), end=now)
    raise ScopeError("无法确定分析范围；请使用“最近 2 小时”“今天”“昨天”“当前会话”或“全部历史”。")


def _rollout_start(path: Path) -> datetime | None:
    match = _ROLLOUT_START.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def filter_rollout_paths(paths: Iterable[Path], scope: AnalysisScope) -> list[Path]:
    """按文件名中的会话标识或 UTC rollout 起始时间筛选，无匹配不会扩大范围。"""
    if scope.kind == "all":
        return list(paths)
    if scope.kind == "session":
        return [path for path in paths if scope.session_id and scope.session_id in path.name]
    if scope.start is None or scope.end is None:
        return []
    return [
        path for path in paths
        if (start := _rollout_start(path)) is not None and scope.start <= start < scope.end
    ]
