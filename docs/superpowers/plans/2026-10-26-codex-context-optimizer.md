# Codex 上下文优化插件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付能在受控工具结果进入模型前压缩上下文的本地 Codex 插件。

**Architecture:** Python 优化核心由 MCP 服务调用；插件清单注册该服务和压缩生命周期钩子。完整结果只留在插件数据目录，模型只接收受预算约束的摘要或重复引用。

**Tech Stack:** Python 标准库、JSON-RPC stdio MCP、Codex 插件清单与 hooks。

**Spec:** `docs/superpowers/specs/2026-10-26-codex-context-optimizer-design.md`

## Global Constraints

- 默认本地、UTF-8、无原始工具输出进入报告或收据。
- 不修改 Codex 原生工具，不执行真实付费会话。
- 所有模型可见工具结果默认不超过 4,000 字符。

## Review Focus

- 含密钥的大输出：压缩收据和异常不得回显正文；由核心测试覆盖。
- 命令失败：错误线索必须优先保留；由命令工具测试覆盖。
- 重复但不同来源的结果：必须按内容指纹去重；由核心测试覆盖。
- 非 UTF-8 文件：必须安全降级；由读取工具测试覆盖。
- 非插件原生工具：必须在文档中标注为不受控；由清单与技能测试覆盖。

### Task 1: 上下文压缩核心

**Files:** 创建 `codex_token/context_optimizer.py`；测试 `test_context_optimizer.py`。

- [ ] 编写大结果、错误、重复与隐私失败测试。
- [ ] 实现 `ContextOptimizer.optimize(text, source, failed) -> OptimizedResult`。
- [ ] 运行 `python -m unittest test_context_optimizer -v`。

### Task 2: 本地 MCP 受控工具

**Files:** 创建 `codex_token/mcp_server.py`；测试 `test_codex_mcp_server.py`。

- [ ] 编写 stdio 协议、命令和文件读取失败测试。
- [ ] 实现 `tools/list`、`tools/call`、`run_command`、`read_file`。
- [ ] 运行 `python -m unittest test_codex_mcp_server -v`。

### Task 3: 插件与钩子封装

**Files:** 创建 `codex-context-optimizer/.codex-plugin/plugin.json`、`.mcp.json`、`hooks/hooks.json`、钩子脚本与技能；测试 `test_codex_plugin_package.py`。

- [ ] 编写清单、Windows 命令和钩子输出预算测试。
- [ ] 接通 MCP 服务和压缩前摘要钩子。
- [ ] 运行插件包装测试。

### Task 4: 文档与全量验证

**Files:** 修改 `docs/codex-token-usage.md`；测试所有相关模块。

- [ ] 说明受控工具路径、安装与原生工具限制。
- [ ] 运行 `python -m unittest discover -v` 与 `git diff --check`。
