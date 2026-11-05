---
name: usage-analyze
description: 在 Codex 聊天窗分析本地 Token 消耗；支持当前会话或中文时间范围。用户输入 /usage-analyze 时使用。
---

# Codex Token 分析

调用 MCP 服务 `token_analyzer` 的 `analyze` 工具，默认分析当前会话。

- 先发现 MCP 工具，再调用 `mcp__token_analyzer__analyze`。工具可能延迟加载，不要仅凭初始工具列表缺少名称就判定未安装。若提供 `functions.exec`，先筛选 `ALL_TOOLS` 中名称包含 `token_analyzer` 的条目，再通过 `tools` 调用发现的准确名称；其他运行时使用其工具搜索入口。
- 没有参数或用户指定“当前会话”时，先通过当前聊天的命令执行工具读取 `CODEX_THREAD_ID`，缺失时读取 `CODEX_SESSION_ID`。只读取这两个环境变量，不扫描历史、不猜测最新会话。将取得的标识作为 `{ "session_id": "<会话标识>" }` 传给 MCP 的 `analyze`；若无法取得标识，报告错误并停止。命令执行只用于取得标识，分析必须由 MCP 完成。
- 可用的跨平台读取命令：`python -c "import os; print(os.getenv('CODEX_THREAD_ID') or os.getenv('CODEX_SESSION_ID') or '')"`。读取结果仅用于 MCP 参数，不作为用户可见报告内容。
- 用户提供时间范围时，调用 `{ "scope": "<完整参数>" }`。允许的范围包括“今天”“昨天”“最近 N 小时”“YYYY-MM-DD HH:MM 至 YYYY-MM-DD HH:MM”和“全部历史”。
- 若用户明确要求分析上下文构成，附加 `{ "focus": "context" }`；若仅要求工具消耗，附加 `{ "focus": "tools" }`。
- 工具返回的是完整 Markdown 诊断。必须原样转发全部内容，不得压缩、改写、摘要、追加报告路径或生成文件；不要改用五小时周期，不要调用 Claude 分析器。
- 图表必须保留为顶层的三反引号 mermaid 代码块。不得在报告或图表外再套 yaml、markdown、text 或四反引号代码块，也不要用反斜杠转义围栏；额外包装会让图表显示为源码。发送前检查每个 mermaid 围栏只出现一层。
- 完成工具发现仍不可用或 MCP 调用失败时，报告具体发现结果或错误并停止。不得运行本地分析脚本替代 MCP，不得改用 Claude 分析器。

本工具默认只分析本地记录，不查询或展示实时账户额度。小时用量使用柱状图，会话占比使用饼图；保留返回的 Mermaid 图表代码块以供聊天窗渲染，不改写为表格。范围不明确时，提示用户使用上述范围格式；不要扩大为历史扫描。
