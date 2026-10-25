import json
import tempfile
import unittest
from pathlib import Path

from codex_token.experiments import ExperimentManifest, RunResult, compare_runs, load_manifest, plan_manifest


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

    def test_plan_is_data_only(self):
        manifest = ExperimentManifest("abc", Path("."), ({"id": "unit", "prompt": "测试", "verify": "python -m unittest"},), {"kind": "narrow_prompt"}, 3, "input_tokens", 100)
        self.assertFalse(plan_manifest(manifest)["execute"])


if __name__ == "__main__":
    unittest.main()
