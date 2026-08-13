# Analysis contract

Use this contract only after the profiler writes a compact `--llm-package`. The
package is transcript-free and is the sole interpretation source.

## Measurement classes

- `facts`: exact local JSONL observations such as selected window, API usage,
  models, sessions, calls, cache reads/writes, limit incidents, and reset times.
- `derived_metrics`: deterministic calculations from facts, including totals,
  shares, rates, percentiles, context buckets, and session or turn attribution.
- `estimates`: character-derived approximations such as thinking, user text, or
  tool-result tokens. Label these as estimates and do not restate them as facts.
- `heuristic_flags`: evidence-bearing classifications such as
  `LARGE_CACHE_CREATION`. A flag suggests a pattern; it does not prove causation.
- `data_gaps`: unavailable or incomplete evidence. Preserve every listed gap and
  add a gap when a claim cannot be supported from the package.

Processed input is local work attributed from JSONL, not subscription quota
deduction. Internal quota weights, Claude.ai activity, activity from other
devices, hidden system prompts, tool schemas, and server-side compaction are not
observable (not observable means they cannot be inferred as facts here).

## Evidence citations

Support each material inference with the most specific evidence available. Cite
the session ID, turn number, timestamp, and metric name with its value, for
example: `[session abc; turn 4; 2026-08-13T22:39:44+08:00; processed=571300]`.
Use the package's exact values and units. Never fabricate a missing identifier or
timestamp; call out the missing field in `Uncertainties` and cite the narrower
window/session/metric evidence that is actually present.

## Output shape

### Facts

Report relevant `facts` and `derived_metrics` without causal language. Identify
estimates and heuristic flags inline rather than silently promoting them.

### Inferences

Explain patterns supported by cited evidence. Calibrate confidence, distinguish
correlation from cause, and do not reverse-engineer quota weights.

### Recommendations

Offer low-risk, actionable changes tied to cited findings. Recommendations are
LLM-owned judgments, not profiler facts.

### Uncertainties

List `data_gaps`, missing citation dimensions, estimates, alternative
explanations, and anything not observable from the package.
