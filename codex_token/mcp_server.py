"""最小 stdio MCP 服务：受控执行和读取在进入模型前压缩。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .context_optimizer import ContextOptimizer


TOOLS = (
    {"name": "run_command", "description": "在工作区运行参数数组命令，并压缩大输出。", "inputSchema": {"type": "object", "required": ["command"], "properties": {"command": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "read_file", "description": "读取工作区内文件，并压缩大内容。", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}},
)


def _content(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _call(name: str, arguments: dict[str, Any], optimizer: ContextOptimizer, cwd: Path) -> dict[str, Any]:
    if name == "run_command":
        command = arguments.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
            return _content("命令必须是非空字符串数组。", error=True)
        try:
            completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return _content("命令无法启动或超时。", error=True)
        text = completed.stdout + completed.stderr
        result = optimizer.optimize(text, source="command", failed=completed.returncode != 0)
        return _content(result.visible_text, error=completed.returncode != 0)
    if name == "read_file":
        relative = arguments.get("path")
        if not isinstance(relative, str) or not relative:
            return _content("路径必须是非空字符串。", error=True)
        target = (cwd / relative).resolve()
        try:
            target.relative_to(cwd.resolve())
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            return _content("文件不可读或不在工作区内。", error=True)
        result = optimizer.optimize(text, source="file")
        return _content(result.visible_text)
    return _content("未知受控工具。", error=True)


def handle_request(request: dict[str, Any], optimizer: ContextOptimizer, cwd: Path) -> dict[str, Any]:
    request_id = request.get("id")
    if request.get("method") == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "codex-context-optimizer", "version": "0.1.0"},
            },
        }
    if request.get("method") == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(TOOLS)}}
    if request.get("method") == "tools/call" and isinstance(request.get("params"), dict):
        params = request["params"]
        if isinstance(params.get("name"), str) and isinstance(params.get("arguments", {}), dict):
            return {"jsonrpc": "2.0", "id": request_id, "result": _call(params["name"], params.get("arguments", {}), optimizer, cwd)}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "不支持的方法"}}


def main() -> int:
    data_dir = Path(__import__("os").environ.get("PLUGIN_DATA", Path.cwd() / ".codex-context-data"))
    optimizer = ContextOptimizer(data_dir)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle_request(request, optimizer, Path.cwd())
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "无效 JSON"}}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
