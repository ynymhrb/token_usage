"""Codex 转录的稳定内部数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class EvidenceKind(str, Enum):
    FACT = "fact"
    DERIVED = "derived_metric"
    ESTIMATE = "estimate"
    RECOMMENDATION = "recommendation"
    DATA_GAP = "data_gap"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class DataGap:
    code: str
    summary: str
    evidence_lines: tuple[int, ...] = ()
    confidence: Confidence = Confidence.LOW


@dataclass(frozen=True)
class CanonicalEvent:
    event_kind: str
    timestamp: str | None
    session_id: str | None
    turn_id: str | None
    cwd: str | None
    model: str | None
    usage: TokenUsage | None
    tool_name: str | None
    payload_chars: int | None
    source_path: str
    source_line: int
    confidence: Confidence


@dataclass(frozen=True)
class RunTrace:
    trace_id: str
    events: tuple[CanonicalEvent, ...] = ()
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    data_gaps: tuple[DataGap, ...] = ()
    model: str | None = None
    source_version: str | None = None


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    summary: str
    evidence_lines: tuple[int, ...]
    impact: Mapping[str, int | float | str | None]
    confidence: Confidence
    data_gaps: tuple[str, ...] = ()
    evidence_kind: EvidenceKind = EvidenceKind.FACT

