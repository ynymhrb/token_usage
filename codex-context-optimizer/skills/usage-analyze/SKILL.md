---
name: usage-analyze
description: 在 Codex 聊天窗分析本地 Token 消耗；支持当前会话或中文时间范围。用户输入 /usage-analyze 时使用。
---

# Codex Token 分析

调用 MCP 服务 `token_analyzer` 的 `analyze` 工具。

- 没有命令参数时，调用 `{}`：默认分析当前会话。
- 用户提供范围时，调用 `{ "scope": "<完整参数>" }`。允许的范围包括“当前会话”“今天”“昨天”“最近 N 小时”“YYYY-MM-DD HH:MM 至 YYYY-MM-DD HH:MM”和“全部历史”。
- 若用户明确要求分析上下文构成，附加 `{ "focus": "context" }`；若仅要求工具消耗，附加 `{ "focus": "tools" }`。
- 直接返回工具给出的紧凑摘要和脱敏报告路径。不要改用五小时周期，不要调用 Claude 分析器。

本工具只读取本机 Codex 数据。范围不明确时，提示用户使用上述范围格式；不要扩大为历史扫描。
