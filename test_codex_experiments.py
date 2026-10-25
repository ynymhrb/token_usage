import json
import tempfile
import unittest
from pathlib import Path

from codex_token.experiments import ExperimentManifest, RunResult, compare_runs, execute_manifest, load_manifest, plan_manifest


def passing_baseline():
    return [RunResult(True, True, 100, 120, 10.0), RunResult(True, True, 100, 120, 10.0), RunResult(True, True, 100, 120, 10.0)]


def cheaper_failing_candidate():
    return [RunResult(False, False, 20, 30, 5.0), RunResult(False, False, 20, 30, 5.0), RunResult(False, False, 20, 30, 5.0)]


class ExperimentTests(unittest.TestCase):
    def test_compare_rejects_token_savings_when_quality_regresses(self):
        result = compare_runs(passing_baseline(), cheaper_failing_candidate())
        self.assertEqual(result.status, "quality_regression")

    def test_effective_requires_three_consistent_repetitions_and_ten_percent_improvement(self):
        candidate = [RunResult(True, True, 80, 90, 9.0) for _ in range(3)]
        self.assertEqual(compare_runs(passing_baseline(), candidate).status, "effective")
        self.assertEqual(compare_runs(passing_baseline(), candidate[:2]).status, "uncertain")

    def test_manifest_requires_fixed_safety_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps({"baseline_commit": "abc"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(path)

    def test_manifest_rejects_invalid_scalar_types_without_crashing_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps({
                "baseline_commit": "abc", "repository_root": ".", "tasks": [{"id": "x", "prompt": "p", "verify": "python"}],
                "candidate": {"kind": "narrow_prompt"}, "repetitions": None, "primary_metric": "input_tokens", "token_budget": -1,
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_manifest(path)

    def test_plan_is_data_only(self):
        manifest = ExperimentManifest("abc", Path("."), ({"id": "unit", "prompt": "测试", "verify": "python -m unittest"},), {"kind": "narrow_prompt"}, 3, "input_tokens", 100)
        self.assertFalse(plan_manifest(manifest)["execute"])

    def test_versioned_fixture_has_fixed_commit_task_verification_and_three_repetitions(self):
        manifest = load_manifest(Path("experiments/token-optimization-v1.json"))
        self.assertRegex(manifest.baseline_commit, r"^[0-9a-f]{40}$")
        self.assertEqual(manifest.repetitions, 3)
        self.assertTrue(all(task["verify"] for task in manifest.tasks))


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def is_git_repository(self, root):
        return True

    def create_worktree(self, root, baseline_commit):
        self.calls.append(("create", str(root), baseline_commit))
        return Path("temporary-worktree")

    def remove_worktree(self, root, worktree):
        self.calls.append(("remove", str(worktree)))

    def run_candidate(self, worktree, candidate, task):
        self.calls.append(task["id"])
        return self.results.pop(0)


class ExecutionGateTests(unittest.TestCase):
    def manifest(self):
        return ExperimentManifest("abc", Path("."), ({"id": "unit", "prompt": "测试", "verify": "python -m unittest"},), {"kind": "narrow_prompt"}, 3, "input_tokens", 100)

    def test_execute_manifest_stops_at_token_budget_without_a_second_run(self):
        runner = FakeRunner([RunResult(True, True, 120, 120, 1.0), RunResult(True, True, 120, 120, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual([call for call in runner.calls if call == "unit"], ["unit"])
        self.assertEqual(runner.calls[-1][0], "remove")
        self.assertEqual(results[0].stop_reason, "token_budget_reached")

    def test_missing_execute_never_invokes_runner(self):
        runner = FakeRunner([RunResult(True, True, 1, 1, 1.0)])
        self.assertEqual(execute_manifest(self.manifest(), execute=False, token_budget=100, runner=runner), [])
        self.assertEqual(runner.calls, [])

    def test_consecutive_quality_failures_stop_and_remove_worktree(self):
        runner = FakeRunner([RunResult(False, False, 1, 1, 1.0), RunResult(False, False, 1, 1, 1.0), RunResult(True, True, 1, 1, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if call == "unit"]), 2)
        self.assertEqual(results[-1].stop_reason, "consecutive_quality_failures")
        self.assertEqual(runner.calls[-1][0], "remove")

    def test_each_repetition_uses_a_fresh_worktree(self):
        runner = FakeRunner([RunResult(True, True, 1, 1, 1.0) for _ in range(3)])
        execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "create"]), 3)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "remove"]), 3)

    def test_unknown_token_usage_stops_before_a_second_paid_run(self):
        runner = FakeRunner([RunResult(True, True, None, None, 1.0), RunResult(True, True, 1, 1, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if call == "unit"]), 1)
        self.assertEqual(results[-1].stop_reason, "unknown_token_usage")


if __name__ == "__main__":
    unittest.main()
