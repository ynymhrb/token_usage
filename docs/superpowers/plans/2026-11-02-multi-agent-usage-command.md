# 多 Agent Token 分析命令实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Claude Code 和 Codex 中提供同名、默认当前会话的 `/usage-analyze`。

**Architecture:** 保留 Claude 的独立 Skill 和原生报告；在 Codex 插件中增加独立同名 Skill，调用现有本地 MCP 分析器。两端不共享日志解析或报告格式。

**Tech Stack:** Markdown Skill、Codex 插件清单、Python `unittest`。

**Spec:** `docs/superpowers/specs/2026-11-02-multi-agent-usage-command-design.md`

## Global Constraints

- 所有分析只读本地数据。
- 缺省范围只能是当前会话。
- Codex 适配层不得依赖 Claude 专有数据或命令。

## Review Focus

- 无参数时必须写明默认当前会话，不能回退为五小时窗口。
- 自然语言范围必须经 `scope` 传给 Codex MCP 服务。
- Claude 专有标识不能出现在 Codex Skill。
- 已安装缓存中必须包含新增 Skill。

### Task 1: Codex 命令适配层

**Files:**
- Create: `codex-context-optimizer/skills/usage-analyze/SKILL.md`
- Modify: `test_codex_plugin_package.py`

**Interfaces:**
- Consumes: MCP 服务 `token_analyzer.analyze(scope?: string, focus?: string)`。
- Produces: Codex 可发现的 `usage-analyze` Skill。

- [ ] **Step 1: 写入失败测试**

断言 Skill 存在、声明 `usage-analyze`、默认当前会话、把范围传为 `scope`，且不出现 Claude 专有实现。

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m unittest test_codex_plugin_package.PluginPackageTests.test_plugin_ships_codex_usage_analyze_skill -v`

预期：因 Skill 文件不存在而失败。

- [ ] **Step 3: 创建 Codex Skill**

添加 Markdown 指令：调用 `token_analyzer.analyze`；无参数不传 `scope`，有参数原样作为 `scope`；返回 MCP 的紧凑摘要和报告路径。

- [ ] **Step 4: 运行测试确认通过**

运行：`python -m unittest test_codex_plugin_package.PluginPackageTests.test_plugin_ships_codex_usage_analyze_skill -v`

预期：通过。

- [ ] **Step 5: 运行插件包回归测试**

运行：`python -m unittest test_codex_plugin_package.py test_token_analyzer_server.py -v`

预期：全部通过。
