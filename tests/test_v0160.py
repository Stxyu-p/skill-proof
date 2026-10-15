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
from core import Catalog, InMemoryPolicyStore, SkillProofEngine, SkillRecord, VectorIndex, select_skill

import routing_benchmark
import _ranking

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


class CandidatePruningTests(unittest.TestCase):
    def _catalog(self, specs):
        skills = tuple(
            SkillRecord(
                str(i), name, name, desc, (), "r1", f"p{i}", f"sha{i}", 100,
                pathlib.Path("."), pathlib.Path("."),
            )
            for i, (name, desc) in enumerate(specs)
        )
        return Catalog(skills, (), "h1")

    def test_index_is_built_on_catalog_construction(self):
        catalog = self._catalog([("python-tdd", "Write Python tests before implementation")])
        self.assertIsNotNone(catalog.index)
        self.assertEqual(catalog.index.token_postings["python"], (0,))
        self.assertEqual(catalog.index.name_to_index["python-tdd"], 0)

    def test_prefilter_adds_identifier_and_phrase_hits_without_token_overlap(self):
        catalog = self._catalog([("python-tdd", "alpha multi axis beta")])
        index = catalog.index
        ident = core._candidate_indices(index, set(), frozenset(), "use python-tdd now", {})
        self.assertEqual(ident, {0})
        phrase = core._candidate_indices(index, set(), frozenset(), "alpha multi axis", {})
        self.assertEqual(phrase, {0})

    def test_pruning_reduces_the_scored_set_on_the_fixture(self):
        data = routing_benchmark.load_fixture(routing_benchmark.DEFAULT_FIXTURE)
        with routing_benchmark.fixture_catalog(data) as catalog:
            tokens = set(core._tokens("review my pull request"))
            core._add_morph_bases(tokens)
            candidates = core._candidate_indices(
                catalog.index, tokens, core._char_ngrams("review my pull request"),
                "review my pull request", {},
            )
            total = len(catalog.skills)
        self.assertTrue(candidates)
        self.assertLess(len(candidates), total)

    def test_pruning_is_result_identical_on_the_shipped_fixture(self):
        data = routing_benchmark.load_fixture(routing_benchmark.DEFAULT_FIXTURE)
        with routing_benchmark.fixture_catalog(data) as catalog:
            full = set(range(len(catalog.skills)))
            queries = [str(case["query"]) for case in data["cases"]]

            def run():
                return [
                    core.select_skill(
                        catalog, query, explicit_names=core.extract_explicit_skill_names(query)
                    )
                    for query in queries
                ]

            with mock.patch.object(_ranking, "_candidate_indices", side_effect=lambda *a, **k: set(full)):
                baseline = run()
            pruned = run()
        self.assertEqual(len(baseline), len(queries))
        for query, before, after in zip(queries, baseline, pruned):
            self.assertEqual(before.status, after.status, query)
            self.assertEqual(before.reason, after.reason, query)
            self.assertEqual(
                before.selected.skill.name if before.selected else None,
                after.selected.skill.name if after.selected else None,
                query,
            )
            self.assertEqual(
                [(c.skill.name, c.score) for c in before.candidates],
                [(c.skill.name, c.score) for c in after.candidates],
                query,
            )


class _FakeClock:
    """Callable monotonic-style clock advanced explicitly by tests."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SeamTests(unittest.TestCase):
    """v0.16 plan item 5.1: injected clock / turn-policy store (test seams)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "skills"
        (self.root / "alpha").mkdir(parents=True)
        (self.root / "alpha" / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: Testing workflow\n---\nInstructions.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_plugin_skills_cache_ttl_uses_injected_clock_without_sleeping(self):
        calls: list[str] = []

        class CountingContext(FakeContext):
            def dispatch_tool(inner_self, name, args):
                calls.append(name)
                return super().dispatch_tool(name, args)

        clock = _FakeClock()
        plugin = plugin_module.SkillProofPlugin(
            CountingContext({"skill_roots": [str(self.root)]}), clock=clock
        )
        plugin.on_pre_llm_call(session_id="s", task_id="t", turn_id="1", user_message="hello")
        self.assertEqual(calls.count("skills_list"), 1)
        clock.advance(5)
        plugin.on_pre_llm_call(session_id="s", task_id="t", turn_id="2", user_message="hello")
        self.assertEqual(calls.count("skills_list"), 1, "within the 30s TTL the cached listing must be reused")
        clock.advance(31)
        plugin.on_pre_llm_call(session_id="s", task_id="t", turn_id="3", user_message="hello")
        self.assertEqual(calls.count("skills_list"), 2, "after the 30s TTL the listing must be refetched")

    def test_engine_metrics_use_injected_clock(self):
        engine = SkillProofEngine(
            {"test": self.root}, clock=_FakeClock(), policy_store=InMemoryPolicyStore()
        )
        turn = engine.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $alpha")
        self.assertEqual(turn.selection.status, "selected")
        performance = engine._state(turn_id="1").performance
        self.assertEqual(performance["scan_ms"], 0.0)
        self.assertEqual(performance["selection_ms"], 0.0)
        self.assertEqual(performance["core_ms"], 0.0)

    def test_begin_turn_publishes_and_clears_turn_policy(self):
        store = InMemoryPolicyStore()
        engine = SkillProofEngine(
            {"test": self.root},
            mode="enforce-tools",
            invariants={"alpha": {"forbidden_tools": ["rm"], "ordered_tools": ["first", "second"]}},
            policy_store=store,
        )
        engine.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $alpha")
        state = store.read()
        self.assertEqual(state["mode"], "enforce-tools")
        self.assertEqual(state["selected_skill"], "alpha")
        self.assertEqual(state["forbidden_tools"], ["rm"])
        self.assertEqual(state["ordered_tools"], ["first", "second"])
        self.assertEqual(state["completed"], [])

        engine.begin_turn(turn_id="2", session_id="s", task_id="t", query="qqqq zzzz nonsense")
        cleared = store.read()
        self.assertIsNone(cleared.get("selected_skill"))
        self.assertNotIn("forbidden_tools", cleared)


if __name__ == "__main__":
    unittest.main()
