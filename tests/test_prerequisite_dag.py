"""Unit tests for Prerequisite DAG Preflight, Provenance Badges, and Telemetry in Skill Proof."""

import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from skill_proof._catalog import (
    SkillRecord,
    SkillInvariants,
    check_prerequisites,
)
from skill_proof.crystallize import crystallize_skill
from skill_proof.core import log_skill_telemetry


class TestPrerequisiteDAG(unittest.TestCase):
    def test_check_prerequisites_success(self):
        # A skill requiring python and git which exist on this system
        inv = SkillInvariants(
            required_binaries=("python",),
            required_tools=("terminal",),
        )
        skill = SkillRecord(
            skill_id="skl-test-1",
            name="test-skill",
            normalized_name="test-skill",
            description="Test skill description.",
            tags=("test",),
            root_id="root-1",
            relative_path="SKILL.md",
            source_sha256="abc",
            source_bytes=100,
            root_path=pathlib.Path("."),
            source_path=pathlib.Path("SKILL.md"),
            invariants=inv,
        )

        ok, missing = check_prerequisites(skill, available_tools=["terminal", "read_file"])
        self.assertTrue(ok)
        self.assertEqual(missing, ())

    def test_check_prerequisites_missing_binary_and_tool(self):
        inv = SkillInvariants(
            required_binaries=("nonexistent_cmd_xyz_404",),
            required_tools=("special_custom_tool",),
        )
        skill = SkillRecord(
            skill_id="skl-test-2",
            name="test-skill-missing",
            normalized_name="test-skill-missing",
            description="Test skill description.",
            tags=("test",),
            root_id="root-1",
            relative_path="SKILL.md",
            source_sha256="abc",
            source_bytes=100,
            root_path=pathlib.Path("."),
            source_path=pathlib.Path("SKILL.md"),
            invariants=inv,
        )

        ok, missing = check_prerequisites(skill, available_tools=["terminal"])
        self.assertFalse(ok)
        self.assertIn("binary:nonexistent_cmd_xyz_404", missing)
        self.assertIn("tool:special_custom_tool", missing)

    def test_provenance_badges_in_crystallize(self):
        skill_md, proof = crystallize_skill(
            name="docker-build-push",
            description="Build and push container image.",
            steps=["docker build -t app:v1 .", "docker push app:v1"],
            category="devops",
            evidence=["Session evidence #100"],
        )

        self.assertIn("## Procedure [EXTRACTED]", skill_md)
        self.assertIn("## When to Use [INFERRED]", skill_md)
        self.assertIn("## Pitfalls [INFERRED]", skill_md)
        self.assertIn("## Evidence [EXTRACTED]", skill_md)

    def test_skill_telemetry_logging(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.environ.get("SKILL_PROOF_DATA_DIR")
            try:
                os.environ["SKILL_PROOF_DATA_DIR"] = tmpdir
                log_skill_telemetry("docker-build-push", action="dispatch", duration_ms=45.67)

                log_path = os.path.join(tmpdir, "usage.jsonl")
                self.assertTrue(os.path.exists(log_path))
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                self.assertEqual(len(lines), 1)
                data = json.loads(lines[0])
                self.assertEqual(data["skill"], "docker-build-push")
                self.assertEqual(data["action"], "dispatch")
                self.assertEqual(data["duration_ms"], 45.67)
            finally:
                if orig is not None:
                    os.environ["SKILL_PROOF_DATA_DIR"] = orig
                else:
                    os.environ.pop("SKILL_PROOF_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()
