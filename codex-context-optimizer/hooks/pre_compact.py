"""压缩前钩子：只提供受控工具使用提醒，不读取或回显转录正文。"""

import json
import os
import sys
from pathlib import Path


try:
    event = json.load(sys.stdin)
    session = event.get("session_id", "unknown")
except (json.JSONDecodeError, OSError):
    session = "unknown"

data_dir = Path(os.environ.get("PLUGIN_DATA", ""))
receipts = []
if data_dir.is_dir():
    for path in data_dir.glob("*.receipt.json"):
        try:
            receipts.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
compressed = sum(bool(item.get("compressed")) for item in receipts)
duplicates = sum(bool(item.get("duplicate")) for item in receipts)
characters = sum(int(item.get("original_chars", 0)) for item in receipts)
summary = f"插件数据目录收据：{len(receipts)} 条，压缩 {compressed} 次，重复省略 {duplicates} 次，原始输出合计 {characters} 字符。"

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreCompact",
        "additionalContext": f"会话 {session} 即将压缩。{summary} 后续大文件读取和命令输出优先使用 context_optimizer 的 read_file 与 run_command；它们会保留本地完整结果并避免重复注入正文。"
    }
}, ensure_ascii=False))
