# Usage Analyze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fact-first Claude usage profiler, a zero-LLM PowerShell command, and a manually invoked `/usage-analyze` Skill whose interpretation runs in a forked subagent.

**Architecture:** Keep JSONL parsing and deterministic aggregation in the Python profiler. Publish one versioned report contract separating facts, derived metrics, estimates, heuristic flags, and data gaps; both the shell wrapper and Skill consume that contract. The shell wrapper invokes Python directly, while the Skill always runs in `context: fork` and optionally interprets the compact LLM package.

**Tech Stack:** Python 3 standard library, `unittest`, PowerShell, Claude Code Agent Skills markdown/YAML.

**Spec:** `docs/superpowers/specs/2026-08-14-usage-analyze-design.md`

## Global Constraints

- The shell commands `usage-analyze` and `ua` must never launch `claude` or another model process.
- `/usage-analyze --raw` may consume one Skill invocation and must not claim zero-token execution.
- Skill frontmatter must include `context: fork` and `disable-model-invocation: true`.
- The profiler must not equate local processed input with subscription quota deduction.
- Exact facts, derived metrics, estimates, heuristic flags, and data gaps must remain separately labeled.
- Use only Python's standard library; add no runtime package dependency.
- Preserve existing JSON keys for one compatibility version and mark them deprecated.

---

### Task 1: Fact-first metrics and configurable observation thresholds

**Files:**
- Modify: `claude_session_token_profiler.py:1437`
- Modify: `claude_session_token_profiler.py:2104`
- Test: `test_claude_session_token_profiler.py`

**Interfaces:**
- Produces: `percentile(values: Iterable[int], percentile: float) -> int`
- Produces: `context_distribution(calls: Iterable[ApiCall], thresholds: Iterable[int]) -> Dict[str, Any]`
- Changes: `diagnosis_metrics(..., context_threshold: Optional[int] = None)` returns measurements without `severity`
- CLI: repeatable `--context-threshold TOKENS`; defaults to observation buckets `100000`, `200000`, `500000`

- [ ] **Step 1: Write failing distribution tests**

```python
class ContextDistributionTests(unittest.TestCase):
    def test_percentiles_and_fixed_buckets_are_hand_checkable(self):
        calls = [ApiCall("main", str(i), i, cache_read=n) for i, n in enumerate([10_000, 100_000, 200_000, 500_000])]
        result = profiler.context_distribution(calls, [100_000, 200_000, 500_000])
        self.assertEqual(result["percentiles"], {"p50": 150_000, "p75": 275_000, "p90": 410_000, "p95": 455_000})
        self.assertEqual(result["buckets"]["gt_100000"]["calls"], 2)
        self.assertEqual(result["buckets"]["gt_200000"]["calls"], 1)
        self.assertEqual(result["buckets"]["gt_500000"]["calls"], 0)

    def test_measurements_have_no_severity(self):
        result = profiler.diagnosis_metrics([], collections.Counter(), [])
        self.assertNotIn("severity", result)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest -v test_claude_session_token_profiler.ContextDistributionTests`

Expected: FAIL because `context_distribution` is absent and `severity` remains.

- [ ] **Step 3: Implement interpolation, buckets, and threshold parsing**

Use linear interpolation on sorted values. Record each bucket as `{threshold, calls, call_pct, processed, processed_pct}`. Treat thresholds as observation metadata rather than risk boundaries.

- [ ] **Step 4: Remove severity and fixed `150k` presentation**

Replace `print_diagnosis` with `[上下文分布]` and deterministic cache/event facts. Remove `CRITICAL/HIGH/MEDIUM/LOW`, `>150k`, and `thinking > 50k` factor rules.

- [ ] **Step 5: Run full tests**

Run: `python -m unittest -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add claude_session_token_profiler.py test_claude_session_token_profiler.py
git commit -m "refactor: report context distribution without severity"
```

### Task 2: Evidence-based large cache creation classification

**Files:**
- Modify: `claude_session_token_profiler.py:564`
- Modify: `claude_session_token_profiler.py:1681`
- Modify: `claude_session_token_profiler.py:2022`
- Test: `test_claude_session_token_profiler.py`

