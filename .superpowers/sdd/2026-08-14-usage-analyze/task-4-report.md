# Task 4 Report: zero-LLM PowerShell command

## Delivered

- Added `scripts/usage-analyze.ps1`.
  - Resolves `../claude_session_token_profiler.py` from `$PSScriptRoot`, independent of the caller's working directory.
  - Locates Python with `Get-Command python`, selects the first executable match, and gives a clear error when absent.
  - Defaults to `--current-five-hour` only when no profiler arguments are supplied.
  - Forwards supplied arguments without rewriting them and exits with Python's exit code.
  - Invokes only Python; it never invokes `claude` or another model process.
- Added standard-library `unittest` wrapper integration coverage using fake `python.cmd` and `claude.cmd` commands.
  - Covers the default argv, absolute profiler path, unrelated working directory, exact argument forwarding, propagated nonzero exit code, no Claude invocation, and `--version`.
- No PowerShell profile was read or modified. `usage-analyze` and `ua` remain external shell-registration names that can safely delegate to this wrapper later.

## RED

```powershell
python -m unittest -v test_usage_analyze_wrapper
```

Before implementation, all four tests failed because `scripts/usage-analyze.ps1` was absent. PowerShell reported that the `-File` target did not exist.

## GREEN

```powershell
python -m unittest -v test_usage_analyze_wrapper
# Ran 4 tests ... OK

powershell -NoProfile -File .\scripts\usage-analyze.ps1 --version
# claude_session_token_profiler.py 2.6.0

python -m unittest -v
# Ran 29 tests ... OK
```

## Scope and concerns

- Existing generated reports and `__pycache__` changes were preserved and left unstaged.
- The wrapper intentionally does not register `usage-analyze` or `ua` in a PowerShell profile; profile mutation is outside this task.
