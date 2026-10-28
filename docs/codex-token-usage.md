# Codex Token 工具使用说明

`python -m codex_token analyze --sessions-root PATH` 仅在本机读取 Codex rollout，并默认输出脱敏 JSON。

`python -m codex_token recommend REPORT.json` 只读取已经脱敏的报告，拒绝原始 JSONL。

## 上下文构成归因

`python -m codex_token context-breakdown --sessions-root PATH --output D:\\ai_tmp\\tokenUsage\\context.json` 会读取保存的 `*.llm_calls.jsonl` 请求结构，分别输出静态指令、工具定义、开发者/用户/助手消息、工具参数、工具结果和不可归因项的占比，并给出优先优化建议。

这个命令不会回显请求或工具正文。它用稳定 JSON 字符数作为结构占比代理，因此能回答“哪一类上下文最臃肿”，但不是精确 Token 账单。只有请求结构与同一次调用的 `token_usage_record` 可验证关联时，才能进一步给每一类校准 Token；当前本机历史导出不具备该关联，报告会明确显示这个缺口，而不会把全局 Token 总量硬分摊。

Codex 的本地转录格式不是公开稳定协议；成本字段只是估算的前提数据，不等于订阅的真实扣费。工具默认不修改仓库，也不调用 Codex。

`python -m codex_token experiment plan experiments/token-optimization-v1.json` 仅生成计划；真实执行必须另获用户明确授权。

首版未随清单版本化模型价格表，因此即使传入 `experiment run --execute --budget-usd` 也会在创建工作树或调用 Codex 前拒绝执行，避免把 Token 上限误报为美元预算。受控执行的结果收据只应保存任务标识、指标、停止原因与验证结论，不保存提示词、候选指令或命令输出；需要写入文件时应由调用方指定 `D:\ai_tmp` 下的路径。

## 上下文优化插件

`codex-context-optimizer` 是本地 Codex 插件包。它提供 `run_command` 与 `read_file` 两个受控 MCP 工具：大输出在返回模型前被压缩，完整内容仅写入插件数据目录；同一内容重复出现时仅返回引用标签。模型可见输出会脱敏常见密钥、Bearer 凭据、GitHub 令牌、私钥块和数据库连接串。每次调用都会写一条不含原文的本地收据，压缩前钩子据此汇总当前插件数据目录的收据统计并提示后续轮次优先使用受控工具。

安装本仓库的本地插件后，信任其 hooks，并在新的 Codex 会话中使用 `context_optimizer` MCP 服务。原生 Bash、原生文件工具的输出没有公开替换接口，仍不受该插件控制；对大输出操作应主动使用受控工具。
