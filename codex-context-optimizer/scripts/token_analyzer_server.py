"""聊天窗 Token 分析 MCP 服务，只调用本地分析器。"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# MCP stdio 是 UTF-8 JSON-RPC；Windows 控制台默认代码页不能替代协议编码。
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

from token_analyzer_runtime import AnalysisError, analyze


TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "description": "可选：当前会话、今天、昨天、最近 N 小时、明确时间范围或全部历史；默认当前会话。",
        },
        "focus": {
            "type": "string",
            "enum": ["full", "context", "tools"],
            "description": "可选：完整 Token 概览、上下文构成或工具相关概览；默认 full。",
        },
    },
    "additionalProperties": False,
}


def _sessions_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return Path(os.environ.get("CODEX_SESSIONS_ROOT", codex_home / "sessions"))


def _tool_list() -> dict[str, object]:
    return {
        "tools": [{
            "name": "analyze",
            "description": "本地分析 Codex Token；默认当前会话，直接返回完整诊断，不写入报告文件。",
            "inputSchema": TOOL_SCHEMA,
        }],
    }


def _protocol_version(request: Mapping[str, Any]) -> str:
    params = request.get("params")
    if isinstance(params, Mapping):
        client_version = params.get("protocolVersion")
        if isinstance(client_version, str) and client_version:
            return client_version
    return "2025-03-26"


def _call(arguments: object) -> dict[str, object]:
    if not isinstance(arguments, Mapping):
        raise AnalysisError("analyze 参数必须是对象。")
    scope = arguments.get("scope")
    focus = arguments.get("focus")
    if scope is not None and not isinstance(scope, str):
        raise AnalysisError("scope 必须是字符串。")
    if focus is not None and not isinstance(focus, str):
        raise AnalysisError("focus 必须是字符串。")
    sessions_root = _sessions_root()
    current_session_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    result = analyze(scope, focus, sessions_root, current_session_id, datetime.now(timezone.utc))
    return {"content": [{"type": "text", "text": result["summary"]}]}


def _response(request: Mapping[str, Any]) -> dict[str, object]:
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized":
        return {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": _protocol_version(request),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "token-analyzer", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_list()}
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, Mapping) or params.get("name") != "analyze":
            raise AnalysisError("仅支持 analyze 工具。")
        return {"jsonrpc": "2.0", "id": request_id, "result": _call(params.get("arguments", {}))}
    raise AnalysisError("不支持的方法。")


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise AnalysisError("请求必须是对象。")
            response = _response(request)
        except AnalysisError as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32602, "message": str(error)}}
        except (OSError, ValueError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "本地 Token 分析失败。"}}
        if response:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
