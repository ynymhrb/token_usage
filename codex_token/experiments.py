"""受控实验的清单校验、比较与执行边界。"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .adapter import discover_rollouts, parse_rollout

_EXECUTION_CAPABILITY = object()


@dataclass(frozen=True)
class ExperimentManifest:
    baseline_commit: str
    repository_root: Path
    tasks: tuple[dict[str, str], ...]
    candidate: dict[str, str]
    repetitions: int
    primary_metric: str
    token_budget: int


@dataclass(frozen=True)
class RunResult:
    task_passed: bool
    quality_passed: bool
    input_tokens: int | None
    total_tokens: int | None
    wall_seconds: float | None
    stop_reason: str | None = None
    variant: str = "candidate"


@dataclass(frozen=True)
class ComparisonResult:
    status: str
    baseline_quality_rate: float
    candidate_quality_rate: float
    primary_metric_change: float | None
    reason: str


def load_manifest(path: Path) -> ExperimentManifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("实验清单必须是可读取的 JSON") from error
    required = {"baseline_commit", "repository_root", "tasks", "candidate", "repetitions", "primary_metric", "token_budget"}
    if not isinstance(data, dict) or (missing := required - data.keys()):
        raise ValueError("实验清单缺少必需字段")
    repetitions = data["repetitions"]
    budget = data["token_budget"]
    if (not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 3
            or not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0
            or not isinstance(data["tasks"], list)
            or data["primary_metric"] not in {"input_tokens", "total_tokens", "wall_seconds"}):
        raise ValueError("实验清单的重复次数、任务或主指标无效")
    candidate = data["candidate"]
    if (not isinstance(candidate, dict) or candidate.get("kind") != "narrow_prompt"
            or not isinstance(candidate.get("instruction"), str) or not candidate["instruction"].strip()):
        raise ValueError("首版候选干预仅支持带非空 instruction 的 narrow_prompt")
    tasks = tuple(task for task in data["tasks"] if isinstance(task, dict) and all(key in task for key in ("id", "prompt", "verify")))
    if len(tasks) != len(data["tasks"]):
        raise ValueError("每个任务必须含 id、prompt 与 verify")
    if not isinstance(data["baseline_commit"], str) or not data["baseline_commit"].strip() or not isinstance(data["repository_root"], str) or not data["repository_root"].strip():
        raise ValueError("基线提交和仓库根目录必须是非空字符串")
    return ExperimentManifest(data["baseline_commit"], Path(data["repository_root"]), tasks, candidate, repetitions, str(data["primary_metric"]), budget)


def plan_manifest(manifest: ExperimentManifest) -> dict[str, object]:
    return {"execute": False, "baseline_commit": manifest.baseline_commit, "tasks": [task["id"] for task in manifest.tasks], "candidate": manifest.candidate, "repetitions": manifest.repetitions, "primary_metric": manifest.primary_metric, "token_budget": manifest.token_budget, "notice": "未执行；真实运行需要 --execute。"}


def _metric(run: RunResult, name: str) -> float | None:
    value = getattr(run, name)
    return float(value) if value is not None else None


def compare_runs(baseline: Sequence[RunResult], candidate: Sequence[RunResult], primary_metric: str = "input_tokens") -> ComparisonResult:
    if not baseline or not candidate:
        return ComparisonResult("uncertain", 0.0, 0.0, None, "缺少基线或候选结果")
    base_quality = sum(run.quality_passed for run in baseline) / len(baseline)
    candidate_quality = sum(run.quality_passed for run in candidate) / len(candidate)
    if candidate_quality < base_quality:
        return ComparisonResult("quality_regression", base_quality, candidate_quality, None, "候选质量验证通过率低于基线")
    values_base = [_metric(run, primary_metric) for run in baseline]
    values_candidate = [_metric(run, primary_metric) for run in candidate]
    if None in values_base or None in values_candidate:
        return ComparisonResult("uncertain", base_quality, candidate_quality, None, "主指标缺失")
    base_mean = sum(values_base) / len(values_base)
    candidate_mean = sum(values_candidate) / len(values_candidate)
    change = (candidate_mean - base_mean) / base_mean if base_mean else None
    directionally_better = len(candidate) >= 3 and all(value < base_mean for value in values_candidate)
    if change is not None and change <= -0.10 and directionally_better:
        return ComparisonResult("effective", base_quality, candidate_quality, change, "质量不低于基线且主指标改善至少 10%")
    if len(candidate) < 3:
        return ComparisonResult("uncertain", base_quality, candidate_quality, change, "重复次数少于 3")
    return ComparisonResult("ineffective", base_quality, candidate_quality, change, "未达到预先声明的有效改善门槛")


class CommandRunner:
    """真实执行器；只有显式执行门放行后才会被调用。"""
    def __init__(self, capability: object | None = None) -> None:
        self._capability = capability

    def is_git_repository(self, root: Path) -> bool:
        completed = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False)
        return completed.returncode == 0 and completed.stdout.strip() == "true"

    def create_worktree(self, root: Path, baseline_commit: str) -> Path:
        path = Path(tempfile.mkdtemp(prefix="codex-token-experiment-"))
        subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(path), baseline_commit], check=True, capture_output=True, text=True)
        return path

    def remove_worktree(self, root: Path, worktree: Path) -> None:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], check=False, capture_output=True, text=True)

    def run_candidate(self, worktree: Path, candidate: dict[str, str], task: dict[str, str]) -> RunResult:
        if self._capability is not _EXECUTION_CAPABILITY:
            raise PermissionError("真实执行只能由受控命令行入口发起")
        started = time.monotonic()
        sessions_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
        before = {path: path.stat().st_mtime_ns for path in discover_rollouts(sessions_root)}
        prompt = task["prompt"] if candidate["kind"] == "baseline" else f"{candidate['instruction'].strip()}\n\n{task['prompt']}"
        codex = subprocess.run(["codex", "exec", prompt], cwd=worktree, check=False, capture_output=True, text=True)
        verify = subprocess.run(shlex.split(task["verify"], posix=False), cwd=worktree, check=False, capture_output=True, text=True)
        passed = codex.returncode == 0
        changed = [path for path in discover_rollouts(sessions_root) if before.get(path) != path.stat().st_mtime_ns]
        traces = [parse_rollout(path, identifier_salt="experiment") for path in changed]
        inputs = [trace.token_usage.input_tokens for trace in traces if trace.token_usage.input_tokens is not None]
        totals = [trace.token_usage.total_tokens for trace in traces if trace.token_usage.total_tokens is not None]
        input_tokens = sum(inputs) if inputs else None
        total_tokens = sum(totals) if totals else None
        return RunResult(passed, verify.returncode == 0, input_tokens, total_tokens, time.monotonic() - started)


def _authorized_command_runner() -> CommandRunner:
    """仅供 CLI 模块使用；公共默认运行器保持失效关闭。"""
    return CommandRunner(_EXECUTION_CAPABILITY)


def execute_manifest(manifest: ExperimentManifest, *, execute: bool, token_budget: int, runner: CommandRunner) -> list[RunResult]:
    """执行前检查预算和 Git 边界；未带 execute 时绝不产生子进程。"""
    if not execute:
        return []
    root = manifest.repository_root.resolve()
    if not runner.is_git_repository(root):
        return [RunResult(False, False, None, None, None, "non_git_repository")]
    results: list[RunResult] = []
    used_tokens = 0
    consecutive_quality_failures = 0
    for _ in range(manifest.repetitions):
        for task in manifest.tasks:
            for variant in ({"kind": "baseline"}, manifest.candidate):
                worktree = runner.create_worktree(root, manifest.baseline_commit)
                try:
                    result = replace(runner.run_candidate(worktree, variant, task), variant=("baseline" if variant["kind"] == "baseline" else "candidate"))
                    if result.input_tokens is None:
                        results.append(replace(result, stop_reason="unknown_token_usage"))
                        return results
                    used_tokens += result.input_tokens
                    if used_tokens >= token_budget:
                        results.append(replace(result, stop_reason="token_budget_reached"))
                        return results
                    consecutive_quality_failures = 0 if result.quality_passed else consecutive_quality_failures + 1
                    if consecutive_quality_failures >= 2:
                        results.append(replace(result, stop_reason="consecutive_quality_failures"))
                        return results
                    results.append(result)
                finally:
                    runner.remove_worktree(root, worktree)
    return results

