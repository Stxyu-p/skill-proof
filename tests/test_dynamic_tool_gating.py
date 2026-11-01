import unittest
import pathlib
import tempfile
from skill_proof.core import SkillProofEngine, SkillRecord, SkillInvariants, RankedCandidate
from skill_proof._catalog import _parse_skill

class TestDynamicToolGating(unittest.TestCase):
    def test_parse_mode_readonly(self):
        raw = b"""---
name: test-readonly
description: Test read only skill
mode: readonly
---
Instructions for reading only.
"""
        record = _parse_skill(
            raw,
            root_id="test",
            relative_path="test/SKILL.md",
            root_path=pathlib.Path("."),
            source_path=pathlib.Path("test/SKILL.md"),
        )
        self.assertIn("write_file", record.invariants.forbidden_tools)
        self.assertIn("patch", record.invariants.forbidden_tools)
        self.assertIn("skill_manage", record.invariants.forbidden_tools)

    def test_guard_tool_blocks_forbidden(self):
        engine = SkillProofEngine(roots={})
        turn = engine.begin_turn(turn_id="turn-1", session_id="s1", task_id="t1", query="read something")
        
        # Inject active skill with forbidden tools
        skill = SkillRecord(
            skill_id="skl-1",
            name="readonly-audit",
            normalized_name="readonly-audit",
            description="Audit only",
            tags=(),
            root_id="test",
            relative_path="SKILL.md",
            source_sha256="abc",
            source_bytes=100,
            root_path=pathlib.Path("."),
            source_path=pathlib.Path("SKILL.md"),
            invariants=SkillInvariants(forbidden_tools=("write_file", "patch")),
            aliases=(),
            related_skills=(),
        )
        from dataclasses import replace
        cand = RankedCandidate(skill=skill, score=1.0, reasons=("exact",), companions=())
        with engine._lock:
            state = engine._state(turn_id="turn-1", session_id="s1", task_id="t1")
            state.selection = replace(state.selection, selected=cand)
            state.host_loaded_event = True

        res_read = engine.guard_tool(turn_id="turn-1", session_id="s1", task_id="t1", tool_name="read_file")
        self.assertTrue(res_read.allowed, "read_file should be allowed")

        res_write = engine.guard_tool(turn_id="turn-1", session_id="s1", task_id="t1", tool_name="write_file")
        self.assertFalse(res_write.allowed, "write_file should be strictly blocked")
        self.assertEqual(res_write.reason, "forbidden_tool")

if __name__ == "__main__":
    unittest.main()
