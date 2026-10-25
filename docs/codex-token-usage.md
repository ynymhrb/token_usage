# Codex Token 工具使用说明

`python -m codex_token analyze --sessions-root PATH` 仅在本机读取 Codex rollout，并默认输出脱敏 JSON。

`python -m codex_token recommend REPORT.json` 只读取已经脱敏的报告，拒绝原始 JSONL。

Codex 的本地转录格式不是公开稳定协议；成本字段只是估算的前提数据，不等于订阅的真实扣费。工具默认不修改仓库，也不调用 Codex。

`python -m codex_token experiment plan experiments/token-optimization-v1.json` 仅生成计划；真实执行必须另获用户明确授权。

首版未随清单版本化模型价格表，因此即使传入 `experiment run --execute --budget-usd` 也会在创建工作树或调用 Codex 前拒绝执行，避免把 Token 上限误报为美元预算。受控执行的结果收据只应保存任务标识、指标、停止原因与验证结论，不保存提示词、候选指令或命令输出；需要写入文件时应由调用方指定 `D:\ai_tmp` 下的路径。
