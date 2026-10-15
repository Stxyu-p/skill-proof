"""Tests for optional, removable host bridges (bridge.py)."""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import bridge
import core


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_bridge_")
        self.target = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_empty_target(self):
        st = bridge.status(self.target)
        self.assertEqual(st["version"], core.__version__)
        self.assertEqual(st["target"], str(self.target))
        self.assertFalse(st["policy"]["present"])
        self.assertFalse(st["hosts"]["claude-code"]["present"])
        self.assertEqual(st["hosts"]["codex"]["section"], "absent")
        self.assertFalse(st["hosts"]["cursor"]["present"])

    def test_init_and_remove_codex(self):
        agents_md = self.target / "AGENTS.md"
        agents_md.write_text("# My Project\n\nSome instructions.\n", encoding="utf-8")

        actions = bridge.init_host("codex", self.target)
        self.assertTrue(any("added" in a for a in actions))
        content = agents_md.read_text(encoding="utf-8")
        self.assertIn(bridge.MARKER_START, content)
        self.assertIn("## Skill Proof", content)
        self.assertIn(bridge.MARKER_END, content)

        st = bridge.status(self.target)
        self.assertEqual(st["hosts"]["codex"]["section"], "present")

        # Idempotent init
        actions_repeat = bridge.init_host("codex", self.target)
        self.assertTrue(any("already up to date" in a for a in actions_repeat))

        # Remove
        remove_actions = bridge.remove_host("codex", self.target)
        self.assertTrue(any("removed" in a for a in remove_actions))
        cleaned = agents_md.read_text(encoding="utf-8")
        self.assertNotIn(bridge.MARKER_START, cleaned)
        self.assertIn("# My Project", cleaned)

    def test_init_and_remove_cursor(self):
        cursor_rule = self.target / ".cursor" / "rules" / "skill-proof.mdc"
        actions = bridge.init_host("cursor", self.target)
        self.assertTrue(any("written" in a for a in actions))
        self.assertTrue(cursor_rule.is_file())

        st = bridge.status(self.target)
        self.assertTrue(st["hosts"]["cursor"]["present"])

        remove_actions = bridge.remove_host("cursor", self.target)
        self.assertTrue(any("deleted" in a for a in remove_actions))
        self.assertFalse(cursor_rule.exists())

    def test_init_and_remove_claude_code(self):
        hook_path = self.target / "skill-proof-hook.py"
        actions = bridge.init_host("claude-code", self.target)
        self.assertTrue(any("wrote" in a for a in actions))
        self.assertTrue(hook_path.is_file())

        st = bridge.status(self.target)
        self.assertTrue(st["hosts"]["claude-code"]["present"])

        remove_actions = bridge.remove_host("claude-code", self.target)
        self.assertTrue(any("deleted" in a for a in remove_actions))
        self.assertFalse(hook_path.exists())

    def test_dry_run_writes_nothing(self):
        actions = bridge.init_host("codex", self.target, dry_run=True)
        self.assertTrue(any("dry run" in a for a in actions))
        self.assertFalse((self.target / "AGENTS.md").exists())
        self.assertFalse((self.target / "skill-proof-bridge.json").exists())

    def test_policy_load_and_write(self):
        pol = bridge.load_policy(self.target)
        self.assertEqual(pol["forbidden_tools"], [])

        path = bridge.write_policy(self.target)
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("forbidden_tools", data)

    def test_run_hook_allows_unlisted_tools(self):
        res = bridge.run_hook({"tool_name": "read_file"})
        self.assertEqual(res, {})

    def test_run_hook_reads_workspace_cwd_policy_and_remove_auto_cleans_detected_hosts(self):
        policy_path = self.target / "skill-proof-bridge.json"
        policy_path.write_text(json.dumps({"forbidden_tools": ["write_file"]}), encoding="utf-8")

        denied = bridge.run_hook({"tool_name": "write_file", "cwd": str(self.target)})
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        allowed = bridge.run_hook({"tool_name": "read_file", "cwd": str(self.target)})
        self.assertEqual(allowed, {})

        bridge.init_host("codex", self.target)
        bridge.init_host("cursor", self.target)
        actions = bridge.remove_host("auto", self.target)
        self.assertTrue(actions)
        self.assertNotIn(bridge.MARKER_START, (self.target / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertFalse((self.target / ".cursor" / "rules" / "skill-proof.mdc").exists())


if __name__ == "__main__":
    unittest.main()
