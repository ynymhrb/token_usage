import json
import unittest
from pathlib import Path


class PluginPackageTests(unittest.TestCase):
    def test_plugin_registers_local_mcp_and_compaction_hook(self):
        root = Path("codex-context-optimizer")
        manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "codex-context-optimizer")
        self.assertIn("context_optimizer", mcp["mcpServers"])
        self.assertIn("PreCompact", hooks["hooks"])
        self.assertIn("${PLUGIN_ROOT}", hooks["hooks"]["PreCompact"][0]["hooks"][0]["command"])

    def test_repo_marketplace_exposes_the_plugin(self):
        marketplace = json.loads(Path(".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        plugin = marketplace["plugins"][0]
        self.assertEqual(marketplace["name"], "token-usage-local")
        self.assertEqual(plugin["name"], "codex-context-optimizer")
        self.assertEqual(plugin["source"]["path"], "./codex-context-optimizer")


if __name__ == "__main__":
    unittest.main()
