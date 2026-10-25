"""基于脱敏报告生成保守建议，不读取原始 rollout。"""

from __future__ import annotations

from typing import Mapping

from .models import Confidence, EvidenceKind, Finding


_ACTIONS = {
    "large_tool_output": "限制单条工具输出，并优先检索必要片段。",
    "repeated_tool": "复用已读取的结果，避免重复读取相同信息。",
    "context_growth": "将已完成子任务摘要化，再继续后续步骤。",
    "failed_retry": "先定位失败原因，再决定是否重试。",
    "attribution_gap": "先补齐用量记录或确认未知事件含义。",
}


def recommend(report: Mapping[str, object]) -> list[Finding]:
    facts = report.get("facts", {})
    raw_findings = facts.get("findings", []) if isinstance(facts, Mapping) else []
    recommendations: list[Finding] = []
    for item in raw_findings if isinstance(raw_findings, list) else []:
        if not isinstance(item, Mapping):
            continue
        rule_id = item.get("rule_id")
        lines = item.get("evidence_lines")
        if not isinstance(rule_id, str) or rule_id not in _ACTIONS or not isinstance(lines, list) or not all(isinstance(line, int) for line in lines):
            continue
        recommendations.append(Finding(
            rule_id=f"recommend_{rule_id}", severity=str(item.get("severity", "low")), summary=_ACTIONS[rule_id],
            evidence_lines=tuple(lines), impact=dict(item.get("impact", {})) if isinstance(item.get("impact"), Mapping) else {},
            confidence=Confidence(str(item.get("confidence", "low"))) if str(item.get("confidence", "low")) in {value.value for value in Confidence} else Confidence.LOW,
            data_gaps=tuple(item.get("data_gaps", [])) if isinstance(item.get("data_gaps", []), list) else (), evidence_kind=EvidenceKind.RECOMMENDATION,
        ))
    return recommendations
