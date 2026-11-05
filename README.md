# Token Usage

本地分析 Claude Code 和 Codex 的会话 Token 使用情况，帮助查找输入上下文中值得优化的部分。两端使用各自的日志解析和报告，不强行统一统计口径。

## 安装和使用

需要 Python 3。Codex 插件的 MCP 服务通过 `PATH` 查找 `python`。

### Claude Code

在 Claude Code 中执行：

```text
/plugin marketplace add ynymhrb/token_usage
/plugin install token-usage@token-usage
```

安装后在聊天窗使用 `/token-usage:usage-analyze`，不指定范围时分析当前会话，也可以附加自然语言范围。Claude Code 为插件技能加上插件名称作为前缀。

### Codex

```text
codex plugin marketplace add ynymhrb/token_usage
codex plugin add codex-context-optimizer@token-usage-local
```

在聊天窗选择插件的 `usage-analyze` 技能，不指定范围时分析当前会话；可附加“分析今天会话”等自然语言范围。分类基于本地历史重放估算，输入总数来自本地用量记录；两者的差额不自动分配给任何类别。

插件安装或升级后，已有聊天是否加载新版本取决于宿主的连接状态；应以实际可用工具和报告结果验证。

## 目录

```text
.
├── .agents/plugins/marketplace.json    # Codex 插件市场入口
├── .claude-plugin/                     # Claude 插件及市场清单
├── skills/usage-analyze/               # Claude 聊天技能及其内部分析脚本
├── plugins/codex-context-optimizer/    # Codex 技能、MCP 服务和钩子
├── tests/                              # 两端及插件打包测试
└── docs/                               # 设计与验证记录
```

分析器随各自的插件安装，用户通过聊天窗使用；插件内部脚本不作为独立入口。

## 验证

```text
python -m unittest discover -q
claude plugin validate .
```

运行时仅读取本地会话记录，不修改原始日志。可见正文的 Token 分类是估算，不能等同于官方逐类计数、订阅扣减或费用。
