"""仅从统一模型生成脱敏证据报告。"""

from __future__ import annotations

import json
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


def build_report(traces: Sequence[RunTrace], findings: Sequence[Finding]) -> dict[str, object]:
    """构建固定五区段报告，不接受也不回显原始转录内容。"""
    gaps = [asdict(gap) for trace in traces for gap in trace.data_gaps]
    safe_gaps = [
        {**gap, "confidence": gap["confidence"].value, "evidence_lines": list(gap["evidence_lines"])}
        for gap in gaps
    ]
    if not traces:
        safe_gaps.append(_gap("no_traces", "没有可分析的 rollout"))
    models = sorted({trace.model for trace in traces if trace.model})
    if models:
        safe_gaps.extend(_gap("unpriced_model", f"模型 {model} 没有匹配的价格表") for model in models)

    facts = {
        "trace_count": len(traces),
        "models": models,
        "traces": [
            {
                "trace_id": trace.trace_id,
                "event_count": len(trace.events),
                "model": trace.model,
                "token_usage": {
                    "input_tokens": trace.token_usage.input_tokens,
                    "output_tokens": trace.token_usage.output_tokens,
                    "cached_input_tokens": trace.token_usage.cached_input_tokens,
                    "total_tokens": trace.token_usage.total_tokens,
                },
            }
            for trace in traces
        ],
        "findings": [_finding(finding) for finding in findings if finding.evidence_kind.value == "fact"],
    }
    known_input = [trace.token_usage.input_tokens for trace in traces if trace.token_usage.input_tokens is not None]
    known_total = [trace.token_usage.total_tokens for trace in traces if trace.token_usage.total_tokens is not None]
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