**Interfaces:**
- Produces: `is_large_cache_creation(call, min_tokens=100000, min_ratio=0.70) -> bool`
- Produces: `classify_cache_creation(call, previous_call=None, idle_seconds=None, ...) -> Dict[str, Any]`
- Classification result: `{kind, confidence, evidence, cache_write_tokens, creation_ratio, ttl}`
- Compatibility: `is_full_rewrite` remains as a deprecated alias for one profiler version

- [ ] **Step 1: Write failing classification tests**

```python
def test_first_large_write_is_initial_population_not_rewrite(self):
    call = ApiCall("main", "1", 1, cache_write=200_000, cache_write_1h=200_000)
    flag = profiler.classify_cache_creation(call, previous_call=None)
    self.assertEqual(flag["kind"], "initial-cache-population")
    self.assertEqual(flag["confidence"], "high")

def test_zero_read_after_similar_cached_context_is_possible_recreation(self):
    previous = ApiCall("main", "1", 1, cache_read=190_000)
    call = ApiCall("main", "2", 2, cache_write=200_000, cache_write_1h=200_000)
    flag = profiler.classify_cache_creation(call, previous_call=previous, idle_seconds=3700)
    self.assertEqual(flag["kind"], "possible-cache-recreation")
    self.assertIn(flag["confidence"], {"medium", "low"})
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest -v test_claude_session_token_profiler.RewriteMetricTests`

Expected: FAIL because evidence-aware classification is absent.

- [ ] **Step 3: Implement classification from observable transitions**

Use prior same-actor request, cache read/write transition, idle interval, context similarity, and declared TTL. Never claim TTL expiry as fact; place every inferred reason in `heuristic_flags` with evidence.

- [ ] **Step 4: Rename human-facing output**

Print `LARGE_CACHE_CREATION` in terminal and JSON. Retain deprecated `full_cache_rewrite` fields with identical booleans for compatibility.

- [ ] **Step 5: Run full tests and real historical report**

Run:

```powershell
python -m unittest -v
python .\claude_session_token_profiler.py --five-hour-at "2026-08-13 22:00" --json-out .\report-cache-classification.json
```

Expected: 18:05 is initial population; 22:39 is possible recreation; totals remain unchanged.

- [ ] **Step 6: Commit**

```powershell
git add claude_session_token_profiler.py test_claude_session_token_profiler.py
git commit -m "refactor: classify large cache creation from evidence"
```

### Task 3: Versioned report contract and compact LLM package

**Files:**
- Modify: `claude_session_token_profiler.py:1814`
- Modify: `claude_session_token_profiler.py:2104`
- Test: `test_claude_session_token_profiler.py`

**Interfaces:**
- Produces: `build_report_contract(...) -> Dict[str, Any]`
- Produces: `build_llm_package(report: Dict[str, Any]) -> Dict[str, Any]`
- CLI: `--llm-package PATH`; suffix `.md` selects Markdown, otherwise JSON
- JSON: `schema_version`, `facts`, `derived_metrics`, `estimates`, `heuristic_flags`, `data_gaps`, `sessions`, `calls`, `deprecated`

- [ ] **Step 1: Write failing contract tests**

```python
def test_contract_separates_measurement_classes(self):
    report = profiler.build_report_contract(sample_inputs)
    self.assertEqual(report["schema_version"], "1.0")
    self.assertIn("facts", report)
    self.assertIn("derived_metrics", report)
    self.assertIn("estimates", report)
    self.assertIn("heuristic_flags", report)
    self.assertIn("data_gaps", report)
    self.assertIn("severity", report.get("deprecated", {}))

def test_llm_package_states_non_equivalence_and_data_gaps(self):
    package = profiler.build_llm_package(sample_report)
    self.assertFalse(package["interpretation_constraints"]["processed_input_equals_quota"])
    self.assertIn("other devices", " ".join(package["data_gaps"]).lower())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest -v test_claude_session_token_profiler.ReportContractTests`

Expected: FAIL because contract builders are absent.

- [ ] **Step 3: Implement the contract and compatibility block**

Move current totals and rows into their correct measurement classes. Keep legacy top-level keys under `deprecated` for one compatibility version and print a migration note in `--help`.

