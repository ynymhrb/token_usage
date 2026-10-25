"""可解释的只读诊断规则。"""

from __future__ import annotations

from .models import Confidence, Finding, RunTrace


def _finding(rule_id: str, severity: str, summary: str, lines: list[int], impact: dict[str, int | float | str | None], confidence: Confidence = Confidence.MEDIUM) -> Finding:
    return Finding(rule_id, severity, summary, tuple(lines), impact, confidence)


def detect(trace: RunTrace) -> list[Finding]:
    findings: list[Finding] = []
    tools = [event for event in trace.events if event.event_kind == "response_item"]
    large = [event for event in tools if (event.payload_chars or 0) >= 8_000]
    if large:
        findings.append(_finding("large_tool_output", "medium", "单条工具输出异常大", [event.source_line for event in large], {"threshold_chars": 8000, "count": len(large)}))
    repeated = {name: [event for event in tools if event.tool_name == name] for name in {event.tool_name for event in tools if event.tool_name}}
    for name, events in repeated.items():
        same_size = len({event.payload_chars for event in events}) == 1
        if len(events) > 1 and same_size:
            findings.append(_finding("repeated_tool", "low", "同一工具出现相同大小的重复结果，仍需人工确认", [event.source_line for event in events], {"count": len(events), "matching_result_sizes": True}, Confidence.LOW))
            break
    inputs = [(event.source_line, event.usage.input_tokens) for event in trace.events if event.usage and event.usage.input_tokens is not None]
    if len(inputs) >= 2 and inputs[-1][1] >= inputs[0][1] * 2:
        findings.append(_finding("context_growth", "medium", "输入 Token 出现持续增长拐点", [inputs[0][0], inputs[-1][0]], {"start_input_tokens": inputs[0][1], "end_input_tokens": inputs[-1][1]}))
    retries = [event for event in trace.events if event.tool_name and ("retry" in event.tool_name or "error" in event.tool_name or "fail" in event.tool_name)]
    if retries:
        findings.append(_finding("failed_retry", "medium", "存在失败或重试回合", [event.source_line for event in retries], {"count": len(retries)}))
    unknown = [gap for gap in trace.data_gaps if gap.code in {"unknown_event", "missing_input_usage", "invalid_json"}]
    if unknown:
        lines = [line for gap in unknown for line in gap.evidence_lines] or [event.source_line for event in trace.events[-1:]]
        findings.append(_finding("attribution_gap", "low", "存在无法可靠归因的事件或用量", lines, {"gap_count": len(unknown)}, Confidence.LOW))
    return findings
