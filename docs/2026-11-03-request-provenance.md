# 请求归因与官方用量能力核验

## 本轮范围

改善现有请求样本的 Token 分类和指令来源拆分；核实官方额度、费用和压缩接口。保持只读分析，不自动保存请求正文，不创建模型推理调用。

## 实现跟踪

- 已编码：逐请求分类顶层指令、标准 tools、动态工具、系统消息、Skill 显式说明/正文、AGENTS 显式规则、用户及助手消息、调用参数与结果；按请求标识去重，同一内容在不同请求分别计数。
- 已编码：分类表增加来源依据；基础指令中的区块也可拆分；助手引用和普通用户引用不再自动当作工作区规则。
- 已本地验证：139 项完整测试通过；`0.1.26` 安装缓存脚本与源码一致，真实数据 MCP 返回来源依据和更新后的官方数据边界。桌面斜杠命令交互仍未验收。

## 官方能力证据

- [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)：`account/rateLimits/read` 提供额度窗口已用比例与重置时间；`account/usage/read` 提供活动统计；`contextCompaction` 表示压缩事件。
- 本机官方 `generate-json-schema --experimental` 还包含 `account/usage/read` 的 `threadId` 参数，响应含 `estimatedUsageCreditsMicros`、`estimatedUsageUsdMicros`。这是估算字段，不能标成实际账单。
- 已通过宿主官方用量工具取得额度快照；现有 App Server 代理查询超时后，独立 stdio 服务查询 `account/usage/read` 成功，但 `threadUsage` 返回 null，不能当作零费用。
- [官方调试命令](https://learn.chatgpt.com/docs/developer-commands?surface=cli)：`codex debug prompt-input` 可导出当前启动配置构建的输入列表。本机执行返回四条消息；它没有历史会话选择参数，不能冒充本聊天历史的逐次请求采集，也不包含完整请求体。
- [官方 Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs) 可查询 API 组织费用，不能直接等同 ChatGPT 订阅扣减。

## 尚未解决

本机历史缺少完整请求样本时，分类仍只能采用已保存内容；本轮没有实现截获桌面聊天全部请求的采集器。未来采集需宿主提供请求观察接口，或改造请求发送层；不能用普通 MCP 调用宣称已经观察到其他请求。服务端压缩内部处理没有从上述公开接口取得；压缩事件不等于内部明细。
