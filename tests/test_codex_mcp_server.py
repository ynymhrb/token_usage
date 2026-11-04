import sys
import tempfile
import unittest
from pathlib import Path

from codex_token.context_optimizer import ContextOptimizer
from codex_token.mcp_server import handle_request


class McpServerTests(unittest.TestCase):
    def test_initialize_negotiates_protocol(self):
        with tempfile.TemporaryDirectory() as temporary:
            response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, ContextOptimizer(Path(temporary)), Path(temporary))
        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(response["result"]["capabilities"], {"tools": {}})
    def test_tools_list_exposes_controlled_command_and_file_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ContextOptimizer(Path(temporary)), Path(temporary))
        self.assertEqual({tool["name"] for tool in response["result"]["tools"]}, {"run_command", "read_file"})

    def test_run_command_returns_compressed_tool_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_command", "arguments": {"command": [sys.executable, "-c", "print('x' * 1000)"]}}}
            response = handle_request(request, ContextOptimizer(Path(temporary), max_chars=100), Path(temporary))
        content = response["result"]["content"][0]["text"]
        self.assertIn("结果已压缩", content)
        self.assertLessEqual(len(content), 100)

    def test_read_file_rejects_path_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "../outside.txt"}}}
            response = handle_request(request, ContextOptimizer(root), root)
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn(str(root), response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
