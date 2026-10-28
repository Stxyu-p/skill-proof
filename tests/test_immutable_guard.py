"""Tests for immutable skill write-protection in SkillProofPlugin.on_pre_tool_call."""
import pathlib
import tempfile
import unittest
from unittest import mock

import importlib.util
import sys

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package_guard", _package_path / "__init__.py",
    submodule_search_locations=[str(_package_path)],
)
plugin_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plugin_module
_spec.loader.exec_module(plugin_module)


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeContext:
    def __init__(self):
        self.settings = {"audit_log": False, "mode": "nudge"}
        self.hooks = {}
        self.middleware = {}
        self.commands = {}
        self.state = FakeState()

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def dispatch_tool(self, name, args):
        return '{"success": true, "skills": []}'

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_middleware(self, name, callback):
        self.middleware[name] = callback

    def register_command(self, name, handler, **metadata):
        self.commands[name] = (handler, metadata)


class TestImmutableGuard(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeContext()
        self.instance = plugin_module.register(self.ctx)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_blocks_skill_manage_on_immutable_skill(self):
        skill_dir = self.root / "critical-sop"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: critical-sop\nimmutable: true\n---\n# Critical SOP",
            encoding="utf-8",
        )

        with mock.patch.dict("os.environ", {"SKILL_PROOF_VAULT": str(self.root)}):
            # 1. Block via skill_manage operations
            res = self.instance.on_pre_tool_call(
                tool_name="skill_manage",
                args={"operations": [{"action": "patch", "name": "critical-sop", "content": "bad"}]},
            )
            self.assertIsNotNone(res)
            self.assertEqual(res.get("action"), "block")
            self.assertIn("immutable", res.get("message", "").lower())

            # 2. Block via single name argument
            res2 = self.instance.on_pre_tool_call(
                tool_name="skill_manage",
                args={"action": "patch", "name": "critical-sop", "content": "bad"},
            )
            self.assertIsNotNone(res2)
            self.assertEqual(res2.get("action"), "block")

    def test_blocks_write_file_on_immutable_skill(self):
        skill_dir = self.root / "locked-sop"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\nname: locked-sop\ngovernance: locked\n---\n# Rule",
            encoding="utf-8",
        )

        res = self.instance.on_pre_tool_call(
            tool_name="write_file",
            args={"path": str(skill_file), "content": "hacked"},
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.get("action"), "block")
        self.assertIn("immutable", res.get("message", "").lower())

    def test_blocks_patch_on_immutable_skill(self):
        skill_dir = self.root / "protected-sop"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\nname: protected-sop\nprotected: true\n---\n# Rule",
            encoding="utf-8",
        )

        res = self.instance.on_pre_tool_call(
            tool_name="patch",
            args={"path": str(skill_file), "old_string": "Rule", "new_string": "Bypass"},
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.get("action"), "block")

    def test_allows_normal_skill_write_and_patch(self):
        skill_dir = self.root / "editable-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\nname: editable-skill\ndescription: normal\n---\n# Editable",
            encoding="utf-8",
        )

        with mock.patch.dict("os.environ", {"SKILL_PROOF_VAULT": str(self.root)}):
            res_manage = self.instance.on_pre_tool_call(
                tool_name="skill_manage",
                args={"operations": [{"action": "patch", "name": "editable-skill", "content": "ok"}]},
            )
            self.assertIsNone(res_manage)

            res_write = self.instance.on_pre_tool_call(
                tool_name="write_file",
                args={"path": str(skill_file), "content": "updated"},
            )
            self.assertIsNone(res_write)


if __name__ == "__main__":
    unittest.main()
