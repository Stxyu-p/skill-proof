"""Tests for skill immutability detection and filesystem read-only enforcement in vault."""
import os
import pathlib
import stat
import tempfile
import unittest

from skill_proof.vault import (
    _is_safe_skill_name,
    create_junction_or_link,
    is_skill_immutable,
    migrate_from_roots,
    pin,
    set_skill_readonly,
    unpin,
)


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

    def test_pin_rejects_skill_name_that_escapes_vault_and_target_roots(self):
        vault = self.root / "vault"
        target_root = self.root / "targets" / "agent"
        outside = self.root / "outside"
        (vault / "outside").mkdir(parents=True)
        (vault / "outside" / "SKILL.md").write_text("inside vault", encoding="utf-8")
        outside.mkdir()
        (outside / "SKILL.md").write_text("outside vault", encoding="utf-8")

        result = pin("../outside", target_root, vault_dir=vault)

        self.assertFalse(result["success"])
        self.assertFalse((target_root.parent / "outside").exists())

    def test_pin_rejects_windows_normalized_parent_name_even_with_force(self):
        targets = self.root / "targets"
        target_root = targets / "agent"
        vault = targets / "vault"
        targets.mkdir()
        (targets / "SKILL.md").write_text("parent skill", encoding="utf-8")
        sentinel = targets / "keep.txt"
        sentinel.write_text("must survive", encoding="utf-8")

        self.assertFalse(_is_safe_skill_name(".. "))
        result = pin(".. ", target_root, vault_dir=vault, force=True)

        self.assertFalse(result["success"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "must survive")

    def test_unpin_rejects_skill_name_that_escapes_target_root(self):
        vault = self.root / "vault"
        target_root = self.root / "targets" / "agent"
        outside_target = self.root / "targets" / "outside"
        skill = vault / "outside"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("safe skill", encoding="utf-8")
        outside_target.parent.mkdir(parents=True)
        if not create_junction_or_link(skill, outside_target):
            self.skipTest("symlinks/junctions unavailable")

        result = unpin("../outside", target_root)

        self.assertFalse(result["success"])
        self.assertTrue(outside_target.exists() or outside_target.is_symlink())

    def test_failed_vault_upgrade_preserves_existing_skill(self):
        from unittest import mock

        vault = self.root / "vault"
        source_root = self.root / "source"
        old_skill = vault / "demo"
        source_skill = source_root / "demo"
        old_skill.mkdir(parents=True)
        source_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_text("old", encoding="utf-8")
        (source_skill / "SKILL.md").write_text("new richer skill content", encoding="utf-8")

        with mock.patch("skill_proof.vault.shutil.copytree", side_effect=OSError("simulated copy failure")):
            migrate_from_roots({"source": source_root}, vault_dir=vault)

        self.assertEqual((old_skill / "SKILL.md").read_text(encoding="utf-8"), "old")

    def test_failed_vault_upgrade_replace_preserves_existing_skill(self):
        from unittest import mock

        vault = self.root / "vault"
        source_root = self.root / "source"
        old_skill = vault / "demo"
        source_skill = source_root / "demo"
        old_skill.mkdir(parents=True)
        source_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_text("old", encoding="utf-8")
        (source_skill / "SKILL.md").write_text("new richer skill content", encoding="utf-8")

        real_replace = __import__("os").replace
        replace_calls = 0

        def fail_staged_install_once(source, target):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise OSError("simulated staged install failure")
            return real_replace(source, target)

        with mock.patch("skill_proof.vault.os.replace", side_effect=fail_staged_install_once):
            migrate_from_roots({"source": source_root}, vault_dir=vault)

        self.assertEqual((old_skill / "SKILL.md").read_text(encoding="utf-8"), "old")

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
