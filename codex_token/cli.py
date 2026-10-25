"""本地只读命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .adapter import discover_rollouts, parse_rollout
from .detectors import detect
from .recommendations import recommend
from .report import build_report, render_markdown_report, write_json_report


def _finding_dict(finding: object) -> dict[str, object]:
    return {
        "rule_id": finding.rule_id, "severity": finding.severity, "summary": finding.summary,
        "evidence_lines": list(finding.evidence_lines), "impact": dict(finding.impact),
        "confidence": finding.confidence.value, "data_gaps": list(finding.data_gaps),
    }


def _analyze(args: argparse.Namespace) -> int:
    root = Path(args.sessions_root)
    salt = os.environ.get("CODEX_TOKEN_SALT", "codex-token-local")
    traces = [parse_rollout(path, identifier_salt=salt) for path in discover_rollouts(root)]
    findings = [finding for trace in traces for finding in detect(trace)]
    report = build_report(traces, findings)
    recommendations = recommend(report)
    report["recommendations"] = [_finding_dict(item) for item in recommendations]
    if args.output:
        write_json_report(Path(args.output), report)
    if args.format == "md":
        print(render_markdown_report(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _recommend(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    if report_path.suffix.lower() != ".json" or report_path.name.endswith(".jsonl"):
        print("recommend 只接受脱敏 JSON 报告，拒绝原始 JSONL。", file=sys.stderr)
        return 2
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("无法读取 JSON 报告。", file=sys.stderr)
        return 2
    print(json.dumps([_finding_dict(item) for item in recommend(report)], ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-token", description="本地、脱敏的 Codex Token 诊断工具")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="只读分析本地 rollout")
    analyze.add_argument("--sessions-root", default=str(Path.home() / ".codex" / "sessions"))
    analyze.add_argument("--format", choices=("json", "md"), default="json")
    analyze.add_argument("--output")
    analyze.set_defaults(handler=_analyze)
    recommend_parser = commands.add_parser("recommend", help="从脱敏报告生成建议")
    recommend_parser.add_argument("report")
    recommend_parser.set_defaults(handler=_recommend)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
