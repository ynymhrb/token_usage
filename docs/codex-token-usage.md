# Codex Token 工具使用说明

`python -m codex_token analyze --sessions-root PATH` 仅在本机读取 Codex rollout，并默认输出脱敏 JSON。

`python -m codex_token recommend REPORT.json` 只读取已经脱敏的报告，拒绝原始 JSONL。

Codex 的本地转录格式不是公开稳定协议；成本字段只是估算的前提数据，不等于订阅的真实扣费。工具默认不修改仓库，也不调用 Codex。

`python -m codex_token experiment plan experiments/token-optimization-v1.json` 仅生成计划；真实执行必须另获用户明确授权。
