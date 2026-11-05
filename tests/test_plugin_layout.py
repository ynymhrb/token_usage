"""验证两端插件入口与自包含文件的结构。"""
import json
import tempfile
import unittest
from pathlib import Path
from shutil import copytree


ROOT = Path(__file__).resolve().parent.parent


class PluginLayoutTests(unittest.TestCase):
    def test_marketplaces_reference_existing_plugin_roots(self):
        codex = json.loads((ROOT / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))
        plugin = codex['plugins'][0]
        self.assertEqual(plugin['source']['path'], './plugins/codex-context-optimizer')
        self.assertTrue((ROOT / plugin['source']['path'] / '.codex-plugin/plugin.json').is_file())
        claude = json.loads((ROOT / '.claude-plugin/marketplace.json').read_text(encoding='utf-8'))
        self.assertEqual(claude['plugins'][0]['source'], './')
        manifest = json.loads((ROOT / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
        self.assertTrue((ROOT / manifest['skills'][0] / 'SKILL.md').is_file())

    def test_claude_skill_and_scripts_survive_plugin_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)
            copytree(ROOT / '.claude-plugin', copy / '.claude-plugin')
            copytree(ROOT / '.claude/skills/usage-analyze', copy / '.claude/skills/usage-analyze')
            manifest = json.loads((copy / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
            skill = copy / manifest['skills'][0]
            for name in ('SKILL.md', 'scripts/usage-analyze.ps1', 'scripts/claude_session_token_profiler.py', 'scripts/current_session.py'):
                self.assertTrue((skill / name).is_file(), name)


if __name__ == '__main__':
    unittest.main()
