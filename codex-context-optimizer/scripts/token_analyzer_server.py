"""聊天窗 Token 分析 MCP 服务，只调用本地分析器。"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# MCP stdio 是 UTF-8 JSON-RPC；Windows 控制台默认代码页不能替代协议编码。
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

from codex_token.plugin_service import PluginAnalysisError, run_plugin_analysis


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


def _paths() -> tuple[Path, Path]:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    sessions_root = Path(os.environ.get("CODEX_SESSIONS_ROOT", codex_home / "sessions"))
    output_dir = Path(os.environ.get("TOKEN_ANALYZER_OUTPUT_DIR", r"D:\ai_tmp\tokenUsage"))
    return sessions_root, output_dir


def _tool_list() -> dict[str, object]:
    return {
        "tools": [{
            "name": "analyze",
            "description": "本地分析 Codex Token；默认当前会话，返回紧凑摘要和脱敏报告路径。",
            "inputSchema": TOOL_SCHEMA,
        }],
    }


def _call(arguments: object) -> dict[str, object]:
    if not isinstance(arguments, Mapping):
        raise PluginAnalysisError("analyze 参数必须是对象。")
    scope = arguments.get("scope")
    focus = arguments.get("focus")
    if scope is not None and not isinstance(scope, str):
        raise PluginAnalysisError("scope 必须是字符串。")
    if focus is not None and not isinstance(focus, str):
        raise PluginAnalysisError("focus 必须是字符串。")
    sessions_root, output_dir = _paths()
    current_session_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    result = run_plugin_analysis(
        scope=scope,
        focus=focus,
        sessions_root=sessions_root,
        output_dir=output_dir,
        current_session_id=current_session_id,
        now=datetime.now(timezone.utc),
    )
    return {"content": [{"type": "text", "text": f"{result['summary']}\n完整报告：{result['report_path']}"}]}


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
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "token-analyzer", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_list()}
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, Mapping) or params.get("name") != "analyze":
            raise PluginAnalysisError("仅支持 analyze 工具。")
        return {"jsonrpc": "2.0", "id": request_id, "result": _call(params.get("arguments", {}))}
    raise PluginAnalysisError("不支持的方法。")


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise PluginAnalysisError("请求必须是对象。")
            response = _response(request)
        except PluginAnalysisError as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32602, "message": str(error)}}
        except (OSError, ValueError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "本地 Token 分析失败。"}}
        if response:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
