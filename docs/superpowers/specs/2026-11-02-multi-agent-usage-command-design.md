# 多 Agent Token 分析命令设计

## 目标

Claude Code 与 Codex 聊天窗统一使用 `/usage-analyze [范围]`。未给范围时只分析当前会话；给出范围时，仅从当前宿主的本地历史中筛选。

## 边界

- Claude Code 保留既有 Claude JSONL、五小时窗口和 `statusLine` 分析；缺省时从当前项目本地历史解析当前会话 ID，不回退为五小时窗口。
- Codex 使用已存在的本地 `token_analyzer.analyze` MCP 工具，支持“当前会话、今天、昨天、最近 N 小时、明确时间范围、全部历史”。
- 两端报告内容不强行统一；各自明确其可观测数据与缺口。
- 不上传日志、不调用模型、不扫描未被用户范围允许的 Codex 历史。

## 方案

将共享的命令名称实现为两个薄宿主适配层：Claude 的同名 Skill 先解析当前项目的当前会话，再调用 Claude 分析器；Codex 插件新增同名 Skill，直接调用 `token_analyzer.analyze`。插件安装后的新聊天可从斜杠列表发现该 Skill。

## 验收

- Codex 插件包包含 `skills/usage-analyze/SKILL.md`。
- Skill 明确默认当前会话，且将自然语言范围作为 `scope` 传给 MCP 工具。
- Skill 不引用 Claude 的分析器或 `statusLine`。
- Claude Skill 仅修改缺省范围解析，不改变其 Claude 专用报告结构。
- Claude 无参数不会再路由到五小时窗口。
