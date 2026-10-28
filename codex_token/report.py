"""仅从统一模型生成脱敏证据报告。"""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from .models import Finding, RunTrace


def _gap(code: str, summary: str) -> dict[str, object]:
    return {"code": code, "summary": summary, "evidence_lines": [], "confidence": "low"}


def _finding(finding: Finding) -> dict[str, object]:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "summary": finding.summary,
        "evidence_lines": list(finding.evidence_lines),
        "impact": dict(finding.impact),
        "confidence": finding.confidence.value,
        "data_gaps": list(finding.data_gaps),
    }


def _trace_signature(trace: RunTrace) -> tuple[object, ...]:
    """跨追加文件识别同一组已解析事件；不使用文件路径或行号。"""
    return tuple(
        (event.event_kind, event.timestamp, event.session_id, event.turn_id, event.model,
         event.tool_name, event.payload_chars, event.usage)
        for event in trace.events
    )


def build_report(traces: Sequence[RunTrace], findings: Sequence[Finding]) -> dict[str, object]:
    """构建固定五区段报告，不接受也不回显原始转录内容。"""
    unavailable_traces = [trace for trace in traces if not trace.events]
    unique_traces: list[RunTrace] = []
    seen_signatures: set[tuple[object, ...]] = set()
    by_trace_id: dict[str, RunTrace] = {}
    for trace in traces:
        if not trace.events:
            continue
        signature = _trace_signature(trace)
        if signature and signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        previous = by_trace_id.get(trace.trace_id)
        if previous is None or len(trace.events) > len(previous.events):
            by_trace_id[trace.trace_id] = trace
    unique_traces = list(by_trace_id.values())
    gaps = [asdict(gap) for trace in unique_traces + unavailable_traces for gap in trace.data_gaps]
    safe_gaps = [
        {**gap, "confidence": gap["confidence"].value, "evidence_lines": list(gap["evidence_lines"])}
        for gap in gaps
    ]
    if not unique_traces:
        safe_gaps.append(_gap("no_traces", "没有可分析的 rollout"))
    models = sorted({trace.model for trace in unique_traces if trace.model})
    model_labels = {model: f"model_{hashlib.sha256(model.encode('utf-8')).hexdigest()[:12]}" for model in models}
    if models:
        safe_gaps.extend(_gap("unpriced_model", "模型没有匹配的价格表") for model in models)

    facts = {
        "trace_count": len(unique_traces),
        "unavailable_trace_count": len(unavailable_traces),
        "model_count": len(models),
        "traces": [
            {
                "trace_id": trace.trace_id,
                "event_count": len(trace.events),
                "model": model_labels.get(trace.model) if trace.model else None,
                "token_usage": {
                    "input_tokens": trace.token_usage.input_tokens,
                    "output_tokens": trace.token_usage.output_tokens,
                    "cached_input_tokens": trace.token_usage.cached_input_tokens,
                    "total_tokens": trace.token_usage.total_tokens,
                },
            }
            for trace in unique_traces
        ],
        "findings": [_finding(finding) for finding in findings if finding.evidence_kind.value == "fact"],
    }
    seen_usage_events: set[tuple[object, ...]] = set()
    usage_events = []
    for trace in traces:
        for event in trace.events:
            if event.event_kind != "token_usage_record" or event.usage is None:
                continue
            key = (event.session_id, event.timestamp, event.turn_id, event.usage)
            if key not in seen_usage_events:
                seen_usage_events.add(key)
                usage_events.append(event.usage)
    known_input = [usage.input_tokens for usage in usage_events if usage.input_tokens is not None]
    known_total = [usage.total_tokens for usage in usage_events if usage.total_tokens is not None]
    derived_metrics = {
        "known_input_tokens": sum(known_input) if known_input else None,
        "known_total_tokens": sum(known_total) if known_total else None,
        "traces_with_known_input": len(known_input),
    }
    return {
        "facts": facts,
        "derived_metrics": derived_metrics,
        "estimates": [],
        "recommendations": [_finding(finding) for finding in findings if finding.evidence_kind.value == "recommendation"],
        "data_gaps": safe_gaps,
    }


def write_json_report(path: Path, report: Mapping[str, object]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown_report(report: Mapping[str, object]) -> str:
    sections = (
        ("事实", "facts"),
        ("派生指标", "derived_metrics"),
        ("估算", "estimates"),
        ("建议", "recommendations"),
        ("数据缺口", "data_gaps"),
    )
    lines = ["# Codex Token 证据报告", ""]
    for title, key in sections:
        lines.extend((f"## {title}", "", "```json", json.dumps(report.get(key, []), ensure_ascii=False, indent=2), "```", ""))
    return "\n".join(lines)
