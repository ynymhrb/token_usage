---
name: context-optimizer
description: 对命令输出和大文件读取使用本地上下文优化工具，避免无价值或重复内容占用 Codex 上下文。
---

# 上下文优化工具

当需要运行可能产生大量输出的命令或读取大文件时，优先使用 MCP 服务 `context_optimizer` 的 `run_command` 与 `read_file`。

- `run_command` 的 `command` 必须是参数数组，不能传 shell 字符串。
- `read_file` 只接受工作区内的相对路径。
- 返回“结果已压缩”时，完整内容仅保存在本机插件数据目录；不要要求模型重复读取同一结果。
- 已知小输出、交互式程序或需要原生工具能力的操作可使用原生工具，但这类输出不受本插件压缩控制。
