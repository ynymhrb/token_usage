# Codex 分析范围 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Codex Token 命令默认分析当前会话，并安全解析明确自然语言范围。

**Architecture:** 新增纯函数范围解析模块，输出不含会话正文的范围选择结果；命令行先筛选 rollout 路径，再交给既有解析器；完整 JSON 写入文件时控制台只输出紧凑摘要。

**Tech Stack:** Python 标准库、unittest。

**Spec:** `docs/superpowers/specs/2026-11-02-codex-analysis-scope-design.md`

## Global Constraints

- 不调用模型或网络服务。
- 无法确定范围时拒绝，不扫描全历史。
- 报告和标准输出不得含请求或工具正文。

## Review Focus

- 没有环境会话标识时不得退化为全量扫描。
- 模糊自然语言必须失败而非猜测。
- `全部历史` 必须是唯一可触发全量扫描的自然语言。
- 时间范围使用 rollout 起始时间且必须在报告中标明。
- 紧凑标准输出不能泄漏原始内容。

---

### Task 1: 范围解析和路径筛选

**Files:**
- Create: `codex_token/scope.py`
- Test: `test_codex_scope.py`

**Interfaces:**
- Produces: `resolve_scope(expression: str | None, session_id: str | None, now: datetime) -> AnalysisScope`
- Produces: `filter_rollout_paths(paths: Iterable[Path], scope: AnalysisScope) -> list[Path]`

- [x] 写出当前会话、显式会话、今天、最近 N 小时、全部历史和模糊范围失败的测试。
- [x] 运行测试确认范围模块不存在而失败。
- [x] 实现不可变范围模型、中文范围解析和文件名起始时间筛选。
- [x] 运行范围测试并提交。

### Task 2: 命令行范围与紧凑摘要

**Files:**
- Modify: `codex_token/cli.py`
- Modify: `test_codex_cli.py`

**Interfaces:**
- Consumes: `resolve_scope` 与 `filter_rollout_paths`。
- Produces: `analyze` / `context-breakdown` 的范围元数据和仅含计数的标准输出摘要。

- [x] 写出默认当前会话、缺少会话标识报错、`--scope` 覆盖默认和 `--output` 写完整报告的测试。
- [x] 运行测试确认命令行尚不支持这些参数。
- [x] 实现 `--session`、`--scope`、`--output` 与范围元数据；不传 `--output` 时只输出紧凑摘要。
- [x] 跑完整测试集、在本地会话目录只读实机验证并提交。