- [ ] **Step 4: Implement compact JSON and Markdown package writers**

Include the account window, context distribution, model/session/turn shares, cache flags, limit incidents, estimates, and data gaps. Omit raw transcript content and full request IDs unless needed for evidence.

- [ ] **Step 5: Run full tests and validate JSON parsing**

Run:

```powershell
python -m unittest -v
python .\claude_session_token_profiler.py --five-hour-at "2026-08-13 22:00" --json-out .\contract.json --llm-package .\llm-package.json
python -m json.tool .\contract.json > $null
python -m json.tool .\llm-package.json > $null
```

Expected: exit 0 and valid UTF-8 JSON.

- [ ] **Step 6: Commit**

```powershell
git add claude_session_token_profiler.py test_claude_session_token_profiler.py
git commit -m "feat: add versioned usage analysis contract"
```

### Task 4: Zero-LLM PowerShell command

**Files:**
- Create: `scripts/usage-analyze.ps1`
- Create: `test_usage_analyze_wrapper.py`
- Modify: `claude_session_token_profiler.py:2104`

**Interfaces:**
- Shell: `usage-analyze [profiler args]`
- Shell alias/function target: `ua [profiler args]`
- No arguments map to `--current-five-hour`
- Wrapper resolves `../claude_session_token_profiler.py` relative to `$PSScriptRoot`

- [ ] **Step 1: Write failing wrapper tests**

Create a temporary fake `python.exe` that records argv and a fake `claude.exe` that records any invocation. Assert:

```python
self.assertEqual(recorded_python_args[-1], "--current-five-hour")
self.assertFalse(claude_marker.exists())
```

