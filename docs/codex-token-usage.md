# Codex Token 工具使用说明

`python -m codex_token analyze --sessions-root PATH` 仅在本机读取 Codex rollout，并默认输出脱敏紧凑摘要。

现在默认范围是当前会话：命令优先读取 `CODEX_THREAD_ID`、其次读取 `CODEX_SESSION_ID`；无法取得会话标识时拒绝执行，避免误扫全部历史。可用 `--session ID` 显式指定会话，或用 `--scope` 覆盖默认范围，例如 `--scope "今天"`、`--scope "最近 2 小时"`、`--scope "2026-11-02 09:00 至 2026-11-02 18:00"`。只有 `--scope "全部历史"` 才会扫描所有本地历史。

默认标准输出只有范围、匹配 rollout 数和报告路径等紧凑摘要；使用 `--output D:\\ai_tmp\\tokenUsage\\report.json` 才写入完整脱敏 JSON。`--format json` 或 `--format md` 会将完整报告输出到终端，适合人工调试，不适合直接回填给模型。

`python -m codex_token recommend REPORT.json` 只读取已经脱敏的报告，拒绝原始 JSONL。

## 上下文构成归因

`python -m codex_token context-breakdown --sessions-root PATH --output D:\\ai_tmp\\tokenUsage\\context.json` 会读取保存的 `*.llm_calls.jsonl` 请求结构，分别输出静态指令、工具定义、开发者/用户/助手消息、工具参数、工具结果和不可归因项的占比，并给出优先优化建议。

这个命令不会回显请求或工具正文。它用稳定 JSON 字符数作为结构占比代理，因此能回答“哪一类上下文最臃肿”，但不是精确 Token 账单。只有请求结构与同一次调用的 `token_usage_record` 可验证关联时，才能进一步给每一类校准 Token；当前本机历史导出不具备该关联，报告会明确显示这个缺口，而不会把全局 Token 总量硬分摊。

Codex 的本地转录格式不是公开稳定协议；成本字段只是估算的前提数据，不等于订阅的真实扣费。工具默认不修改仓库，也不调用 Codex。

`python -m codex_token experiment plan experiments/token-optimization-v1.json` 仅生成计划；真实执行必须另获用户明确授权。

首版未随清单版本化模型价格表，因此即使传入 `experiment run --execute --budget-usd` 也会在创建工作树或调用 Codex 前拒绝执行，避免把 Token 上限误报为美元预算。受控执行的结果收据只应保存任务标识、指标、停止原因与验证结论，不保存提示词、候选指令或命令输出；需要写入文件时应由调用方指定 `D:\ai_tmp` 下的路径。

## 上下文优化插件

`plugins/codex-context-optimizer/` 是本地 Codex 插件包。它提供 `run_command` 与 `read_file` 两个受控 MCP 工具：大输出在返回模型前被压缩，完整内容仅写入插件数据目录；同一内容重复出现时仅返回引用标签。模型可见输出会脱敏常见密钥、Bearer 凭据、GitHub 令牌、私钥块和数据库连接串。每次调用都会写一条不含原文的本地收据，压缩前钩子据此汇总当前插件数据目录的收据统计并提示后续轮次优先使用受控工具。

安装本仓库的本地插件后，信任其 hooks，并在新的 Codex 会话中使用 `context_optimizer` MCP 服务。原生 Bash、原生文件工具的输出没有公开替换接口，仍不受该插件控制；对大输出操作应主动使用受控工具。

## 聊天窗 Token 分析

同一插件还提供 `token_analyzer.analyze`。在**新开的 Codex 聊天**中，直接说“分析当前会话 Token”或“分析今天的 Token 消耗”；Agent 应调用该工具。它只接收可选的范围 `scope` 和重点 `focus`（`full`、`context`、`tools`），默认当前会话，不接受任意文件路径或命令。

工具仅在本机读取会话记录，聊天窗只返回范围、匹配数量、核心统计和报告路径；完整脱敏 JSON 固定写入 `D:\\ai_tmp\\tokenUsage`。如果宿主没有提供当前会话标识，默认范围会失败而不会扫描全历史；“全部历史”必须显式指定。
