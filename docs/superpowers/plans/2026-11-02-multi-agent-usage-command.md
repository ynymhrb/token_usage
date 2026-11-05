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
- Claude 无参数时必须只传入当前项目的当前会话 ID。

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

### Task 2: Claude 当前会话默认值

**Files:**
- Create: `scripts/current_session.py`
- Create: `.claude/skills/usage-analyze/scripts/current_session.py`
- Modify: `.claude/skills/usage-analyze/scripts/route_usage_analyze.py`
- Modify: `scripts/usage-analyze.ps1`
- Modify: `.claude/skills/usage-analyze/scripts/usage-analyze.ps1`
- Test: `test_claude_current_session.py`

**Interfaces:**
- Consumes: `~/.claude/history.jsonl` 的项目路径与会话 ID 元数据。
- Produces: 当前工作目录的最新有效 Claude 会话 ID，或明确错误。

- [ ] **Step 1: 写入失败测试**

断言缺省路由为 `--current-session`，解析器只取当前项目的最后一个有效会话 ID。

- [ ] **Step 2: 运行测试确认失败**

运行：`python -m unittest test_usage_analyze_skill.UsageAnalyzeSkillTests.test_router_defaults_and_raw_mapping_contract test_claude_current_session -v`

预期：旧的五小时默认值或缺少解析器导致失败。

- [ ] **Step 3: 实现当前会话解析与转发**

解析器反向读取本地历史，仅比较项目路径和 UUID；PowerShell 包装器将 `--current-session` 转为位置会话 ID 后调用既有分析器。无匹配必须退出，不得扫描五小时历史。

- [ ] **Step 4: 运行全量回归**

运行：`python -m unittest discover -v`

预期：全部通过。
