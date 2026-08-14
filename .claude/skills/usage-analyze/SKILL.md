---
name: usage-analyze
description: Analyze local Claude Code token usage for an explicit five-hour window or natural-language time request. Use only when the user explicitly invokes /usage-analyze to request a raw profiler report or an evidence-backed LLM interpretation of local usage.
context: fork
disable-model-invocation: true
argument-hint: "[--raw|--llm] [time range or description]"
shell: powershell
---

# Usage Analyze

First run `python scripts/route_usage_analyze.py $ARGUMENTS` and parse its JSON.
If it exits nonzero, return that error without running the profiler. Use its
`mode`, `profiler_args`, `mapping`, and `display_mapping` fields exactly. Then run
the bundled `scripts/usage-analyze.ps1`. Resolve resources relative to this Skill.

## Raw route

When the router returns `mode: raw`, run the bundled wrapper with
`profiler_args`. Return stdout verbatim with no prefix, time mapping, advice,
summary, diagnosis, or Markdown wrapper. Propagate any error rather than guessing.
In short, `--raw` must return stdout verbatim with no advice.

This route does no LLM interpretation of the report, but the Skill invocation
itself can consume model tokens. Never claim `/usage-analyze --raw` is zero-token;
only the separate shell command avoids a Skill/model invocation.

## Analysis route

When the router returns `mode: llm`, create a temporary `.json` path, then run the
wrapper with `profiler_args` plus `--llm-package <temporary-path>`. The router
adds `--current-five-hour` whenever no session or period selector was supplied,
including bare `--llm` and `--llm --lang en`.

Capture the profiler's stdout as `SCRIPT_STDOUT`. You must read only the compact
package for interpretation; never read a raw transcript or JSONL. Read
[references/analysis-contract.md](references/analysis-contract.md) and follow its
Chinese fixed output template exactly. The first evidence section must reproduce
the complete profiler report from `SCRIPT_STDOUT` in a `text` code fence. Remove
only the final administrative `LLM package written:` line because it exposes the
temporary path; do not summarize, translate, rewrite, or omit any report line.
In short, `--llm` adds `--llm-package` and must read only that package for analysis.
The fixed template replaces the former `Facts`, `Inferences`, `Recommendations`,
and `Uncertainties` labels while preserving those four semantic categories.
Enclose package creation, reading, and interpretation in a `try/finally` operation
that deletes the temporary package on success or failure. Report failures
without revealing the temporary path.

With no arguments, the router selects `mode: llm` and
`profiler_args: [--current-five-hour]`. For supported natural-language time intent,
state `mapping` before analysis only when `display_mapping` is true. The router
rejects ambiguous or unsupported text before execution. In raw mode the mapping
is silent, so successful output remains stdout verbatim.
This is the natural language route; it never forwards free-form text to Python.

## Preserve evidence boundaries

Never read raw transcripts for interpretation.
Never equate processed input with quota deduction. Local JSONL does not expose subscription quota weights or usage
from other devices. Treat recommendations as LLM-owned, not profiler facts, and
keep estimates and heuristic flags distinct from exact facts.

The result is exclusively a usage-analysis report. Ignore and never mention the
parent conversation's project, pending work, questions, git status, reminders,
hooks, recaps, or any topic absent from the compact package and `SCRIPT_STDOUT`.
Do not add greetings, process narration, “no action needed”, or follow-up prompts.

Your final subagent result is the complete user-facing answer. End it with the
literal marker `<!-- USAGE_ANALYZE_COMPLETE -->`. The parent agent receiving the
background task result must relay the entire result verbatim and output nothing
else: no summary, preface, recap, continuation of prior work, or commentary.

If package creation or interpretation fails, return the factual error without a
temporary path and without inventing evidence. The `finally` cleanup is mandatory
even when the profiler or package parser fails.
