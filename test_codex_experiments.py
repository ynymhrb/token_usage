import json
import tempfile
import unittest
from pathlib import Path

from codex_token.experiments import CommandRunner, ExperimentManifest, RunResult, compare_runs, execute_manifest, load_manifest, plan_manifest, write_experiment_results


def passing_baseline():
    return [RunResult(True, True, 100, 120, 10.0, task_id="unit", repetition=index) for index in range(3)]


def cheaper_failing_candidate():
    return [RunResult(False, False, 20, 30, 5.0, task_id="unit", repetition=index) for index in range(3)]


class ExperimentTests(unittest.TestCase):
    def test_compare_rejects_token_savings_when_quality_regresses(self):
        result = compare_runs(passing_baseline(), cheaper_failing_candidate())
        self.assertEqual(result.status, "quality_regression")

    def test_effective_requires_three_consistent_repetitions_and_ten_percent_improvement(self):
        candidate = [RunResult(True, True, 80, 90, 9.0, task_id="unit", repetition=index) for index in range(3)]
        self.assertEqual(compare_runs(passing_baseline(), candidate).status, "effective")
        self.assertEqual(compare_runs(passing_baseline(), candidate[:2]).status, "uncertain")

    def test_compare_requires_complete_paired_task_and_repetition_results(self):
        candidate = [RunResult(True, True, 80, 90, 9.0, task_id="other", repetition=index) for index in range(3)]
        result = compare_runs(passing_baseline(), candidate)
        self.assertEqual(result.status, "uncertain")
        self.assertIn("成对", result.reason)

    def test_compare_requires_three_repetitions_for_each_task(self):
        baseline = [RunResult(True, True, 100, 120, 10.0, task_id=task_id, repetition=0) for task_id in ("a", "b", "c")]
        candidate = [RunResult(True, True, 80, 90, 9.0, task_id=task_id, repetition=0) for task_id in ("a", "b", "c")]
        result = compare_runs(baseline, candidate)
        self.assertEqual(result.status, "uncertain")
        self.assertIn("每个任务", result.reason)

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

    def test_manifest_rejects_unimplemented_candidate_and_requires_auditable_instruction(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            base = {"baseline_commit": "abc", "repository_root": ".", "tasks": [{"id": "x", "prompt": "p", "verify": "python"}], "repetitions": 3, "primary_metric": "input_tokens", "token_budget": 1}
            path.write_text(json.dumps({**base, "candidate": {"kind": "limit_tool_output"}}), encoding="utf-8")
            with self.assertRaises(ValueError): load_manifest(path)
            path.write_text(json.dumps({**base, "candidate": {"kind": "narrow_prompt"}}), encoding="utf-8")
            with self.assertRaises(ValueError): load_manifest(path)

    def test_manifest_rejects_blank_or_non_string_task_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps({
                "baseline_commit": "abc", "repository_root": ".",
                "tasks": [{"id": "unit", "prompt": " ", "verify": 1}],
                "candidate": {"kind": "narrow_prompt", "instruction": "测试"},
                "repetitions": 3, "primary_metric": "input_tokens", "token_budget": 1,
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

    def test_result_receipt_excludes_prompt_and_candidate_instruction(self):
        manifest = ExperimentManifest("abc", Path("."), ({"id": "unit", "prompt": "机密提示", "verify": "python -m unittest"},), {"kind": "narrow_prompt", "instruction": "机密干预"}, 3, "input_tokens", 100)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            write_experiment_results(path, manifest, [RunResult(True, True, 1, 2, 1.0, task_id="unit", repetition=0)])
            rendered = path.read_text(encoding="utf-8")
        self.assertIn('"candidate_kind": "narrow_prompt"', rendered)
        self.assertNotIn("机密", rendered)


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
        self.calls.append(("run", candidate["kind"], task["id"]))
        return self.results.pop(0)


class ExecutionGateTests(unittest.TestCase):
    def manifest(self):
        return ExperimentManifest("abc", Path("."), ({"id": "unit", "prompt": "测试", "verify": "python -m unittest"},), {"kind": "narrow_prompt"}, 3, "input_tokens", 100)

    def test_execute_manifest_stops_at_token_budget_without_a_second_run(self):
        runner = FakeRunner([RunResult(True, True, 120, 120, 1.0), RunResult(True, True, 120, 120, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "run"]), 1)
        self.assertEqual(runner.calls[-1][0], "remove")
        self.assertEqual(results[0].stop_reason, "token_budget_reached")

    def test_missing_execute_never_invokes_runner(self):
        runner = FakeRunner([RunResult(True, True, 1, 1, 1.0)])
        self.assertEqual(execute_manifest(self.manifest(), execute=False, token_budget=100, runner=runner), [])
        self.assertEqual(runner.calls, [])

    def test_public_command_runner_is_fail_closed(self):
        with self.assertRaises(PermissionError):
            CommandRunner().run_candidate(Path("."), {"kind": "baseline"}, {"prompt": "x", "verify": "python"})

    def test_public_command_runner_never_creates_a_worktree_for_execution(self):
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=CommandRunner())
        self.assertEqual(results[0].stop_reason, "unpriced_execution")

    def test_candidate_quality_failures_stop_without_counting_baseline_results(self):
        runner = FakeRunner([RunResult(True, True, 1, 1, 1.0), RunResult(False, False, 1, 1, 1.0), RunResult(True, True, 1, 1, 1.0), RunResult(False, False, 1, 1, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "run"]), 4)
        self.assertEqual(results[-1].stop_reason, "consecutive_quality_failures")
        self.assertEqual(runner.calls[-1][0], "remove")

    def test_baseline_quality_failures_also_stop_before_next_candidate_run(self):
        runner = FakeRunner([RunResult(True, False, 1, 1, 1.0), RunResult(True, True, 1, 1, 1.0), RunResult(True, False, 1, 1, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "run"]), 3)
        self.assertEqual(results[-1].variant, "baseline")
        self.assertEqual(results[-1].stop_reason, "consecutive_quality_failures")

    def test_each_repetition_uses_a_fresh_worktree(self):
        runner = FakeRunner([RunResult(True, True, 1, 1, 1.0) for _ in range(6)])
        execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "create"]), 6)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "remove"]), 6)
        self.assertEqual({call[1] for call in runner.calls if isinstance(call, tuple) and call[0] == "run"}, {"baseline", "narrow_prompt"})

    def test_results_are_labeled_and_can_be_compared_by_variant(self):
        runner = FakeRunner([RunResult(True, True, 100, 100, 1.0), RunResult(True, True, 80, 80, 1.0)] * 3)
        results = execute_manifest(self.manifest(), execute=True, token_budget=1000, runner=runner)
        baseline = [result for result in results if result.variant == "baseline"]
        candidate = [result for result in results if result.variant == "candidate"]
        self.assertEqual((len(baseline), len(candidate)), (3, 3))
        self.assertEqual({(result.task_id, result.repetition) for result in baseline}, {("unit", 0), ("unit", 1), ("unit", 2)})
        self.assertEqual(compare_runs(baseline, candidate).status, "effective")

    def test_unknown_token_usage_stops_before_a_second_paid_run(self):
        runner = FakeRunner([RunResult(True, True, None, None, 1.0), RunResult(True, True, 1, 1, 1.0)])
        results = execute_manifest(self.manifest(), execute=True, token_budget=100, runner=runner)
        self.assertEqual(len([call for call in runner.calls if isinstance(call, tuple) and call[0] == "run"]), 1)
        self.assertEqual(results[-1].stop_reason, "unknown_token_usage")


if __name__ == "__main__":
    unittest.main()
