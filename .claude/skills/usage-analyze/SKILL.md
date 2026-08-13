---
name: usage-analyze
description: Analyze local Claude Code token usage for an explicit five-hour window or natural-language time request. Use only when the user explicitly invokes /usage-analyze to request a raw profiler report or an evidence-backed LLM interpretation of local usage.
context: fork
disable-model-invocation: true
argument-hint: "[--raw|--llm] [time range or description]"
shell: powershell
---

# Usage Analyze

Parse `$ARGUMENTS`, select exactly one route below, and run the bundled
`scripts/usage-analyze.ps1`. Resolve resources relative to this Skill directory.

## Route the request

### `--raw`

Remove `--raw`, verify the remaining arguments are known profiler flags, and run
the bundled wrapper with those flags. Return stdout verbatim with no advice,
summary, diagnosis, or Markdown wrapper. Propagate any error rather than guessing.

Although this route does no LLM interpretation of the report, the Skill invocation
itself can consume model tokens. Do not claim that `/usage-analyze --raw` is a
zero-token execution; only the separate shell command avoids a Skill invocation.

### `--llm`

Remove `--llm`, create a temporary `.json` path, then run the wrapper with the
remaining known profiler flags plus `--llm-package <temporary-path>`. Read only
that package for interpretation; never read a raw transcript or any JSONL file.
Read [references/analysis-contract.md](references/analysis-contract.md), emit the
four required sections `Facts`, `Inferences`, `Recommendations`, and
`Uncertainties`, and cite evidence. Delete the temporary package after reading it.

### No arguments

Use `--current-five-hour` and the analysis flow: create a temporary package, run
the wrapper with `--current-five-hour --llm-package <temporary-path>`, read only
that package, and apply the analysis contract.

### Natural language or unknown arguments

Interpret explicit time intent and map it to supported profiler flags; state the mapping
before analysis, for example, `"last/current five-hour window" ->
--current-five-hour` or an explicit timestamp -> `--five-hour-at <timestamp>`.
Do not pass unknown text to the profiler. Ask a question only when two plausible
time mappings would materially change the result. Then create a temporary package,
run with the mapped flags plus `--llm-package`, read only that package, and apply
the analysis contract.

If `--raw` is combined with natural language, state the time-to-flag mapping only
when needed to make execution unambiguous, then return the wrapper stdout verbatim
with no advice.

## Preserve evidence boundaries

Never read raw transcripts for interpretation. Never equate processed input with quota deduction.
Local JSONL does not expose subscription quota weights or usage
from other devices. Treat recommendations as LLM-owned, not profiler facts, and
keep estimates and heuristic flags distinct from exact facts.

If package creation or interpretation fails, return the profiler's factual stdout
and the temporary package path when one exists; do not invent missing evidence.
