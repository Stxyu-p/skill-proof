import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from skill_proof.crystallize import SkillProof, crystallize_skill, save_crystallized_skill


class TestCrystallizeSkill(unittest.TestCase):
    def test_crystallize_valid_skill(self):
        steps = [
            "Run platform tools probe to verify ADB connection.",
            "Apply safe power tuning settings via shell command.",
            "Verify device responds with battery status OK.",
        ]
        skill_md, proof = crystallize_skill(
            name="android-battery-tune",
            description="Tune device power settings for battery longevity.",
            steps=steps,
            verification_cmd="adb shell dumpsys battery",
            exit_code=0,
            executed=True,
            category="devops",
        )

        self.assertTrue(skill_md.startswith("---\n"))
        self.assertIn("name: android-battery-tune", skill_md)
        self.assertIn('description: "Tune device power settings for battery longevity."', skill_md)
        self.assertIn("platforms: [linux, macos, windows]", skill_md)
        self.assertIn("## Procedure", skill_md)
        self.assertIn("## Verification", skill_md)
        self.assertIn("adb shell dumpsys battery", skill_md)

        # Check Skill Proof
        self.assertEqual(proof.status, "verified")
        self.assertEqual(proof.exit_code, 0)
        self.assertTrue(len(proof.proof_hash) == 64)
        self.assertIn(f"proof_hash: {proof.proof_hash}", skill_md)

    def test_crystallize_without_execution_marks_declared(self):
        skill_md, proof = crystallize_skill(
            name="unrun-skill",
            description="Skill with verification command never executed.",
            steps=["Step 1"],
            verification_cmd="pytest tests/unit",
        )
        self.assertEqual(proof.status, "declared")
        self.assertIsNone(proof.exit_code)
        self.assertIn("status: declared", skill_md)
        self.assertNotIn("status: verified", skill_md)

    def test_description_length_enforced_under_60_chars(self):
        long_desc = "This is an unnecessarily long description that exceeds sixty characters easily."
        skill_md, _ = crystallize_skill(
            name="short-skill",
            description=long_desc,
            steps=["Step 1"],
            verification_cmd="echo ok",
            exit_code=0,
        )
        for line in skill_md.splitlines():
            if line.startswith("description:"):
                desc_val = line.split(":", 1)[1].strip().strip('"\'')
                self.assertLessEqual(len(desc_val), 60)
                self.assertTrue(desc_val.endswith("."))

    def test_failed_verification_marks_unverified(self):
        _, proof = crystallize_skill(
            name="failing-skill",
            description="Failing skill workflow.",
            steps=["Step 1"],
            verification_cmd="pytest tests/fail.py",
            exit_code=1,
        )
        self.assertEqual(proof.status, "unverified")

    def test_escapes_inner_quotes_in_yaml_frontmatter(self):
        skill_md, _ = crystallize_skill(
            name="quoted-cmd",
            description='Check "quotes" inside desc.',
            steps=["Step 1"],
            verification_cmd='python -c "print(1)"',
            exit_code=0,
            executed=True,
        )
        self.assertIn('verification_cmd: "python -c \\"print(1)\\""', skill_md)
        self.assertIn('description: "Check \\"quotes\\" inside desc."', skill_md)

    def test_save_crystallized_skill_dual_sync(self):
        tmp = tempfile.mkdtemp(prefix="tt-skill-test-")
        v_dir = pathlib.Path(tmp) / "vault"
        h_dir = pathlib.Path(tmp) / "hermes_skills"

        skill_md, _ = crystallize_skill(
            name="test-workflow",
            description="Testing dual storage sync.",
            steps=["Step 1"],
            verification_cmd="echo 1",
            category="testing",
        )

        res = save_crystallized_skill(
            skill_md=skill_md,
            name="test-workflow",
            category="testing",
            vault_dir=v_dir,
            hermes_skills_dir=h_dir,
        )

        self.assertTrue(pathlib.Path(res["vault_file"]).is_file())
        self.assertTrue(pathlib.Path(res["hermes_file"]).is_file())
        self.assertEqual(pathlib.Path(res["vault_file"]).name, "SKILL.md")
        self.assertEqual(pathlib.Path(res["hermes_file"]).name, "SKILL.md")


if __name__ == "__main__":
    unittest.main()
