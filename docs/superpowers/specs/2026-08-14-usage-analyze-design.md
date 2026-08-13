# Usage Analyze 设计规格

## 目标

将 Claude Code 本地 JSONL 分析器产品化为两个共享同一分析核心的入口：

1. `usage-analyze` / `ua`：直接在 shell 中运行，完全不调用 LLM。
2. `/usage-analyze`：Claude Code Skill，在 forked subagent 中解析参数、运行脚本，并按需分析。

分析器自身只输出事实、派生指标、估算值和数据缺口。情境化解释与建议由 LLM 完成。

## 用户入口

### Shell：零模型调用

```powershell
usage-analyze
ua --five-hour-at "2026-08-13 22:00"
usage-analyze --current-five-hour --json-out report.json
```

无参数默认分析当前可识别的 5 小时周期。Shell 入口直接执行 Python，不经过 Claude Code。

### Skill：隔离模型上下文

```text
/usage-analyze
/usage-analyze --raw --five-hour-at "2026-08-13 22:00"
/usage-analyze --llm --five-hour-at "2026-08-13 22:00"
/usage-analyze 最近一次为什么耗尽这么快
```

路由规则：

- 无参数：分析当前 5 小时周期并解释结果。
- 全部是已知 CLI 参数且包含 `--raw`：原样返回脚本事实报告，不生成诊断建议。
- 包含 `--llm`：执行脚本后分析 LLM 数据包。
- 包含自然语言或未知参数：subagent 先解析意图和时间范围，再执行并分析。
- Skill 始终设置 `context: fork` 和 `disable-model-invocation: true`，只允许用户显式调用，并隔离分析过程。

`/usage-analyze --raw` 仍需要 Skill 的一次模型执行，不能宣称零 Token；真正零模型入口是 shell 命令。

## 分析器职责边界

### 精确事实

- JSONL API usage 字段
- 调用时间、模型、会话、轮次
- cache read/write 与声明的 TTL
- 明确的 rate-limit/429 和 reset 时间
- statusLine 捕获值

### 派生指标

- 总量、比例、每小时速率
- P50/P75/P90/P95/最大上下文
- `>100k`、`>200k`、`>500k` 分桶
- 会话和轮次贡献
- 缓存创建比例及相邻事件时间差

### 估算值

- Thinking、用户文本、工具结果的字符启发式 token

### 不可观测项

- Claude 订阅额度的内部换算权重
- Claude.ai 和其他设备使用量
- 隐藏系统提示、工具 schema、服务端压缩细节

## 通用化变更

- 删除默认 `CRITICAL/HIGH/MEDIUM/LOW`。
- 删除内置固定优化建议。
- 将 `>150k` 替换为分位数和固定观察桶；另提供可重复的 `--context-threshold`。
- 将“主要责任会话”改为“本机处理输入贡献最高会话”。
- 将 `FULL_REWRITE` 改为 `LARGE_CACHE_CREATION`。
- 大额缓存创建默认阈值为：绝对值至少 100k 且占该请求上下文至少 70%；阈值写入报告元数据并允许 CLI 覆盖。
- 原因分类只作启发式标志：首次缓存建立、疑似缓存过期/未命中、疑似前缀变化、未知。每个标志携带依据和置信度。
- 旧 JSON 字段在一个兼容版本内保留并标记 deprecated，避免已有消费者立即中断。

## 结构化输出

JSON 顶层结构：

```json
{
  "schema_version": "1.0",
  "profiler_version": "...",
  "facts": {},
  "derived_metrics": {},
  "estimates": {},
  "heuristic_flags": [],
  "data_gaps": [],
  "sessions": [],
  "calls": []
}
```

新增 `--llm-package` 输出紧凑 JSON/Markdown，只保留诊断所需字段。包内要求分析者：

- 分开陈述事实、推断与建议；
- 不把 processed input 等同于订阅扣减；
- 每项推断引用具体指标；
- 对不可观测项明确保留意见；
- 优先给低风险、可操作建议。

## Skill 目录

项目内先创建可测试版本：

```text
.claude/skills/usage-analyze/
├── SKILL.md
├── scripts/
│   ├── claude_session_token_profiler.py
│   └── usage-analyze.ps1
└── references/
    └── analysis-contract.md
```

验证后再复制到 `~/.claude/skills/usage-analyze/` 作为个人 Skill；该写入位于工作区外，需要用户授权。

Shell 包装器解析自身路径，不依赖当前工作目录。可选安装阶段把 `usage-analyze` 和 `ua` 注册到用户 PowerShell profile；修改 profile 前单独征求授权。

## 错误处理

- 无法定位 5 小时窗口：显示可识别窗口并提示使用明确时间参数。
- Python 不可用：输出明确安装错误，不回退到 LLM 猜测。
- JSONL 部分损坏：继续读取有效行，在 `data_gaps` 中记录行数。
- 自然语言存在多个合理时间范围：subagent说明采用的解释；只有会显著改变结果时才询问用户。
- subagent 分析失败：仍返回脚本事实报告和 LLM 数据包路径。

## 测试与验收

### 分析器

- 分位数和上下文桶使用手算 fixture。
- 大额缓存创建区分首次建立与后续疑似重建。
- 无严重度和固定建议残留。
- 新旧兼容字段一致。
- 会话、轮次和调用总量守恒。

### Shell

- 无参数、明确周期、历史时间点和 JSON 输出。
- 从任意工作目录执行。
- 不启动 `claude` 子进程。

### Skill

- `/usage-analyze --raw ...` 只返回事实报告。
- `/usage-analyze --llm ...` 分开事实、推断和建议。
- 自然语言正确映射到时间范围并分析。
- 使用 fresh-context subagent 前向测试，确认结果不依赖本对话泄漏。

## 非目标

- 复刻 Claude 服务端订阅额度算法。
- 声称本地 JSONL 覆盖账户全部使用量。
- 自动清理、compact 或终止用户会话。
- 自动修改 Claude Code 套餐、模型或 effort 配置。
