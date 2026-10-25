"""以容错方式将不稳定的 Codex rollout JSONL 映射到内部模型。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .models import CanonicalEvent, Confidence, DataGap, RunTrace, TokenUsage
from .privacy import sanitize_identifier


KNOWN_EVENT_TYPES = {
    "session_meta",
    "turn_context",
    "event_msg",
    "response_item",
    "token_usage_record",
}
SUPPORTED_SUFFIXES = {".jsonl"}


def discover_rollouts(root: Path) -> list[Path]:
    """列出可分析及需要明确降级说明的 rollout 文件。"""
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and (path.suffix in SUPPORTED_SUFFIXES or path.suffix in {".zst", ".gz", ".zip"})
    )


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = record.get("payload", record.get("data", {}))
    return payload if isinstance(payload, Mapping) else {}


def _usage(payload: Mapping[str, Any]) -> TokenUsage:
    source = payload.get("usage", payload)
    if not isinstance(source, Mapping):
        source = {}

    def integer(name: str) -> int | None:
        value = source.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return TokenUsage(
        input_tokens=integer("input_tokens"),
        output_tokens=integer("output_tokens"),
        cached_input_tokens=integer("cached_input_tokens"),
        total_tokens=integer("total_tokens"),
    )


def _payload_chars(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def parse_rollout(path: Path, *, identifier_salt: str) -> RunTrace:
    """解析一个文件；任一行的问题均降级为缺口而非终止整个文件。"""
    source_label = path.name
    if path.suffix not in SUPPORTED_SUFFIXES:
        return RunTrace(
            trace_id=sanitize_identifier(source_label, identifier_salt),
            data_gaps=(DataGap("unsupported_compression", "不支持压缩 rollout", ()),),
        )

    events: list[CanonicalEvent] = []
    gaps: list[DataGap] = []
    usage = TokenUsage()
    session_id: str | None = None
    model: str | None = None
    turn_id: str | None = None
    cwd: str | None = None
    seen_records: set[str] = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return RunTrace(
            trace_id=sanitize_identifier(source_label, identifier_salt),
            data_gaps=(DataGap("unreadable_rollout", "rollout 无法读取", ()),),
        )

    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            gaps.append(DataGap("invalid_json", "存在无法解析的 JSON 行", (line_number,)))
            continue
        if not isinstance(record, Mapping):
            gaps.append(DataGap("invalid_event", "事件不是对象", (line_number,)))
            continue

        event_kind = record.get("type", record.get("event_type"))
        if not isinstance(event_kind, str) or event_kind not in KNOWN_EVENT_TYPES:
            gaps.append(DataGap("unknown_event", "存在未知事件类型", (line_number,)))
            continue

        fingerprint = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen_records:
            gaps.append(DataGap("duplicate_event", "已跳过重复事件", (line_number,), Confidence.MEDIUM))
            continue
        seen_records.add(fingerprint)

        payload = _payload(record)
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            timestamp = None
        elif not (timestamp.endswith("Z") or "+" in timestamp[10:] or "-" in timestamp[10:]):
            gaps.append(DataGap("missing_timezone", "时间戳缺少时区", (line_number,)))

        if event_kind == "session_meta":
            raw_session = payload.get("id", payload.get("session_id"))
            if isinstance(raw_session, str):
                session_id = sanitize_identifier(raw_session, identifier_salt)
            model = payload.get("model") if isinstance(payload.get("model"), str) else model
            cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else cwd
        elif event_kind == "turn_context":
            raw_turn = payload.get("turn_id", payload.get("id"))
            if isinstance(raw_turn, (str, int)):
                turn_id = str(raw_turn)
        elif event_kind == "token_usage_record":
            usage = _usage(payload)

        tool_name = payload.get("name") if event_kind == "response_item" and isinstance(payload.get("name"), str) else None
        events.append(CanonicalEvent(
            event_kind=event_kind,
            timestamp=timestamp,
            session_id=session_id,
            turn_id=turn_id,
            cwd=cwd,
            model=model,
            usage=usage if event_kind == "token_usage_record" else None,
            tool_name=tool_name,
            payload_chars=_payload_chars(payload),
            source_path=source_label,
            source_line=line_number,
            confidence=Confidence.HIGH if event_kind == "token_usage_record" else Confidence.MEDIUM,
        ))

    if usage.input_tokens is None and not any(gap.code == "missing_input_usage" for gap in gaps):
        gaps.append(DataGap("missing_input_usage", "输入 Token 不可得", ()))
    trace_id = session_id or sanitize_identifier(source_label, identifier_salt)
    return RunTrace(trace_id=trace_id, events=tuple(events), token_usage=usage, data_gaps=tuple(gaps), model=model)
