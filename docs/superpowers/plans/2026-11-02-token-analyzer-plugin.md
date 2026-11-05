# Token 分析聊天插件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地 Token 分析器暴露为聊天窗可调用的唯一 MCP 工具。

**Architecture:** 新建可测试的服务层负责范围、报告和摘要；新 MCP server 将 JSON-RPC 调用转发至服务层；插件清单注册独立 `token_analyzer` server。

**Tech Stack:** Python 标准库、unittest、MCP JSON-RPC stdio。

**Spec:** `docs/superpowers/specs/2026-11-02-token-analyzer-plugin-design.md`

## Global Constraints

- 不调用模型、网络或 shell。
- 默认范围必须是当前会话，缺少标识时报错。
- 完整报告只能写入 `D:\ai_tmp\tokenUsage`。
- 响应和摘要不得含原始请求或工具正文。

## Review Focus

- MCP server 的 `sys.path` 必须能从插件根目录导入分析器。
- `tools/list` 不能意外暴露写文件或执行命令能力。
- 非法 scope / focus 必须返回错误而非扩大全历史。
- `focus=context` 与完整分析必须使用相同范围筛选。
- 新服务的输出路径不得被调用参数覆盖。

---

### Task 1: 本地分析服务

**Files:**
- Create: `codex_token/plugin_service.py`
- Test: `test_codex_plugin_service.py`

**Interfaces:**
- Produces: `run_plugin_analysis(scope: str | None, focus: str | None, sessions_root: Path, output_dir: Path, current_session_id: str | None, now: datetime) -> dict[str, object]`

- [x] 写出默认范围、上下文重点、非法重点和固定输出目录的失败测试。
- [x] 运行测试确认服务不存在。
- [x] 实现本地分析、报告写入和无正文摘要。
- [x] 运行服务测试并提交。

### Task 2: MCP 注册与 JSON-RPC 入口

**Files:**
- Create: `codex-context-optimizer/scripts/token_analyzer_server.py`
- Modify: `codex-context-optimizer/.mcp.json`
- Test: `test_token_analyzer_server.py`

**Interfaces:**
- Consumes: `run_plugin_analysis`。
- Produces: `tools/list` 中的 `analyze`，以及 `tools/call` 的紧凑文本结果。

- [x] 写出 JSON-RPC tools/list、合法调用和非法参数失败测试。
- [x] 运行测试确认 server 不存在。
- [x] 注册 MCP server 并实现 stdio JSON-RPC 转发。
- [x] 跑完整测试、实机 stdio 调用和提交。
