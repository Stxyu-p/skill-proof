"""Tests for v0.11.0 features:

- Fleet Profile Awareness & Subagent Routing (Feature A)
- Companion Skill Chaining (Feature B)
- Interactive Query Diagnostics & `cli.py eval` (Feature C)
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import cli
import core

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]


def _skill_bytes(name: str, description: str, *, tags: str = "", related: str = "") -> bytes:
    tags_line = f"tags: [{tags}]\n" if tags else ""
    related_line = f"related_skills: [{related}]\n" if related else ""
    return (
        f"---\nname: {name}\ndescription: {description}\n{tags_line}{related_line}---\n\n"
        "Instruction body text.\n"
    ).encode("utf-8")


class FleetRoutingTests(unittest.TestCase):
    def test_suggest_fleet_agent_mapping(self):
        # Code review / audit -> altima
        self.assertEqual(core.suggest_fleet_agent("Please review this PR diff", "github-code-review"), "altima")
        self.assertEqual(core.suggest_fleet_agent("audit these changes", "code-review"), "altima")

        # Software / build / tdd -> sora
        self.assertEqual(core.suggest_fleet_agent("fix this bug and run tests", "systematic-debugging"), "sora")
        self.assertEqual(core.suggest_fleet_agent("build this backend endpoint", "python-tdd"), "sora")

        # Evidence / research -> nua
        self.assertEqual(core.suggest_fleet_agent("verify evidence across multiple sources", "evidence-check"), "nua")
        self.assertEqual(core.suggest_fleet_agent("research benchmark numbers", "market-research"), "nua")

        # Design / UI / UX -> milim
        self.assertEqual(core.suggest_fleet_agent("design a landing page with clean tokens", "ui-ux-pro-max"), "milim")
        self.assertEqual(core.suggest_fleet_agent("style this modal with Tailwind", "frontend-design"), "milim")

        # Thai Fleet Agent Mapping
        self.assertEqual(core.suggest_fleet_agent("ช่วยรีวิวโค้ดชุดนี้ให้หน่อย"), "altima")
        self.assertEqual(core.suggest_fleet_agent("เขียนโค้ดและแก้บั๊กตรงนี้"), "sora")
        self.assertEqual(core.suggest_fleet_agent("ค้นคว้าหาข้อมูลและหลักฐาน"), "nua")
        self.assertEqual(core.suggest_fleet_agent("ช่วยออกแบบหน้าเว็บให้สวยหรู"), "milim")

        # Default / orchestrator -> mika
        self.assertEqual(core.suggest_fleet_agent("plan the next steps for this project", None), "mika")

    def test_detect_agent_roots_with_profile(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_profile_") as tmp:
            tmp_path = pathlib.Path(tmp)
            home = tmp_path / "home"
            hermes_home = home / ".hermes"
            profile_dir = hermes_home / "profiles" / "altima" / "skills"
            profile_dir.mkdir(parents=True)

            detected = core.detect_agent_roots(home=home, hermes_home=hermes_home, profile="altima")
            self.assertIn("profile-altima", detected)
            self.assertEqual(detected["profile-altima"], profile_dir)


class CompanionSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_companion_")
        self.root = pathlib.Path(self.tmp.name)

        d1 = self.root / "python-tdd"
        d1.mkdir()
        (d1 / "SKILL.md").write_bytes(
            _skill_bytes("python-tdd", "Write tests before Python implementation", related="verify-before-done, systematic-debugging")
        )

        d2 = self.root / "verify-before-done"
        d2.mkdir()
        (d2 / "SKILL.md").write_bytes(
            _skill_bytes("verify-before-done", "Verify evidence before claiming completion")
        )

        d3 = self.root / "systematic-debugging"
        d3.mkdir()
        (d3 / "SKILL.md").write_bytes(
            _skill_bytes("systematic-debugging", "Four-phase root cause debugging")
        )

        self.catalog = core.scan_catalog({"local": self.root})

    def tearDown(self):
        self.tmp.cleanup()

    def test_related_skills_parsed_in_record(self):
        skill = next(s for s in self.catalog.skills if s.name == "python-tdd")
        self.assertEqual(skill.related_skills, ("verify-before-done", "systematic-debugging"))

    def test_companion_skills_attached_to_selection(self):
        selection = core.select_skill(self.catalog, "write python unit tests with tdd")
        self.assertEqual(selection.status, "selected")
        self.assertIsNotNone(selection.selected)
        self.assertEqual(selection.selected.skill.name, "python-tdd")
        self.assertEqual(selection.selected.companions, ("verify-before-done", "systematic-debugging"))
        self.assertEqual(selection.suggested_agent, "sora")

    def test_missing_companions_are_filtered(self):
        # A related skill not in catalog must not be listed as an available companion
        d4 = self.root / "orphan"
        d4.mkdir()
        (d4 / "SKILL.md").write_bytes(
            _skill_bytes("orphan", "An isolated skill", related="nonexistent-skill-xyz")
        )
        catalog = core.scan_catalog({"local": self.root})
        selection = core.select_skill(catalog, "an isolated skill")
        self.assertEqual(selection.selected.skill.name, "orphan")
        self.assertEqual(selection.selected.companions, ())


class CliEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_eval_")
        self.root = pathlib.Path(self.tmp.name)
        d = self.root / "demo"
        d.mkdir()
        (d / "SKILL.md").write_bytes(
            _skill_bytes("demo-skill", "Demo workflow for testing CLI eval", related="other-skill")
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_eval_payload(self):
        payload = cli._eval_payload(
            {"local": self.root},
            query="run demo-skill workflow",
            min_score=0.28,
            min_margin=0.05,
            limit=5,
        )
        self.assertIn("tokens", payload["query_breakdown"])
        self.assertIn("candidates", payload)
        self.assertEqual(payload["decision"]["status"], "selected")
        self.assertEqual(payload["decision"]["selected_skill"], "demo-skill")
        self.assertIn("suggested_agent", payload["decision"])


if __name__ == "__main__":
    unittest.main()
