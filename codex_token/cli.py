"""本地只读命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .adapter import discover_rollouts, parse_rollout
from .context_attribution import analyze_context_exports, context_report
from .detectors import detect
from .recommendations import recommend
from .report import build_report, render_markdown_report, write_json_report
from .experiments import RunResult, compare_runs, load_manifest, plan_manifest


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


def _context_breakdown(args: argparse.Namespace) -> int:
    report = context_report(analyze_context_exports(Path(args.sessions_root)))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        write_json_report(Path(args.output), report)
    print(rendered)
    return 0


def _experiment_plan(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(plan_manifest(load_manifest(Path(args.manifest))), ensure_ascii=False, indent=2))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


def _experiment_run(args: argparse.Namespace) -> int:
    if not args.execute:
        print("真实执行需要显式 --execute；当前仅允许 experiment plan。", file=sys.stderr)
        return 2
    if args.budget_usd <= 0:
        print("--budget-usd 必须为正数。", file=sys.stderr)
        return 2
    # 首版没有随清单版本化的模型价格表；不能把 Token 上限伪装为美元上限。
    print("当前清单没有匹配的价格表，无法安全执行美元预算实验。", file=sys.stderr)
    return 2


def _compare(args: argparse.Namespace) -> int:
    try:
        def load(path: str) -> list[RunResult]:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError
            return [RunResult(**row) for row in raw if isinstance(row, dict)]
        result = compare_runs(load(args.baseline), load(args.candidate), args.primary_metric)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        print("比较输入必须是 RunResult 数组 JSON。", file=sys.stderr)
        return 2
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-token", description="本地、脱敏的 Codex Token 诊断工具")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="只读分析本地 rollout")
    analyze.add_argument("--sessions-root", default=str(Path.home() / ".codex" / "sessions"))
    analyze.add_argument("--format", choices=("json", "md"), default="json")
    analyze.add_argument("--output")
    analyze.set_defaults(handler=_analyze)
    context_breakdown = commands.add_parser("context-breakdown", help="按保存请求结构分析上下文构成")
    context_breakdown.add_argument("--sessions-root", default=str(Path.home() / ".codex" / "sessions"))
    context_breakdown.add_argument("--output")
    context_breakdown.set_defaults(handler=_context_breakdown)
    recommend_parser = commands.add_parser("recommend", help="从脱敏报告生成建议")
    recommend_parser.add_argument("report")
    recommend_parser.set_defaults(handler=_recommend)
    compare = commands.add_parser("compare", help="比较保存的基线与候选运行结果")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    compare.add_argument("--primary-metric", choices=("input_tokens", "total_tokens", "wall_seconds"), default="input_tokens")
    compare.set_defaults(handler=_compare)
    experiment = commands.add_parser("experiment", help="管理受控实验")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    plan = experiment_commands.add_parser("plan", help="只生成实验计划，不执行")
    plan.add_argument("manifest")
    plan.set_defaults(handler=_experiment_plan)
    run = experiment_commands.add_parser("run", help="仅在显式确认后运行受控实验")
    run.add_argument("manifest")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--budget-usd", type=float, required=True)
    run.set_defaults(handler=_experiment_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
