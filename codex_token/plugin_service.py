"""聊天插件调用的本地、无模型 Token 分析服务。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .adapter import discover_rollouts, parse_rollout
from .context_attribution import analyze_context_exports, context_report, discover_context_exports
from .detectors import detect
from .recommendations import recommend
from .report import build_report, write_json_report
from .scope import ScopeError, filter_rollout_paths, resolve_scope


Focus = Literal["full", "context", "tools"]
VALID_FOCUSES: frozenset[str] = frozenset({"full", "context", "tools"})


class PluginAnalysisError(ValueError):
    """用户请求不能安全地在本地分析。"""


def _scope_metadata(scope: object, matched_rollout_count: int) -> dict[str, object]:
    return {
        "kind": scope.kind,
        "description": scope.description,
        "start": scope.start.isoformat() if scope.start else None,
        "end": scope.end.isoformat() if scope.end else None,
        "matched_rollout_count": matched_rollout_count,
        "time_measurement": "rollout 文件名中的 UTC 起始时间" if scope.kind == "time" else None,
    }


def _report_path(output_dir: Path, focus: Focus, now: datetime) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return output_dir / f"codex-token-{focus}-{timestamp}.json"


def _summary_for_full(report: dict[str, object], scope_metadata: dict[str, object], focus: Focus) -> str:
    facts = report.get("facts", {})
    derived = report.get("derived_metrics", {})
    input_tokens = derived.get("known_input_tokens", 0) if isinstance(derived, dict) else 0
    traces = facts.get("trace_count", 0) if isinstance(facts, dict) else 0
    label = "工具相关 Token 概览" if focus == "tools" else "Token 概览"
    return (
        f"{label}：范围={scope_metadata['description']}；匹配 rollout={scope_metadata['matched_rollout_count']}；"
        f"可读 trace={traces}；已知输入 Token={input_tokens}。"
    )


def _summary_for_context(report: dict[str, object], scope_metadata: dict[str, object]) -> str:
    categories = report.get("categories", [])
    top = max(categories, key=lambda item: item.get("share_of_observed_request_chars") or 0, default=None)
    if not top:
        return f"上下文构成：范围={scope_metadata['description']}；没有可归因的请求结构。"
    share = top.get("share_of_observed_request_chars") or 0
    return (
        f"上下文构成：范围={scope_metadata['description']}；匹配 rollout={scope_metadata['matched_rollout_count']}；"
        f"最大类别={top['label']}（观察到请求字符的 {share:.1%}）。"
    )


def run_plugin_analysis(
    scope: str | None,
    focus: str | None,
    sessions_root: Path,
    output_dir: Path,
    current_session_id: str | None,
    now: datetime,
) -> dict[str, object]:
    """运行单次脱敏本地分析，返回聊天窗可用的紧凑摘要与报告路径。"""
    selected_focus = (focus or "full").strip().lower()
    if selected_focus not in VALID_FOCUSES:
        raise PluginAnalysisError("focus 必须是 full、context 或 tools。")
    try:
        selected_scope = resolve_scope(scope, session_id=current_session_id, now=now)
    except ScopeError as error:
        raise PluginAnalysisError(str(error)) from error

    rollout_paths = filter_rollout_paths(discover_rollouts(sessions_root), selected_scope)
    export_paths = filter_rollout_paths(discover_context_exports(sessions_root), selected_scope)
    scope_metadata = _scope_metadata(selected_scope, len(rollout_paths))
    destination = _report_path(output_dir, selected_focus, now)

    if selected_focus == "context":
        report = context_report(analyze_context_exports(
            sessions_root,
            export_paths=export_paths,
            rollout_paths=rollout_paths,
        ))
        report["analysis_scope"] = scope_metadata
        summary = _summary_for_context(report, scope_metadata)
    else:
        salt = os.environ.get("CODEX_TOKEN_SALT", "codex-token-local")
        traces = [parse_rollout(path, identifier_salt=salt) for path in rollout_paths]
        findings = [finding for trace in traces for finding in detect(trace)]
        report = build_report(traces, findings)
        report["analysis_scope"] = scope_metadata
        report["recommendations"] = [{
            "rule_id": item.rule_id,
            "severity": item.severity,
            "summary": item.summary,
            "evidence_lines": list(item.evidence_lines),
            "impact": dict(item.impact),
            "confidence": item.confidence.value,
            "data_gaps": list(item.data_gaps),
        } for item in recommend(report)]
        summary = _summary_for_full(report, scope_metadata, selected_focus)

    write_json_report(destination, report)
    return {
        "focus": selected_focus,
        "summary": summary,
        "report_path": str(destination),
        "matched_rollout_count": len(rollout_paths),
    }
