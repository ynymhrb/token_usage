# Codex 上下文构成归因 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从保存的 Codex 请求结构生成可审计的上下文类别占比和优化建议。

**Architecture:** 新模块解析 `*.llm_calls.jsonl`，仅记录各类别的字符量和调用标识；命令行将其汇总为校准估算 Token，并与原始 Token 总量明确分列。

**Tech Stack:** Python 标准库、unittest。

**Spec:** `docs/superpowers/specs/2026-10-28-codex-context-attribution-design.md`

## Global Constraints

- 不调用 Codex 或其他付费模型。
- 不在报告中保存或回显请求正文。
- 精确 Token 与估算分类 Token 必须分开标注。

## Review Focus

- 合并后的静态指令不能伪称系统提示或 Skill 的精确 Token。
- 工具调用参数和工具结果必须分开。
- 缺少请求结构的会话必须产生覆盖缺口。
- 未知项与推理密文必须计入不可归因类别。
- 输出不得泄漏请求正文或工具输出。

---

### Task 1: 请求结构分类器

**Files:**
- Create: `codex_token/context_attribution.py`
- Test: `test_codex_context_attribution.py`

- [x] 写出会失败的分类与脱敏测试。
- [x] 实现请求结构发现、类别字符计数与未知项归类。
- [x] 运行该测试并提交。

### Task 2: 报告与建议

**Files:**
- Modify: `codex_token/cli.py`
- Modify: `codex_token/recommendations.py`
- Test: `test_codex_cli.py`

- [ ] 写出会失败的命令行报告测试。
- [ ] 实现 `context-breakdown` 只读命令、校准估算和类别建议。
- [ ] 运行全量测试、实机只读分析并提交。
