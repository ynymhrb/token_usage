import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import copytree


class PluginPackageTests(unittest.TestCase):
    def test_plugin_ships_codex_usage_analyze_skill(self):
        skill = Path("codex-context-optimizer/skills/usage-analyze/SKILL.md")

        text = skill.read_text(encoding="utf-8")

        self.assertIn("name: usage-analyze", text)
        self.assertIn("token_analyzer", text)
        self.assertIn("analyze", text)
        self.assertIn("默认分析当前会话", text)
        self.assertIn("scope", text)
        self.assertIn("今天", text)
        self.assertIn("可调用工具集合", text)
        self.assertIn("不要调用 exec", text)
        self.assertIn("不得继续其他任务", text)
        self.assertNotIn("claude_session_token_profiler", text)
        self.assertNotIn("statusLine", text)

    def test_plugin_registers_local_mcp_and_compaction_hook(self):
        root = Path("codex-context-optimizer")
        manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "codex-context-optimizer")
        self.assertIn("context_optimizer", mcp["mcpServers"])
        self.assertFalse(mcp["mcpServers"]["context_optimizer"]["enabled"])
        self.assertTrue(mcp["mcpServers"]["token_analyzer"]["enabled"])
        self.assertTrue(mcp["mcpServers"]["context_optimizer"]["command"].endswith("python.exe"))
        self.assertTrue(mcp["mcpServers"]["token_analyzer"]["command"].endswith("python.exe"))
        self.assertEqual(mcp["mcpServers"]["context_optimizer"]["cwd"], ".")
        self.assertEqual(mcp["mcpServers"]["token_analyzer"]["cwd"], ".")
        self.assertEqual(mcp["mcpServers"]["token_analyzer"]["default_tools_approval_mode"], "approve")
        self.assertEqual(mcp["mcpServers"]["context_optimizer"]["env_vars"], ["CODEX_HOME", "PATH"])
        self.assertEqual(
            mcp["mcpServers"]["token_analyzer"]["env_vars"],
            ["CODEX_HOME", "PATH", "SYSTEMROOT", "WINDIR", "USERPROFILE", "LOCALAPPDATA"],
        )
        self.assertIn("PreCompact", hooks["hooks"])
        self.assertIn("${PLUGIN_ROOT}", hooks["hooks"]["PreCompact"][0]["hooks"][0]["command"])

    def test_mcp_servers_start_from_a_copied_plugin_cache(self):
        root = Path("codex-context-optimizer")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary, ".codex")
            cache_root = codex_home / "plugins" / "cache" / "token-usage-local" / "codex-context-optimizer" / "0.1.9"
            copytree(root, cache_root)
            mcp = json.loads((cache_root / ".mcp.json").read_text(encoding="utf-8"))
            for name, expected_server_name in (("context_optimizer", "codex-context-optimizer"), ("token_analyzer", "token-analyzer")):
                server = mcp["mcpServers"][name]
                completed = subprocess.run(
                    [server["command"], *server["args"]], input=request, text=True, capture_output=True,
                    cwd=cache_root / server["cwd"], env=(os.environ | {"CODEX_HOME": str(codex_home)}), check=True,
                )
                response = json.loads(completed.stdout)
                self.assertEqual(response["result"]["serverInfo"]["name"], expected_server_name)
                self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")

    def test_repo_marketplace_exposes_the_plugin(self):
        marketplace = json.loads(Path(".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        plugin = marketplace["plugins"][0]
        self.assertEqual(marketplace["name"], "token-usage-local")
        self.assertEqual(plugin["name"], "codex-context-optimizer")
        self.assertEqual(plugin["source"]["path"], "./codex-context-optimizer")

    def test_controlled_mcp_tools_require_approval(self):
        config = Path(".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn('default_tools_approval_mode = "prompt"', config)

    def test_self_contained_runtime_suppresses_duplicate_after_restart(self):
        root = Path("codex-context-optimizer")
        script = root / "scripts" / "mcp_server.py"
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "sample.txt"}}}) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "sample.txt").write_text("same output", encoding="utf-8")
            environment = os.environ | {"PLUGIN_DATA": str(Path(temporary, "data"))}
            first = subprocess.run([sys.executable, str(script.resolve())], input=request, text=True, capture_output=True, cwd=temporary, env=environment, check=True)
            second = subprocess.run([sys.executable, str(script.resolve())], input=request, text=True, capture_output=True, cwd=temporary, env=environment, check=True)
        self.assertIn("same output", json.loads(first.stdout)["result"]["content"][0]["text"])
        self.assertIn("重复结果已省略", json.loads(second.stdout)["result"]["content"][0]["text"])

    def test_self_contained_runtime_redacts_common_credentials(self):
        script = Path("codex-context-optimizer/scripts/mcp_server.py").resolve()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "credentials.txt"}}}) + "\n"
        secrets = "AWS_SECRET_ACCESS_KEY=leaked-value\nAWS_SESSION_TOKEN=session-secret\nAuthorization: Bearer bearer-secret\nAuthorization: Token token-secret\nghp_abcdefghijklmnopqrstuvwxyz1234567890\ngithub_pat_abcdefghijklmnopqrstuvwxyz0123456789\npostgresql://admin:db-secret@example.test/app\nhttps://admin:https-secret@example.test/path\n"
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "credentials.txt").write_text(secrets, encoding="utf-8")
            completed = subprocess.run([sys.executable, str(script)], input=request, text=True, capture_output=True, cwd=temporary, env=(os.environ | {"PLUGIN_DATA": str(Path(temporary, "data"))}), check=True)
        visible = json.loads(completed.stdout)["result"]["content"][0]["text"]
        for secret in ("leaked-value", "session-secret", "bearer-secret", "token-secret", "ghp_abcdefghijklmnopqrstuvwxyz1234567890", "github_pat_abcdefghijklmnopqrstuvwxyz0123456789", "db-secret", "https-secret"):
            self.assertNotIn(secret, visible)

    def test_self_contained_runtime_writes_a_receipt_for_every_call(self):
        script = Path("codex-context-optimizer/scripts/mcp_server.py").resolve()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "sample.txt"}}}) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary, "data")
            Path(temporary, "sample.txt").write_text("same output", encoding="utf-8")
            environment = os.environ | {"PLUGIN_DATA": str(data)}
            for _ in range(2):
                subprocess.run([sys.executable, str(script)], input=request, text=True, capture_output=True, cwd=temporary, env=environment, check=True)
            receipts = list(data.glob("*.receipt.json"))
            duplicate_count = sum(json.loads(path.read_text(encoding="utf-8"))["duplicate"] for path in receipts)
            self.assertEqual(len(receipts), 2)
            self.assertEqual(duplicate_count, 1)

    def test_self_contained_runtime_silently_accepts_initialized_notification(self):
        script = Path("codex-context-optimizer/scripts/mcp_server.py").resolve()
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run([sys.executable, str(script)], input=notification, text=True, capture_output=True, cwd=temporary, check=True)
        self.assertEqual(completed.stdout, "")

    def test_self_contained_runtime_negotiates_the_client_protocol_version(self):
        script = Path("codex-context-optimizer/scripts/mcp_server.py").resolve()
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        }) + "\n"

        completed = subprocess.run([sys.executable, str(script)], input=request, text=True, capture_output=True, check=True)

        self.assertEqual(json.loads(completed.stdout)["result"]["protocolVersion"], "2025-11-25")

    def test_pre_compact_hook_reports_receipt_counts_without_raw_output(self):
        script = Path("codex-context-optimizer/hooks/pre_compact.py").resolve()
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary, "data")
            data.mkdir()
            Path(data, "abc.receipt.json").write_text(json.dumps({"original_chars": 5000, "compressed": True, "duplicate": False}), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(script)], input=json.dumps({"session_id": "test-session"}), text=True, capture_output=True, cwd=temporary, env=(os.environ | {"PLUGIN_DATA": str(data)}), check=True)
        additional_context = json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("插件数据目录收据：1 条，压缩 1 次，重复省略 0 次，原始输出合计 5000 字符", additional_context)
        self.assertNotIn("API_KEY", additional_context)


if __name__ == "__main__":
    unittest.main()
