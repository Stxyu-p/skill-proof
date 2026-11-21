import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import skill_proof._dialogue as dialogue
from skill_proof._catalog import scan_catalog
from skill_proof._dialogue import _MEMCORE_TRIGGER_RE, _skill_is_negated, memcore_skill_vetoes
from skill_proof._ranking import select_skill


class TestMemCoreSynergy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.temp_dir.name)
        self.db_path = self.tmp / "memory.db"
        dialogue._MEMCORE_VETO_CACHE = (0.0, set())

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE memory (
                id TEXT PRIMARY KEY,
                current_version_id TEXT,
                lifecycle TEXT,
                scope TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE memory_version (
                id TEXT PRIMARY KEY,
                content TEXT
            )
            """
        )
        # Only accepted + project scope should apply via SKILL_PROOF_MEMCORE_DB opt-in
        conn.execute("INSERT INTO memory VALUES ('m1', 'v1', 'accepted', 'project')")
        conn.execute(
            "INSERT INTO memory_version VALUES ('v1', 'ห้ามใช้ test-driven-development ในโปรเจกต์นี้')"
        )
        conn.execute("INSERT INTO memory VALUES ('m2', 'v2', 'candidate', 'project')")
        conn.execute(
            "INSERT INTO memory_version VALUES (?, ?)",
            ("v2", "don't use candidate-only-skill"),
        )
        conn.execute("INSERT INTO memory VALUES ('m3', 'v3', 'accepted', 'private')")
        conn.execute("INSERT INTO memory_version VALUES ('v3', 'never use private-only-skill')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()
        dialogue._MEMCORE_VETO_CACHE = (0.0, set())

    def test_memcore_regex_triggers(self):
        self.assertIn(
            "test-driven-development",
            [m.group(1).lower() for m in _MEMCORE_TRIGGER_RE.finditer("ห้ามใช้ test-driven-development")],
        )
        self.assertIn("tdd", [m.group(1).lower() for m in _MEMCORE_TRIGGER_RE.finditer("don't use tdd")])
        self.assertIn("code-review", [m.group(1).lower() for m in _MEMCORE_TRIGGER_RE.finditer("อย่าใช้ code-review")])

    def test_neutral_veto_file_blocks_non_negated_select_skill(self):
        veto_file = self.tmp / "skill-vetoes.json"
        veto_file.write_text(json.dumps(["tdd"]), encoding="utf-8")

        skills_root = self.tmp / "skills"
        (skills_root / "tdd").mkdir(parents=True)
        (skills_root / "tdd" / "SKILL.md").write_text(
            "---\nname: tdd\ndescription: Write tests before writing application code workflow\n---\nSteps.\n",
            encoding="utf-8",
        )
        (skills_root / "code-review").mkdir(parents=True)
        (skills_root / "code-review" / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: Inspect pull requests for quality\n---\nSteps.\n",
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {
                "SKILL_PROOF_VETO_FILE": str(veto_file),
                "SKILL_PROOF_MEMCORE_DB": "",
            },
            clear=False,
        ):
            dialogue._MEMCORE_VETO_CACHE = (0.0, set())
            catalog = scan_catalog({"local": skills_root})
            sel_tdd = select_skill(catalog, "apply tdd workflow for this module")
            self.assertEqual(sel_tdd.status, "no_match")
            self.assertEqual(sel_tdd.reason, "negated_skill")

            sel_unrelated = select_skill(catalog, "completely unrelated astrology query")
            self.assertEqual(sel_unrelated.status, "no_match")
            self.assertEqual(sel_unrelated.reason, "below_threshold")

            sel_other = select_skill(catalog, "code-review my pull request")
            self.assertEqual(sel_other.status, "selected")
            self.assertEqual(sel_other.selected.skill.name, "code-review")

    def test_opt_in_memcore_db_honors_accepted_project_scope_only(self):
        with mock.patch.dict(
            os.environ,
            {
                "SKILL_PROOF_VETO_FILE": str(self.tmp / "missing-vetoes.json"),
                "SKILL_PROOF_MEMCORE_DB": str(self.db_path),
            },
            clear=False,
        ):
            dialogue._MEMCORE_VETO_CACHE = (0.0, set())
            vetoes = memcore_skill_vetoes()
            self.assertIn("test-driven-development", vetoes)
            self.assertNotIn("candidate-only-skill", vetoes)
            self.assertNotIn("private-only-skill", vetoes)
            self.assertTrue(_skill_is_negated("test-driven-development", "Write unit tests"))
            self.assertFalse(_skill_is_negated("candidate-only-skill", "Run candidate skill"))
            self.assertFalse(_skill_is_negated("private-only-skill", "Run private skill"))

    def test_zero_silent_memcore_db_open_when_env_unset(self):
        fake_home = self.tmp / "fake_home"
        hidden_db_dir = fake_home / ".memcore"
        hidden_db_dir.mkdir(parents=True)
        # Copy the db with an active veto into ~/.memcore/memory.db
        (hidden_db_dir / "memory.db").write_bytes(self.db_path.read_bytes())

        with mock.patch.object(pathlib.Path, "home", return_value=fake_home):
            env_patch = {"SKILL_PROOF_VETO_FILE": str(self.tmp / "none.json")}
            with mock.patch.dict(os.environ, env_patch, clear=False):
                os.environ.pop("SKILL_PROOF_MEMCORE_DB", None)
                dialogue._MEMCORE_VETO_CACHE = (0.0, set())
                self.assertEqual(memcore_skill_vetoes(), set())


if __name__ == "__main__":
    unittest.main()
