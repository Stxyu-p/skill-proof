import json
import pathlib
import tempfile
import unittest

from core import SkillProofEngine, SkillInvariants
from tests.test_plugin import FakeContext, plugin_module


class ComplianceCoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="skill_proof_compliance_")
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "tdd").mkdir()
        (self.root / "tdd" / "SKILL.md").write_text(
            """---
name: tdd
description: Test driven development workflow
invariants:
  required_tools: [terminal]
  ordered_tools: [terminal, write_file]
---
Always test before writing code.
""",
            encoding="utf-8",
        )
        (self.root / "audit").mkdir()
        (self.root / "audit" / "SKILL.md").write_text(
            """---
name: audit
description: Read-only security audit workflow
invariants:
  forbidden_tools: [write_file, patch]
---
Only inspect, never modify.
""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def engine(self, mode="nudge", invariants=None):
        return SkillProofEngine({"root": self.root}, mode=mode, invariants=invariants)

    def test_frontmatter_invariants_parsed(self):
        eng = self.engine()
        eng.begin_turn(
            turn_id="t1", session_id="s1", task_id="k1",
            query="Use $tdd", available_names={"tdd", "audit"},
        )
        sel = eng.receipt(turn_id="t1")["selected"]
        self.assertEqual(sel["name"], "tdd")
        skill = eng._turns["t1"].selection.selected.skill
        self.assertEqual(skill.invariants.required_tools, ("terminal",))
        self.assertEqual(skill.invariants.ordered_tools, ("terminal", "write_file"))

    def test_required_tools_satisfied_yields_verified(self):
        eng = self.engine()
        eng.begin_turn(
            turn_id="t1", session_id="s1", task_id="k1",
            query="Use $tdd", available_names={"tdd"},
        )
        eng.observe_skill_loaded(session_id="s1", task_id="k1", skill_name="tdd")
        eng.guard_tool(turn_id="t1", session_id="s1", task_id="k1", tool_name="terminal")
        eng.guard_tool(turn_id="t1", session_id="s1", task_id="k1", tool_name="write_file")
        receipt = eng.finish_turn(turn_id="t1")
        self.assertEqual(receipt["compliance"], "verified")
        self.assertEqual(receipt["compliance_reasons"], [])
        self.assertIn("compliance=verified", eng.summary(turn_id="t1"))

    def test_required_tools_missing_yields_failed(self):
        eng = self.engine()
        eng.begin_turn(
            turn_id="t1", session_id="s1", task_id="k1",
            query="Use $tdd", available_names={"tdd"},
        )
        eng.observe_skill_loaded(session_id="s1", task_id="k1", skill_name="tdd")
        # terminal was never called
        receipt = eng.finish_turn(turn_id="t1")
        self.assertEqual(receipt["compliance"], "failed")
        self.assertIn("missing_required_tool:terminal", receipt["compliance_reasons"])
        self.assertIn("compliance=failed", eng.summary(turn_id="t1"))

    def test_forbidden_tool_fails_and_blocks_in_enforce_mode(self):
        eng = self.engine(mode="enforce-tools")
        eng.begin_turn(
            turn_id="t1", session_id="s1", task_id="k1",
            query="Use $audit", available_names={"audit"},
        )
        eng.observe_skill_loaded(session_id="s1", task_id="k1", skill_name="audit")
        # read_file should be allowed
        read_res = eng.guard_tool(turn_id="t1", session_id="s1", task_id="k1", tool_name="read_file")
        self.assertTrue(read_res.allowed)

        # write_file is forbidden -> must be blocked
        write_res = eng.guard_tool(turn_id="t1", session_id="s1", task_id="k1", tool_name="write_file")
        self.assertFalse(write_res.allowed)
        self.assertEqual(write_res.reason, "forbidden_tool")

        receipt = eng.finish_turn(turn_id="t1")
        self.assertEqual(receipt["compliance"], "failed")
        self.assertIn("forbidden_tool:write_file", receipt["compliance_reasons"])

    def test_order_violation_fails_and_blocks_in_enforce_mode(self):
        eng = self.engine(mode="enforce-tools")
        eng.begin_turn(
            turn_id="t1", session_id="s1", task_id="k1",
            query="Use $tdd", available_names={"tdd"},
        )
        eng.observe_skill_loaded(session_id="s1", task_id="k1", skill_name="tdd")

        # write_file before terminal violates order (requires terminal first)
        res = eng.guard_tool(turn_id="t1", session_id="s1", task_id="k1", tool_name="write_file")
        self.assertFalse(res.allowed)
        self.assertEqual(res.reason, "tool_order_violated")

        receipt = eng.finish_turn(turn_id="t1")
        self.assertEqual(receipt["compliance"], "failed")
        self.assertIn("order_violation:write_file_before_terminal", receipt["compliance_reasons"])

    def test_configured_invariants_override_or_extend(self):
        config_invariants = {
            "audit": {"required_tools": ["search_files"]},
        }
        eng = self.engine(invariants=config_invariants)
        eng.begin_turn(
            turn_id="t1", session_id="s1", task_id="k1",
            query="Use $audit", available_names={"audit"},
        )
        eng.observe_skill_loaded(session_id="s1", task_id="k1", skill_name="audit")
        eng.guard_tool(turn_id="t1", session_id="s1", task_id="k1", tool_name="search_files")
        receipt = eng.finish_turn(turn_id="t1")
        self.assertEqual(receipt["compliance"], "verified")


class ComplianceFakeContext(FakeContext):
    def __init__(self, settings=None, skills=None):
        isolated = dict(settings or {})
        isolated.setdefault("audit_log", False)
        super().__init__(isolated)
        self._skills = skills or [{'name': 'python-tdd'}]

    def dispatch_tool(self, name, args):
        assert name == 'skills_list'
        return json.dumps({'success': True, 'skills': self._skills})


class CompliancePluginAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="skill_proof_plugin_comp_")
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "tdd").mkdir()
        (self.root / "tdd" / "SKILL.md").write_text(
            """---
name: tdd
description: Test driven development
invariants:
  required_tools: [terminal]
---
Test instructions.
""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_compact_footer_shows_verified_and_failed(self):
        ctx = ComplianceFakeContext({
            "skill_roots": [str(self.root)],
            "receipt_style": "compact",
        }, skills=[{'name': 'tdd'}])
        plugin = plugin_module.register(ctx)

        # Turn 1: missing required tool -> failed
        plugin.on_pre_llm_call(session_id="s1", task_id="t1", turn_id="turn1", user_message="Use $tdd")
        plugin.on_skill_lifecycle(action="loaded", session_id="s1", task_id="t1", skill_name="tdd")
        footer_fail = plugin.on_transform_llm_output(session_id="s1", response_text="Done.")
        self.assertIn("compliance failed", footer_fail)
        self.assertIn("missing_required_tool:terminal", footer_fail)

        # Turn 2: called terminal -> verified
        plugin.on_pre_llm_call(session_id="s2", task_id="t2", turn_id="turn2", user_message="Use $tdd")
        plugin.on_skill_lifecycle(action="loaded", session_id="s2", task_id="t2", skill_name="tdd")
        plugin.on_pre_tool_call(session_id="s2", task_id="t2", turn_id="turn2", tool_name="terminal")
        footer_pass = plugin.on_transform_llm_output(session_id="s2", response_text="Done.")
        self.assertIn("compliance verified", footer_pass)
        self.assertNotIn("missing_required_tool", footer_pass)


if __name__ == "__main__":
    unittest.main()
