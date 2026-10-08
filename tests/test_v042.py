"""Regression tests for the v0.4.2 fixes.

Covers the negation window for phrase vetoes ("don't use the skill X"),
YAML block-list ``tags:``, Windows junction (reparse point) classification,
cross-root precedence with alias collapse, and the ``listed_but_unindexed``
selection reason.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest

import core
from core import SkillProofEngine, extract_explicit_skill_names, scan_catalog, select_skill


def _skill_bytes(name: str, description: str, extra: str = "") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\nBody text.\n".encode("utf-8")


class NegationWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_negation_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        for name, description in (
            ("python-tdd", "Write Python tests before implementation"),
            ("frontend-design", "Design a polished landing page"),
        ):
            folder = self.root / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(_skill_bytes(name, description))

    def test_phrase_vetoes_never_become_a_selection(self):
        catalog = scan_catalog({"local": self.root})
        cases = (
            "don't use the skill python-tdd",
            "please don't use the skill python-tdd",
            "do not use the skill python-tdd",
            "never use the skill python-tdd",
            "without using the skill python-tdd",
            "i don't want to use python-tdd for this",
        )
        for query in cases:
            with self.subTest(query=query):
                names = extract_explicit_skill_names(query, known_names=("python-tdd", "frontend-design"))
                selection = select_skill(catalog, query, explicit_names=names)
                self.assertIsNone(selection.selected, query)
                self.assertEqual(selection.reason, "negated_skill", query)

    def test_request_phrasings_are_not_vetoes(self):
        for query in (
            "do not forget to use python-tdd",
            "don't forget python-tdd",
            "rebuild python-tdd docs",
            "not only python-tdd but also the rest",
        ):
            with self.subTest(query=query):
                self.assertFalse(core._skill_is_negated("python-tdd", query), query)

    def test_thai_spaceless_veto_still_matches(self):
        for query in (
            "ไม่ต้องใช้สกิล python-tdd",
            "ไม่ต้องใช้python-tdd",
            "อย่าใช้ python-tdd",
            "ไม่ใช้ skill python-tdd เลย",
        ):
            with self.subTest(query=query):
                self.assertTrue(core._skill_is_negated("python-tdd", query), query)

    def test_rest_of_turn_still_selects_other_skills(self):
        catalog = scan_catalog({"local": self.root})
        query = "don't use the skill python-tdd, use the skill frontend-design"
        names = extract_explicit_skill_names(query, known_names=("python-tdd", "frontend-design"))
        selection = select_skill(catalog, query, explicit_names=names)
        self.assertEqual(selection.status, "selected")
        self.assertEqual(selection.selected.skill.name, "frontend-design")


class TagsBlockListTests(unittest.TestCase):
    def test_block_list_tags_are_parsed(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_tags_") as tmp:
            root = pathlib.Path(tmp)
            folder = root / "tagskill"
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                _skill_bytes("tagskill", "Testing tag parsing", "tags:\n  - ui\n  - design\n")
            )
            record = scan_catalog({"local": root}).by_name("tagskill")[0]
            self.assertEqual(record.tags, ("ui", "design"))

    def test_block_list_tags_are_bounded_and_deduplicated(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_tags_") as tmp:
            root = pathlib.Path(tmp)
            folder = root / "tagskill"
            folder.mkdir()
            items = "\n".join(f"  - tag{i}" for i in range(40))
            items += "\n  - tag0\n"
            (folder / "SKILL.md").write_bytes(
                _skill_bytes("tagskill", "Testing tag parsing", f"tags:\n{items}\n")
            )
            record = scan_catalog({"local": root}).by_name("tagskill")[0]
            self.assertEqual(len(record.tags), 32)
            self.assertEqual(record.tags[0], "tag0")

    def test_inline_tags_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_tags_") as tmp:
            root = pathlib.Path(tmp)
            folder = root / "tagskill"
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                _skill_bytes("tagskill", "Testing tag parsing", "tags: [ui, design]\n")
            )
            record = scan_catalog({"local": root}).by_name("tagskill")[0]
            self.assertEqual(record.tags, ("ui", "design"))


class ReparsePointTests(unittest.TestCase):
    def _make_junction(self, link: pathlib.Path, target: pathlib.Path) -> None:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation unavailable: {result.stdout}{result.stderr}")

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_junction_directory_is_classified_and_pruned(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_junction_") as tmp:
            root = pathlib.Path(tmp)
            physical = root / "physical-skill"
            physical.mkdir()
            (physical / "SKILL.md").write_bytes(_skill_bytes("physical-skill", "Physical copy"))
            outside = root.parent / f"{root.name}-target"
            outside.mkdir()
            (outside / "SKILL.md").write_bytes(_skill_bytes("linked-skill", "Linked copy"))
            self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
            self._make_junction(root / "linked-skill", outside)

            catalog = scan_catalog({"local": root})

            self.assertEqual([skill.name for skill in catalog.skills], ["physical-skill"])
            codes = {item.code for item in catalog.diagnostics}
            self.assertIn("unsafe_reparse", codes)
            self.assertNotIn("unsafe_path", codes)


class CrossRootPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_roots_")
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)

    def _write(self, root: pathlib.Path, name: str, description: str, folder: str = "") -> None:
        skill_dir = root / (folder or name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_bytes(_skill_bytes(name, description))

    def test_identical_copies_collapse_as_alias(self):
        first = self.base / "first"
        second = self.base / "second"
        self._write(first, "shared", "Same content everywhere")
        self._write(second, "shared", "Same content everywhere")

        catalog = scan_catalog({"first-root": first, "second-root": second})

        self.assertEqual(len(catalog.skills), 1)
        self.assertEqual(catalog.skills[0].root_id, "first-root")
        codes = [item.code for item in catalog.diagnostics]
        self.assertEqual(codes, ["alias_skipped"])
        self.assertNotIn("duplicate_name", codes)

    def test_divergent_copies_are_shadowed_by_configured_order(self):
        first = self.base / "first"
        second = self.base / "second"
        self._write(first, "shared", "First variant")
        self._write(second, "shared", "Second variant")

        catalog = scan_catalog({"first-root": first, "second-root": second})

        self.assertEqual(len(catalog.skills), 1)
        self.assertEqual(catalog.skills[0].description, "First variant")
        self.assertEqual(catalog.skills[0].root_id, "first-root")
        self.assertIn("shadowed_by_root", {item.code for item in catalog.diagnostics})

    def test_root_order_is_configuration_order_not_alphabetical(self):
        first = self.base / "first"
        second = self.base / "second"
        self._write(first, "shared", "First variant")
        self._write(second, "shared", "Second variant")

        catalog = scan_catalog({"b-root": second, "a-root": first})

        self.assertEqual(len(catalog.skills), 1)
        self.assertEqual(catalog.skills[0].root_id, "b-root")

    def test_same_root_twins_stay_ambiguous(self):
        root = self.base / "single"
        self._write(root, "twin", "First copy", folder="twin-a")
        self._write(root, "twin", "Second copy", folder="twin-b")

        catalog = scan_catalog({"local": root})

        self.assertEqual(len(catalog.skills), 2)
        self.assertNotIn("shadowed_by_root", {item.code for item in catalog.diagnostics})
        self.assertEqual(
            [item.code for item in catalog.diagnostics if item.code == "duplicate_name"], ["duplicate_name", "duplicate_name"]
        )


class ListedButUnindexedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_host_")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        folder = root / "local-skill"
        folder.mkdir()
        (folder / "SKILL.md").write_bytes(_skill_bytes("local-skill", "Local workflow"))
        self.engine = SkillProofEngine({"local": root})

    def begin(self, turn_id: str, query: str, available_names):
        return self.engine.begin_turn(
            turn_id=turn_id,
            session_id="session",
            task_id="task",
            query=query,
            available_names=available_names,
        )

    def test_host_listed_but_unindexed_name_is_named_as_such(self):
        turn = self.begin("one", "Use $ghost-skill", {"local-skill", "ghost-skill"})

        self.assertEqual(turn.selection.status, "blocked")
        self.assertEqual(turn.selection.reason, "listed_but_unindexed")
        self.assertIn("Hermes lists", turn.context)
        receipt = self.engine.receipt(turn_id="one")
        self.assertEqual(receipt["catalog"]["host_unindexed_count"], 1)
        self.assertEqual(receipt["catalog"]["host_unindexed_sample"], ["ghost-skill"])
        self.assertEqual(receipt["catalog"]["host_unindexed_omitted"], 0)

    def test_unknown_name_stays_unknown_without_host_listing(self):
        turn = self.begin("two", "Use $ghost-skill", {"local-skill"})

        self.assertEqual(turn.selection.status, "blocked")
        self.assertEqual(turn.selection.reason, "unknown_explicit_skill")

    def test_missing_listing_keeps_legacy_reason(self):
        turn = self.engine.begin_turn(
            turn_id="three", session_id="session", task_id="task", query="Use $ghost-skill"
        )

        self.assertEqual(turn.selection.status, "blocked")
        self.assertEqual(turn.selection.reason, "unknown_explicit_skill")

    def test_unavailable_listing_does_not_claim_host_knowledge(self):
        turn = self.engine.begin_turn(
            turn_id="four",
            session_id="session",
            task_id="task",
            query="Use $local-skill",
            available_names=set(),
            availability_error=True,
        )

        self.assertEqual(turn.selection.status, "blocked")
        self.assertEqual(turn.selection.reason, "hermes_catalog_unavailable")


if __name__ == "__main__":
    unittest.main()
