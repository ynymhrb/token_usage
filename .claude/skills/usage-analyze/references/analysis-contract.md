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

## Fixed output template

Write in Chinese and use exactly these six top-level headings in this order. Do
not rename, merge, omit, or add top-level headings. Do not output any content
before the first heading or after the completion marker.

```markdown
# Claude Usage 分析

## 1. 脚本原始输出

```text
<SCRIPT_STDOUT 的完整报告正文；只删除 LLM package written 临时路径行>
```

## 2. 核心结论

- <3–6 条最重要结论，每条给出数值证据引用>

## 3. 消耗证据

| 观察项 | 脚本数据 | 判断 |
|---|---:|---|
| <上下文/会话/轮次/缓存/Thinking/额度事件> | <原值> | <事实或估算标签> |

## 4. 原因分析

1. **<原因>**：<证据支持的推断与置信度；引用 session/turn/time/metric>。

## 5. 改进建议

1. **<优先级与动作>**：<说明它针对哪条证据，以及预期改善什么>。

## 6. 局限与不确定性

- <逐条列出 data_gaps、边界置信度、估算与不可观测项>

<!-- USAGE_ANALYZE_COMPLETE -->
```

“分析”不能只是复述总量。至少比较会话贡献、主轮次贡献、上下文分布、
cache read/write、LARGE_CACHE_CREATION、Thinking 估算和额度事件中实际存在的
项目，并指出主要驱动项、时间邻近关系和可采取动作。没有证据时明确写
“脚本数据不足以判断”，不得填充泛化建议。

Display this entire block directly in the current conversation. Do not compress
it into a “quick summary” or append unrelated pending work.

## Section semantics

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
