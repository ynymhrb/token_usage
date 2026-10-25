"""压缩前钩子：只提供受控工具使用提醒，不读取或回显转录正文。"""

import json
import sys


try:
    event = json.load(sys.stdin)
    session = event.get("session_id", "unknown")
except (json.JSONDecodeError, OSError):
    session = "unknown"

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreCompact",
        "additionalContext": f"会话 {session} 即将压缩。后续大文件读取和命令输出优先使用 context_optimizer 的 read_file 与 run_command；它们会保留本地完整结果并避免重复注入正文。"
    }
}, ensure_ascii=False))
