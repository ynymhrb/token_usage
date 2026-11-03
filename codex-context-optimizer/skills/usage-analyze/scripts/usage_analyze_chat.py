"""聊天窗 Token 分析兜底入口：只读本地会话，直接输出 Markdown。"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from token_analyzer_runtime import AnalysisError, analyze


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default=None)
    arguments = parser.parse_args()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    session_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    try:
        result = analyze(arguments.scope, None, codex_home / "sessions", session_id, datetime.now(timezone.utc))
    except AnalysisError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
