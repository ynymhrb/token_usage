import json
import tempfile
import unittest
from pathlib import Path

from codex_token.context_attribution import analyze_context_exports


class ContextAttributionTests(unittest.TestCase):
    def test_classifies_request_structure_without_retaining_request_text(self):
        record = {
            "request": {
                "instructions": {"text": "静态规则"},
                "persisted_dynamic_tools": [{"name": "shell", "description": "运行命令"}],
                "input": [
                    {"type": "message", "role": "developer", "content": "开发者规则"},
                    {"type": "message", "role": "user", "content": "用户问题"},
                    {"type": "message", "role": "assistant", "content": "历史回答"},
                    {"type": "function_call", "name": "exec", "arguments": "秘密参数"},
                    {"type": "function_call_output", "output": "私密工具输出"},
                    {"type": "reasoning", "encrypted_content": "不可见推理"},
                ],
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.llm_calls.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            result = analyze_context_exports(root)

        self.assertEqual(result.call_count, 1)
        self.assertGreater(result.category_chars["static_instructions"], 0)
        self.assertGreater(result.category_chars["tool_definitions"], 0)
        self.assertGreater(result.category_chars["developer_messages"], 0)
        self.assertGreater(result.category_chars["user_messages"], 0)
        self.assertGreater(result.category_chars["assistant_history"], 0)
        self.assertGreater(result.category_chars["tool_arguments"], 0)
        self.assertGreater(result.category_chars["tool_results"], 0)
        self.assertGreater(result.category_chars["unattributable"], 0)
        self.assertNotIn("秘密参数", repr(result))
        self.assertNotIn("私密工具输出", repr(result))

    def test_missing_exports_are_a_coverage_gap_not_zero_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = analyze_context_exports(Path(temporary))

        self.assertEqual(result.call_count, 0)
        self.assertIn("missing_request_structure", {gap.code for gap in result.data_gaps})

    def test_malformed_export_line_becomes_gap_while_good_lines_continue(self):
        good = {"request": {"input": [{"type": "message", "role": "user", "content": "x"}]}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.llm_calls.jsonl").write_text("not json\n" + json.dumps(good) + "\n", encoding="utf-8")
            result = analyze_context_exports(root)

        self.assertEqual(result.call_count, 1)
        self.assertIn("invalid_request_export", {gap.code for gap in result.data_gaps})


if __name__ == "__main__":
    unittest.main()
