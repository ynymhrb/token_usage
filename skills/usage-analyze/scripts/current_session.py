"""从 Claude 本机历史中解析当前项目最新会话，不读取提示词内容。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path


def _same_project(left: str, right: Path) -> bool:
    try:
        return Path(left).resolve() == right.resolve()
    except OSError:
        return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(str(right)))


def resolve_current_session(history_path: Path, cwd: Path) -> str | None:
    """返回该工作目录最后一条历史记录中的有效 UUID；无匹配时返回空。"""
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        project = record.get("project")
        session_id = record.get("sessionId")
        if not isinstance(project, str) or not isinstance(session_id, str) or not _same_project(project, cwd):
            continue
        try:
            return str(uuid.UUID(session_id))
        except ValueError:
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="解析 Claude 当前项目会话标识")
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument("--claude-home", default=os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    args = parser.parse_args(argv)
    session_id = resolve_current_session(Path(args.claude_home) / "history.jsonl", Path(args.cwd))
    if not session_id:
        print("无法取得当前 Claude 会话标识；请提供会话 ID 或明确时间范围。", file=sys.stderr)
        return 2
    print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
