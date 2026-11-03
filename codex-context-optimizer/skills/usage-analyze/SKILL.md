---
name: usage-analyze
description: 在 Codex 聊天窗分析本地 Token 消耗；支持当前会话或中文时间范围。用户输入 /usage-analyze 时使用。
---

# Codex Token 分析

调用 MCP 服务 `token_analyzer` 的 `analyze` 工具。

- 先确认当前可调用工具集合中存在 `token_analyzer.analyze`。若不存在，直接说明“Token 分析 MCP 未附加到此聊天；请在安装或更新插件后新开本地 Codex 聊天再试”，随后停止。
- 没有命令参数时，调用 `{}`：默认分析当前会话。
- 用户提供范围时，调用 `{ "scope": "<完整参数>" }`。允许的范围包括“当前会话”“今天”“昨天”“最近 N 小时”“YYYY-MM-DD HH:MM 至 YYYY-MM-DD HH:MM”和“全部历史”。
- 若用户明确要求分析上下文构成，附加 `{ "focus": "context" }`；若仅要求工具消耗，附加 `{ "focus": "tools" }`。
- 工具返回的是完整 Markdown 诊断。必须原样转发全部内容，不得压缩、改写、摘要、追加报告路径或生成文件；不要改用五小时周期，不要调用 Claude 分析器。
- 工具缺失、连接失败或调用失败时，不要调用 exec，不要改用任何其他工具或命令，不得继续其他任务；只报告该失败并停止。

本工具只读取本机 Codex 数据。范围不明确时，提示用户使用上述范围格式；不要扩大为历史扫描。
