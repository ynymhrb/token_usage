"""自包含的插件 MCP 运行时；安装缓存中不依赖仓库源码。"""

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def redact(text):
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "[私钥已脱敏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b(?:api[_-]?key|token|password|secret|aws_secret_access_key|aws_(?:session_)?token|access[_-]?key|client_secret|private_key)\b\s*[:=]\s*[^\s,;]+", "[凭据已脱敏]", text)
    text = re.sub(r"(?i)(authorization\s*:\s*[a-z][a-z0-9_-]*\s+)[^\s]+", r"\1[已脱敏]", text)
    text = re.sub(r"\b(?:gh[pousr]_\w{20,}|github_pat_\w{20,})\b", "[GitHub 令牌已脱敏]", text)
    text = re.sub(r"(?i)https?://[^\s/@:]+:[^@\s]+@[^\s]+", "[带凭据 URL 已脱敏]", text)
    return re.sub(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+", "[连接串已脱敏]", text)


def output(text, data, source, failed=False):
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    data.mkdir(parents=True, exist_ok=True)
    storage_name = f"{source}-{digest}.txt"
    marker_path = data / f"{digest}.seen"
    duplicate = marker_path.exists()
    (data / storage_name).write_text(text, encoding="utf-8")
    marker_path.touch(exist_ok=True)
    if duplicate:
        visible = f"重复结果已省略：{digest}；原始长度 {len(text)} 字符。"
    else:
        visible = redact(text)
    compressed = duplicate or len(text) > 4000
    if not duplicate and len(visible) > 4000:
        errors = [redact(line) for line in text.splitlines() if "error" in line.lower() or "failed" in line.lower()]
        visible = f"结果已压缩：原始长度 {len(text)} 字符，完整结果保存为 {storage_name}。" + (f"\n关键错误：{errors[0]}" if failed and errors else "")
    receipt = {"source": source, "content_id": digest, "original_chars": len(text), "compressed": compressed, "duplicate": duplicate, "failed": failed, "storage_name": storage_name, "recorded_at": datetime.now(timezone.utc).isoformat(), "session_id": os.environ.get("CODEX_SESSION_ID", "unknown")}
    receipt_path = data / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}.receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return {"content": [{"type": "text", "text": visible}], "isError": failed}


data = Path(os.environ.get("PLUGIN_DATA", Path.cwd() / ".codex-context-data"))
for line in sys.stdin:
    try:
        req = json.loads(line); method = req.get("method"); params = req.get("params", {}); result = None
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            client_version = params.get("protocolVersion") if isinstance(params, dict) else None
            protocol_version = client_version if isinstance(client_version, str) and client_version else "2025-03-26"
            result = {"protocolVersion": protocol_version, "capabilities": {"tools": {}}, "serverInfo": {"name": "codex-context-optimizer", "version": "0.1.0"}}
        elif method == "tools/list": result = {"tools": [{"name": "run_command", "description": "运行参数数组命令并压缩输出", "inputSchema": {"type": "object"}}, {"name": "read_file", "description": "读取工作区文件并压缩内容", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call" and params.get("name") == "run_command":
            command = params.get("arguments", {}).get("command", [])
            if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command): raise ValueError("命令必须是非空字符串数组")
            run = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
            result = output(run.stdout + run.stderr, data, "command", run.returncode != 0)
        elif method == "tools/call" and params.get("name") == "read_file":
            target = (Path.cwd() / params.get("arguments", {}).get("path", "")).resolve(); target.relative_to(Path.cwd().resolve())
            result = output(target.read_text(encoding="utf-8"), data, "file")
        else: raise ValueError("不支持的方法")
        response = {"jsonrpc": "2.0", "id": req.get("id"), "result": result}
    except Exception:
        response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "受控工具执行失败"}}
    print(json.dumps(response, ensure_ascii=False), flush=True)
