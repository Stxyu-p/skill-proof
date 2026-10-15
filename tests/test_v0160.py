"""Unit tests for the v0.16+ plan items (Argon audit rounds 12-31, Part 5).

Covers: lightweight English morphology normalizer, filtered-view catalog
cache, confidence-gated vector cascade (lexical fast path), and the
skill-lifecycle cache policy.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest import mock

import core
from core import Catalog, SkillRecord, VectorIndex, select_skill

import routing_benchmark

from tests.test_plugin import FakeContext, plugin_module


def _skill(skill_id, name, description):
    return SkillRecord(
        skill_id, name, name, description, (), "r1", f"p{skill_id}", f"sha{skill_id}", 100,
        pathlib.Path("."), pathlib.Path("."),
    )


class MorphologyNormalizerTests(unittest.TestCase):
    def test_normalize_morph_english_examples(self):
        cases = {
            "crashing": "crash",
            "crashes": "crash",
            "tables": "table",
            "migrations": "migration",
            "keeps": "keep",
            "failed": "fail",
            "speed": "speed",
            "class": "class",
            "document": "document",
            "สรุป": "สรุป",
            "3d": "3d",
        }
        for token, expected in cases.items():
            self.assertEqual(core._normalize_morph(token), expected, f"{token!r}")

    def test_stretch_pdf_table_case_now_selects_in_off_mode(self):
        data = routing_benchmark.load_fixture(routing_benchmark.DEFAULT_FIXTURE)
        with routing_benchmark.fixture_catalog(data) as catalog:
            sel = select_skill(catalog, "convert this PDF into a table")
        self.assertEqual(sel.status, "selected")
        self.assertEqual(sel.selected.skill.name, "pdf-extraction")


class FilteredViewCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_v0160_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        for name, description in (
            ("python-tdd", "Write Python tests before implementation"),
            ("slides", "Create strategic HTML presentations"),
        ):
            folder = self.root / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")
            )

    def test_filtered_view_cache_reuses_identical_catalog(self):
        engine = core.SkillProofEngine({"local": self.root})
        names = {"python-tdd"}
        for turn in ("t1", "t2"):
            engine.begin_turn(
                turn_id=turn, session_id="s", task_id="k",
                query="write tests first", available_names=names,
            )
        first = engine._turns["t1"].catalog
        self.assertIs(first, engine._turns["t2"].catalog)
        self.assertEqual([s.name for s in first.skills], ["python-tdd"])

        engine.begin_turn(
            turn_id="t3", session_id="s", task_id="k",
            query="write tests first", available_names={"python-tdd", "slides"},
        )
        self.assertIsNot(engine._turns["t3"].catalog, first)
        self.assertEqual(len(engine._turns["t3"].catalog.skills), 2)
        self.assertEqual(len(engine._filtered_view_cache), 2)

    def test_filtered_view_cache_cleared_on_refresh(self):
        engine = core.SkillProofEngine({"local": self.root})
        engine.begin_turn(turn_id="t1", session_id="s", task_id="k", query="q", available_names={"python-tdd"})
        self.assertEqual(len(engine._filtered_view_cache), 1)
        engine.refresh_catalog()
        self.assertEqual(len(engine._filtered_view_cache), 0)


class VectorFastPathTests(unittest.TestCase):
    def test_fastpath_skips_vectors_when_lexical_is_dominant(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = VectorIndex(pathlib.Path(tmp) / "vec.db", api_key="dummy")
            index._memory_vectors = {"python-tdd": (1.0, 0.0), "slides": (0.0, 1.0)}
            catalog = Catalog(
                (
                    _skill("a", "python-tdd", "Write Python tests before implementation"),
                    _skill("b", "slides", "Create strategic HTML presentations"),
                ),
                (),
                "h1",
            )
            with mock.patch.object(index, "rank") as rank_spy:
                sel = select_skill(catalog, "python-tdd", vector_index=index)
            rank_spy.assert_not_called()
            self.assertEqual(sel.selected.skill.name, "python-tdd")
            self.assertEqual(index.trace_snapshot()["provider_used"], "lexical_fastpath")
            self.assertFalse(index.trace_snapshot()["vector_used"])


class LifecycleCachePolicyTests(unittest.TestCase):
    def test_loaded_keeps_listing_cache_but_listing_changes_clear_it(self):
        plugin = plugin_module.SkillProofPlugin(FakeContext({"skill_roots": []}))
        plugin._skills_cache = (123.0, {"python-tdd"})
        plugin.on_skill_lifecycle(action="loaded", skill_name="python-tdd")
        self.assertEqual(plugin._skills_cache[1], {"python-tdd"})
        plugin.on_skill_lifecycle(action="deleted", skill_name="old-skill")
        self.assertEqual(plugin._skills_cache, (0.0, set()))


if __name__ == "__main__":
    unittest.main()
