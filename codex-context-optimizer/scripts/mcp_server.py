"""本地开发插件启动器；发布时随插件一同保留。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from codex_token.mcp_server import main


raise SystemExit(main())
