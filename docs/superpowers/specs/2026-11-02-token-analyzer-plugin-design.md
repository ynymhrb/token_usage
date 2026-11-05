# 聊天窗 Token 分析插件设计

## 目标

在已安装的 `codex-context-optimizer` 插件中新增 MCP 服务 `token_analyzer`，向新 Codex 聊天提供唯一工具 `analyze`。工具默认分析当前会话，支持自然语言范围和分析重点，所有处理均为本地读取与聚合。

## 工具契约

`token_analyzer.analyze` 的参数：

- `scope`：可选字符串；沿用范围解析器，默认当前会话。
- `focus`：可选枚举：`full`、`context`、`tools`；默认 `full`。

工具返回不含原文的紧凑中文摘要，包含范围、匹配 rollout 数、核心统计或上下文热点和报告路径。完整脱敏 JSON 固定写入 `D:\ai_tmp\tokenUsage`；调用方不能提供任意输出路径。

## 实现边界

- 独立 MCP 服务器仅处理 JSON-RPC stdin/stdout。
- 服务器将插件根目录加入 `sys.path` 后调用已有 `codex_token` 包，不经 shell，也不调用模型。
- 当前会话标识取 `CODEX_THREAD_ID`，再取 `CODEX_SESSION_ID`；缺失时默认范围拒绝执行。
- `focus=context` 调用上下文构成分析；`focus=full` 和 `focus=tools` 调用 Token 分析，其中 `tools` 暂仅改变摘要标题，不伪造工具细分。
- 无法解析范围、非法 focus、文件读取异常返回 MCP 错误文本，不回显原始内容。

## 验收

- `tools/list` 只暴露一个 `analyze` 工具。
- 默认当前会话范围不会扩大到全历史。
- MCP 调用生成 `D:\ai_tmp\tokenUsage` 内的 JSON，并返回摘要与路径。
- 请求/工具正文不出现在响应或报告摘要。
