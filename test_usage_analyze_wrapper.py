"""Integration tests for the zero-LLM PowerShell usage-analyze wrapper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent
WRAPPER = REPOSITORY_ROOT / "scripts" / "usage-analyze.ps1"
PROFILER = REPOSITORY_ROOT / "claude_session_token_profiler.py"
POWERSHELL = "powershell"


class UsageAnalyzeWrapperTests(unittest.TestCase):
    def make_fake_commands(self, directory: Path, record: Path, claude_marker: Path) -> None:
        recorder = directory / "record_python_args.py"
        recorder.write_text(
            textwrap.dedent(
                """\
                import json
                import os
                import sys
                from pathlib import Path

                Path(os.environ["USAGE_ANALYZE_TEST_RECORD"]).write_text(
                    json.dumps(sys.argv[1:]), encoding="utf-8"
                )
                raise SystemExit(int(os.environ.get("USAGE_ANALYZE_TEST_PYTHON_EXIT", "0")))
                """
            ),
            encoding="utf-8",
        )
        (directory / "python.cmd").write_text(
            f'@"{sys.executable}" "{recorder}" %*\r\n', encoding="utf-8"
        )
        (directory / "claude.cmd").write_text(
            f'@echo invoked>"{claude_marker}"\r\n', encoding="utf-8"
        )

    def run_wrapper(self, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-File", str(WRAPPER), *args],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_wrapper_exists(self):
        self.assertTrue(WRAPPER.is_file(), "scripts/usage-analyze.ps1 must exist")

    def test_defaults_to_current_five_hour_with_absolute_profiler_from_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            commands = temp / "commands"
            commands.mkdir()
            record = temp / "python-args.json"
            claude_marker = temp / "claude-invoked.txt"
            self.make_fake_commands(commands, record, claude_marker)
            env = os.environ.copy()
            env["PATH"] = str(commands) + os.pathsep + env["PATH"]
            env["USAGE_ANALYZE_TEST_RECORD"] = str(record)
            env["USAGE_ANALYZE_TEST_PYTHON_EXIT"] = "0"

            result = self.run_wrapper([], temp, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(record.read_text(encoding="utf-8")), [str(PROFILER.resolve()), "--current-five-hour"])
            self.assertFalse(claude_marker.exists())

    def test_forwards_profiler_arguments_exactly_and_propagates_exit_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            commands = temp / "commands"
            commands.mkdir()
            record = temp / "python-args.json"
            claude_marker = temp / "claude-invoked.txt"
            self.make_fake_commands(commands, record, claude_marker)
            env = os.environ.copy()
            env["PATH"] = str(commands) + os.pathsep + env["PATH"]
            env["USAGE_ANALYZE_TEST_RECORD"] = str(record)
            env["USAGE_ANALYZE_TEST_PYTHON_EXIT"] = "17"
            arguments = ["--five-hour-at", "2026-08-13 22:00", "--json-out", "report with spaces.json"]

            result = self.run_wrapper(arguments, temp, env)

            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertEqual(json.loads(record.read_text(encoding="utf-8")), [str(PROFILER.resolve()), *arguments])
            self.assertFalse(claude_marker.exists())

    def test_wrapper_reports_profiler_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_wrapper(["--version"], Path(temp_dir), os.environ.copy())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.0.0", result.stdout)


if __name__ == "__main__":
    unittest.main()
