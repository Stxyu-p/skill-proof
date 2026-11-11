"""Fail-closed pruning regressions for Skill Proof."""
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from skill_proof import cli
from skill_proof.prune import audit_stats, prune_dead_skills


class TestPruneSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.archive = self.root / "archive"
        self.skill("unused-skill")
        self.audit = self.root / "audit.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def skill(self, name, marker="source"):
        path = self.vault / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(f"# {name}\n{marker}\n", encoding="utf-8")
        return path

    def write_audit(self, *records):
        self.audit.write_text("".join(json.dumps({"schema": "skill-proof.audit.v1", "status": "selected", **r}) + "\n" for r in records), encoding="utf-8")

    def test_missing_audit_refuses_automatic_pruning(self):
        result = prune_dead_skills(vault_dir=self.vault, archive_dir=self.archive, audit_path=self.audit)
        self.assertTrue(result["refused"])
        self.assertEqual(audit_stats(audit_path=self.audit, vault_dir=self.vault)["dead_skills"], [])
        self.assertTrue((self.vault / "unused-skill").exists())
        self.assertFalse(self.archive.exists())

    def test_corrupt_audit_refuses_automatic_pruning(self):
        self.audit.write_text("not-json\n", encoding="utf-8")
        result = prune_dead_skills(vault_dir=self.vault, archive_dir=self.archive, audit_path=self.audit)
        self.assertTrue(result["refused"])
        self.assertTrue((self.vault / "unused-skill").exists())

    def test_schema_only_records_are_not_usable_for_pruning(self):
        self.write_audit({"status": None, "selected": None})
        stats = audit_stats(audit_path=self.audit, vault_dir=self.vault)
        self.assertFalse(stats["prune_safe"])
        self.assertEqual(stats["dead_skills"], [])

    def test_loaded_and_override_loaded_skills_are_not_marked_dead(self):
        self.skill("host-loaded")
        self.skill("override-loaded")
        self.write_audit(
            {"selected": "host-loaded", "loaded": True},
            {"selected": None, "loaded": False, "override_loaded": "override-loaded"},
        )
        stats = audit_stats(audit_path=self.audit, vault_dir=self.vault)
        self.assertNotIn("host-loaded", stats["dead_skills"])
        self.assertNotIn("override-loaded", stats["dead_skills"])
        self.assertIn("unused-skill", stats["dead_skills"])

    def test_cli_prune_passes_audit_path_and_defaults_to_dry_run(self):
        with patch("skill_proof.prune.prune_dead_skills", return_value={
            "vault_dir": "vault", "archive_dir": "archive", "pruned_count": 0,
            "skipped_count": 0, "pruned": [], "skipped": [], "dry_run": True,
        }) as prune:
            self.assertEqual(cli.main(["audit", "prune", "--audit-path", str(self.audit), "--json"]), 0)
        prune.assert_called_once_with(skills_to_prune=None, audit_path=str(self.audit), dry_run=True)

    def test_archive_collision_preserves_both_existing_and_source_data(self):
        self.write_audit({"selected": "someone-else", "loaded": True})
        dest = self.archive / "unused-skill"
        dest.mkdir(parents=True)
        (dest / "old.txt").write_text("keep archive", encoding="utf-8")
        result = prune_dead_skills(vault_dir=self.vault, archive_dir=self.archive,
                                   audit_path=self.audit, dry_run=False)
        self.assertEqual((dest / "old.txt").read_text(encoding="utf-8"), "keep archive")
        self.assertTrue((self.vault / "unused-skill" / "SKILL.md").exists())
        self.assertIn("archive_destination_exists", [x["reason"] for x in result["skipped"]])


if __name__ == "__main__":
    unittest.main()