Also execute the wrapper from a temporary unrelated working directory and assert the resolved profiler path is absolute.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest -v test_usage_analyze_wrapper`

Expected: FAIL because `scripts/usage-analyze.ps1` is absent.

- [ ] **Step 3: Implement the wrapper**

Use a PowerShell `param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ProfilerArgs)` block, resolve Python through `Get-Command python`, append `--current-five-hour` only when no args exist, and propagate `$LASTEXITCODE`.

- [ ] **Step 4: Run wrapper and full tests**

Run:

```powershell
python -m unittest -v test_usage_analyze_wrapper
powershell -NoProfile -File .\scripts\usage-analyze.ps1 --version
python -m unittest -v
```

Expected: wrapper reports the profiler version and never starts Claude.

- [ ] **Step 5: Commit**

```powershell
git add scripts/usage-analyze.ps1 test_usage_analyze_wrapper.py claude_session_token_profiler.py
git commit -m "feat: add zero-llm usage analyze command"
```

### Task 5: Create the project Skill with isolated routing

**Files:**
- Create: `.claude/skills/usage-analyze/SKILL.md`
- Create: `.claude/skills/usage-analyze/agents/openai.yaml`
- Create: `.claude/skills/usage-analyze/scripts/usage-analyze.ps1`
- Create: `.claude/skills/usage-analyze/references/analysis-contract.md`
- Create: `test_usage_analyze_skill.py`

**Interfaces:**
- Slash command: `/usage-analyze [--raw|--llm] [profiler args or natural language]`
- Frontmatter: `name: usage-analyze`, `context: fork`, `disable-model-invocation: true`, `argument-hint: "[--raw|--llm] [time range or description]"`, `shell: powershell`
- Skill output contract: raw mode returns deterministic stdout; analysis mode returns `Facts`, `Inferences`, `Recommendations`, `Uncertainties`

- [ ] **Step 1: Write baseline routing evaluations before creating the Skill**

Record expected failures for these prompts without Skill instructions:

```text
/usage-analyze --raw --five-hour-at "2026-08-13 22:00"
/usage-analyze --llm --five-hour-at "2026-08-13 22:00"
/usage-analyze 最近一次为什么耗尽这么快
```

The baseline fails if it invents quota weights, omits the script, mixes facts with advice, or executes analysis inline.

- [ ] **Step 2: Initialize the Skill using skill-creator tooling**

Run:

```powershell
python C:\Users\19632\.codex\skills\.system\skill-creator\scripts\init_skill.py usage-analyze --path .\.claude\skills --resources scripts,references --interface 'display_name=usageAnalyze' --interface 'short_description=Analyze Claude token usage safely' --interface 'default_prompt=Use $usage-analyze to inspect my current five-hour Claude usage.'
```

Expected: required Skill directory and `agents/openai.yaml` exist.

- [ ] **Step 3: Write the minimal routing Skill**

Make the body follow this exact decision contract:

```text
Parse $ARGUMENTS.
- --raw: run the bundled wrapper with remaining profiler flags; return stdout verbatim.
- --llm: run with --llm-package, read only that package, then emit Facts/Inferences/Recommendations/Uncertainties.
- no arguments: use --current-five-hour and analyze.
- natural language: map explicit time intent to profiler flags, state the mapping, run with --llm-package, and analyze.
Never read raw transcripts for interpretation. Never equate processed input with quota deduction.
```

- [ ] **Step 4: Add the analysis contract reference**

Document field meanings and the four-section output shape. Require evidence citations using session ID, turn number, timestamp, and metric values.

- [ ] **Step 5: Copy the tested profiler into the Skill bundle**

Use `Copy-Item -LiteralPath .\claude_session_token_profiler.py -Destination .\.claude\skills\usage-analyze\scripts\claude_session_token_profiler.py`, then adjust the bundled wrapper to resolve that adjacent file.

- [ ] **Step 6: Validate static Skill invariants**

Run:

```powershell
python C:\Users\19632\.codex\skills\.system\skill-creator\scripts\quick_validate.py .\.claude\skills\usage-analyze
python -m unittest -v test_usage_analyze_skill
```

Expected: validation and tests PASS; frontmatter contains both isolation controls.

- [ ] **Step 7: Forward-test three fresh-context invocations**

Run one isolated evaluation each for raw flags, explicit LLM flags, and natural-language time selection. Pass only the Skill path and user invocation; do not pass intended answers or this plan. Verify raw mode contains no advice and analysis mode has four labeled sections with evidence.

- [ ] **Step 8: Commit**

```powershell
git add .claude/skills/usage-analyze test_usage_analyze_skill.py
git commit -m "feat: add isolated usage analyze skill"
```

### Task 6: Personal installation and end-to-end acceptance

**Files:**
- Create outside repository after approval: `C:\Users\19632\.claude\skills\usage-analyze\...`
- Optionally modify after separate approval: the active PowerShell profile

**Interfaces:**
- Personal Skill becomes discoverable as `/usage-analyze`
- Optional profile functions: `usage-analyze` and `ua` delegate to installed wrapper

- [ ] **Step 1: Verify repository state before installation**

Run:

```powershell
python -m unittest -v
python -m py_compile .\claude_session_token_profiler.py
python C:\Users\19632\.codex\skills\.system\skill-creator\scripts\quick_validate.py .\.claude\skills\usage-analyze
```

Expected: all tests and validation PASS.

- [ ] **Step 2: Request approval for personal Skill installation**

State the exact target `C:\Users\19632\.claude\skills\usage-analyze` and that existing content, if any, will be inspected before replacement.

- [ ] **Step 3: Install without destructive replacement**

If the target is absent, copy the directory. If present, compare files and request direction before overwriting divergent content.

- [ ] **Step 4: Offer optional PowerShell profile registration**

Do not modify the profile implicitly. If approved, append idempotent functions whose bodies invoke the installed `usage-analyze.ps1` with `@args`.

- [ ] **Step 5: Run end-to-end acceptance**

Run:

```powershell
usage-analyze --version
usage-analyze --five-hour-at "2026-08-13 22:00" --json-out .\acceptance-report.json
```

Then invoke `/usage-analyze --raw --five-hour-at "2026-08-13 22:00"` and `/usage-analyze 最近一次为什么耗尽这么快` in fresh Claude Code contexts.

Expected: shell reports no model process, raw Skill output contains facts only, and natural-language analysis is isolated and evidence-backed.

- [ ] **Step 6: Final verification and commit any repository-only fixes**

Run `git status --short`, inspect every changed path, rerun the full suite, and commit only required repository changes.
