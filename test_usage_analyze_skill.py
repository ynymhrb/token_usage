"""Static contract tests for the project-local usage-analyze Skill."""

from __future__ import annotations

import re
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SKILL = ROOT / ".claude" / "skills" / "usage-analyze"
SKILL_MD = SKILL / "SKILL.md"
OPENAI_YAML = SKILL / "agents" / "openai.yaml"
BUNDLED_PROFILER = SKILL / "scripts" / "claude_session_token_profiler.py"
BUNDLED_WRAPPER = SKILL / "scripts" / "usage-analyze.ps1"
ANALYSIS_CONTRACT = SKILL / "references" / "analysis-contract.md"
ROUTER = SKILL / "scripts" / "route_usage_analyze.py"
ROOT_PROFILER = ROOT / "claude_session_token_profiler.py"


class UsageAnalyzeSkillTests(unittest.TestCase):
    def read(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"required Skill file missing: {path}")
        return path.read_text(encoding="utf-8")

    def test_required_files_exist(self):
        for path in (
            SKILL_MD,
            OPENAI_YAML,
            BUNDLED_PROFILER,
            BUNDLED_WRAPPER,
            ANALYSIS_CONTRACT,
            ROUTER,
        ):
            self.assertTrue(path.is_file(), f"required Skill file missing: {path}")

    def test_claude_frontmatter_enforces_explicit_isolated_invocation(self):
        text = self.read(SKILL_MD)
        frontmatter = text.split("---", 2)[1]
        expected = {
            "name": "usage-analyze",
            "context": "fork",
            "disable-model-invocation": "true",
            "argument-hint": '"[--raw|--llm] [time range or description]"',
            "shell": "powershell",
        }
        for key, value in expected.items():
            self.assertRegex(frontmatter, rf"(?m)^{re.escape(key)}:\s*{re.escape(value)}\s*$")

    def test_skill_defines_every_routing_mode_and_safety_boundary(self):
        text = self.read(SKILL_MD)
        required = (
            "$ARGUMENTS",
            "--raw",
            "stdout verbatim",
            "no advice",
            "--llm",
            "temporary",
            "--llm-package",
            "read only that package",
            "--current-five-hour",
            "natural language",
            "display_mapping",
            "Never read raw transcripts",
            "Never equate processed input with quota deduction",
            "Facts",
            "Inferences",
            "Recommendations",
            "Uncertainties",
        )
        for phrase in required:
            self.assertIn(phrase, text)
        self.assertRegex(text, r"(?is)--raw.*Skill invocation.*model tokens")

    def test_raw_route_is_facts_only_and_llm_route_is_package_only(self):
        text = self.read(SKILL_MD)
        self.assertRegex(text, r"(?is)--raw.*return stdout verbatim.*no advice")
        self.assertRegex(text, r"(?is)--llm.*--llm-package.*read only that package")
        self.assertRegex(text, r"(?is)recommendations.*LLM-owned.*not profiler facts")

    def test_router_defaults_and_raw_mapping_contract(self):
        spec = importlib.util.spec_from_file_location("usage_analyze_router", ROUTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertEqual(module.route(["--llm"])["profiler_args"], ["--current-five-hour"])
        self.assertEqual(
            module.route(["--llm", "--lang", "en"])["profiler_args"],
            ["--lang", "en", "--current-five-hour"],
        )
        raw = module.route(["--raw", "最近一次"])
        self.assertEqual(raw["profiler_args"], ["--current-five-hour"])
        self.assertFalse(raw["display_mapping"])
        analyzed = module.route(["最近6小时"])
        self.assertEqual(analyzed["profiler_args"], ["--last-hours", "6"])
        self.assertTrue(analyzed["display_mapping"])
        with self.assertRaises(module.RouteError):
            module.route(["--llm", "随便分析一下"])

    def test_skill_requires_unconditional_cleanup_and_exact_raw_output(self):
        text = self.read(SKILL_MD)
        self.assertIn("try/finally", text)
        self.assertIn("on success or failure", text)
        self.assertIn("without revealing the temporary path", text)
        self.assertRegex(text, r"(?is)mode: raw.*stdout verbatim.*no prefix.*time mapping")

    def test_bundled_wrapper_uses_adjacent_profiler_and_never_claude(self):
        text = self.read(BUNDLED_WRAPPER)
        self.assertIn("$PSScriptRoot", text)
        self.assertIn("claude_session_token_profiler.py", text)
        self.assertNotIn('Get-Command -Name claude', text)
        self.assertNotRegex(text, r"(?im)^\s*&\s*claude\b")
        self.assertNotIn("..\\claude_session_token_profiler.py", text)

    def test_root_and_bundled_profiler_are_identical_version_3(self):
        self.assertEqual(ROOT_PROFILER.read_bytes(), BUNDLED_PROFILER.read_bytes())
        text = self.read(ROOT_PROFILER)
        self.assertTrue(text.startswith("# Claude Code Session Token Profiler v3.0.0"))
        self.assertRegex(text, r'(?m)^__version__\s*=\s*"3\.0\.0"$')

    def test_analysis_reference_defines_classes_evidence_and_gaps(self):
        text = self.read(ANALYSIS_CONTRACT)
        for term in (
            "facts",
            "derived_metrics",
            "estimates",
            "heuristic_flags",
            "data_gaps",
            "session ID",
            "turn number",
            "timestamp",
            "metric",
            "Facts",
            "Inferences",
            "Recommendations",
            "Uncertainties",
        ):
            self.assertIn(term, text)
        self.assertIn("not observable", text)

    def test_openai_metadata_is_concise_and_explicit(self):
        text = self.read(OPENAI_YAML)
        self.assertIn('display_name: "usageAnalyze"', text)
        self.assertIn('short_description: "Analyze Claude token usage safely"', text)
        self.assertIn("$usage-analyze", text)


if __name__ == "__main__":
    unittest.main()
