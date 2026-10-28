"""Tests for skill immutability detection and filesystem read-only enforcement in vault."""
import os
import pathlib
import stat
import tempfile
import unittest

from skill_proof.vault import is_skill_immutable, set_skill_readonly


class TestImmutableVault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        # Restore write permissions so cleanup doesn't fail on Windows
        for p in self.root.rglob("*"):
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
        self.tmp.cleanup()

    def test_detects_immutable_in_yaml_frontmatter(self):
        skill_dir = self.root / "my-sop"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: my-sop\nimmutable: true\ndescription: Core SOP\n---\n# SOP\nDo not change.",
            encoding="utf-8",
        )
        self.assertTrue(is_skill_immutable(skill_dir))
        self.assertTrue(is_skill_immutable(skill_md))

    def test_detects_governance_locked(self):
        skill_dir = self.root / "locked-sop"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: locked-sop\ngovernance: locked\n---\n# Rule",
            encoding="utf-8",
        )
        self.assertTrue(is_skill_immutable(skill_dir))

    def test_detects_protected_flag(self):
        skill_dir = self.root / "protected-sop"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: protected-sop\nprotected: true\n---\n# Rule",
            encoding="utf-8",
        )
        self.assertTrue(is_skill_immutable(skill_dir))

    def test_returns_false_for_normal_skill(self):
        skill_dir = self.root / "normal-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: normal-skill\ndescription: normal\n---\n# Hi",
            encoding="utf-8",
        )
        self.assertFalse(is_skill_immutable(skill_dir))

    def test_returns_false_for_missing_file_or_dir(self):
        self.assertFalse(is_skill_immutable(self.root / "nonexistent"))

    def test_set_skill_readonly_applies_and_removes(self):
        skill_dir = self.root / "readonly-skill"
        skill_dir.mkdir()
        md = skill_dir / "SKILL.md"
        md.write_text("hello", encoding="utf-8")

        # Make read-only
        ok = set_skill_readonly(skill_dir, readonly=True)
        self.assertTrue(ok)

        # On read-only, attempting to overwrite should fail
        with self.assertRaises(PermissionError):
            with open(md, "w", encoding="utf-8") as f:
                f.write("overwrite")

        # Revert read-only
        ok = set_skill_readonly(skill_dir, readonly=False)
        self.assertTrue(ok)
        with open(md, "w", encoding="utf-8") as f:
            f.write("now writeable")
        self.assertEqual(md.read_text(encoding="utf-8"), "now writeable")


if __name__ == "__main__":
    unittest.main()
