"""Claude 当前会话解析器的边界测试。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).parent.parent / ".claude" / "skills" / "usage-analyze" / "scripts" / "current_session.py"


def load_module():
    spec = importlib.util.spec_from_file_location("claude_current_session", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ClaudeCurrentSessionTests(unittest.TestCase):
    def test_uses_latest_matching_project_history_record(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "history.jsonl"
            history.write_text(
                "\n".join([
                    json.dumps({"project": r"D:\\other", "sessionId": "11111111-1111-1111-1111-111111111111"}),
                    json.dumps({"project": r"D:\\repo", "sessionId": "22222222-2222-2222-2222-222222222222"}),
                    json.dumps({"project": r"D:\\repo", "sessionId": "33333333-3333-3333-3333-333333333333"}),
                ]),
                encoding="utf-8",
            )
            actual = module.resolve_current_session(history, Path(r"D:\\repo"))
        self.assertEqual(actual, "33333333-3333-3333-3333-333333333333")

    def test_refuses_missing_or_invalid_matching_history(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "history.jsonl"
            history.write_text(json.dumps({"project": r"D:\\repo", "sessionId": "not-a-session"}), encoding="utf-8")
            actual = module.resolve_current_session(history, Path(r"D:\\repo"))
        self.assertIsNone(actual)


if __name__ == "__main__":
    unittest.main()
