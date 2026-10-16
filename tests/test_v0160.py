"""Unit tests for the v0.16+ plan items (Argon audit rounds 12-31, Part 5).

Covers: lightweight English morphology normalizer, filtered-view catalog
cache, confidence-gated vector cascade (lexical fast path), and the
skill-lifecycle cache policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import core
from core import Catalog, InMemoryPolicyStore, SkillProofEngine, SkillRecord, VectorIndex, select_skill

import routing_benchmark
import _dialogue
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


class ContrastiveRoutingTests(unittest.TestCase):
    """v0.16 Part 3.2: clause-level contrastive routing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "skills"
        for name, description in (("alpha", "Testing workflow"), ("beta", "Spreadsheet automation")):
            folder = self.root / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\nInstructions.\n",
                encoding="utf-8",
            )
        self.catalog = core.scan_catalog({"test": self.root})

    def tearDown(self):
        self.tmp.cleanup()

    def test_instead_of_keeps_the_first_side(self):
        result = core.select_skill(self.catalog, "use alpha instead of beta")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "alpha")

    def test_rather_than_keeps_the_first_side(self):
        result = core.select_skill(self.catalog, "run alpha rather than beta")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "alpha")

    def test_thai_but_keeps_the_requested_side(self):
        result = core.select_skill(self.catalog, "อย่าใช้ beta แต่ให้ใช้ alpha")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "alpha")

    def test_explicit_request_survives_contrastive_negation(self):
        result = core.select_skill(self.catalog, "Use $alpha but without beta")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "alpha")

    def test_plain_prose_with_but_is_not_split(self):
        self.assertIsNone(
            _dialogue._contrastive_split(
                "prepare the release but keep careful notes",
                lambda side: _ranking._text_names_skill(self.catalog, side),
            )
        )


class CompanionHandoffTests(unittest.TestCase):
    """v0.16 Part 3.6: companion warm-handoff bonus via related_skills."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "skills"
        specs = (
            ("alpha", "Testing workflow", "related_skills: [beta]\n"),
            ("beta", "Spreadsheet automation", ""),
        )
        for name, description, extra in specs:
            folder = self.root / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n{extra}---\nInstructions.\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_previous_skill_warms_related_companion_scores(self):
        engine = SkillProofEngine({"test": self.root}, policy_store=InMemoryPolicyStore())
        engine.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $alpha")
        warm = engine.begin_turn(turn_id="2", session_id="s", task_id="t", query="spreadsheet automation")

        cold = SkillProofEngine({"test": self.root}, policy_store=InMemoryPolicyStore())
        cold_turn = cold.begin_turn(turn_id="1", session_id="s", task_id="t", query="spreadsheet automation")

        warm_beta = {candidate.skill.name: candidate for candidate in warm.selection.candidates}.get("beta")
        cold_beta = {candidate.skill.name: candidate for candidate in cold_turn.selection.candidates}.get("beta")
        self.assertIsNotNone(warm_beta)
        self.assertIsNotNone(cold_beta)
        self.assertIn("companion_handoff", warm_beta.reasons)
        self.assertNotIn("companion_handoff", cold_beta.reasons)
        self.assertAlmostEqual(warm_beta.score, cold_beta.score + core._COMPANION_BONUS, places=4)


class ProofCacheTests(unittest.TestCase):
    """v0.16 Part 3.5: stat-signature proof cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "skills"
        folder = self.root / "alpha"
        folder.mkdir(parents=True)
        self.skill_file = folder / "SKILL.md"
        self.skill_file.write_text(
            "---\nname: alpha\ndescription: Testing workflow\n---\nInstructions.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_stats_reuse_proof_without_resolving(self):
        engine = SkillProofEngine({"test": self.root}, policy_store=InMemoryPolicyStore())
        engine.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $alpha")
        original_resolve = pathlib.Path.resolve
        calls: list[str] = []

        def counting_resolve(self_path, *args, **kwargs):
            calls.append(str(self_path))
            return original_resolve(self_path, *args, **kwargs)

        with mock.patch.object(pathlib.Path, "resolve", autospec=True, side_effect=counting_resolve):
            self.assertTrue(engine.observe_skill_loaded(session_id="s", task_id="t", skill_name="alpha"))
            first = len(calls)
            self.assertTrue(engine.observe_skill_loaded(session_id="s", task_id="t", skill_name="alpha"))
        self.assertGreater(first, 0)
        self.assertEqual(len(calls), first, "an unchanged stat signature must reuse the cached proof")

        stat = self.skill_file.stat()
        os.utime(self.skill_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        with mock.patch.object(pathlib.Path, "resolve", autospec=True, side_effect=counting_resolve):
            engine.observe_skill_loaded(session_id="s", task_id="t", skill_name="alpha")
        self.assertGreater(len(calls), first, "a changed mtime must re-verify and re-resolve")


class BundleCacheBlockTests(unittest.TestCase):
    """v0.16 Part 3.7: mtime-cached bundle hashing + optional modified-block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "skills"
        self.folder = self.root / "demo-skill"
        self.folder.mkdir(parents=True)
        (self.folder / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: Demo workflow\n---\nInstructions.\n",
            encoding="utf-8",
        )
        (self.folder / "notes.md").write_text("notes v1", encoding="utf-8")
        self.lock = pathlib.Path(self.tmp.name) / "lock.json"
        self.files = ["SKILL.md", "notes.md"]

    def tearDown(self):
        self.tmp.cleanup()

    def _write_lock(self, bundle_hex):
        self.lock.write_text(
            json.dumps(
                {
                    "version": 1,
                    "installed": {
                        "demo-skill": {
                            "source": "skills.sh",
                            "trust_level": "community",
                            "scan_verdict": "safe",
                            "content_hash": "sha256:" + bundle_hex[:16],
                            "files": list(self.files),
                            "metadata": {"source_revision": "abc123"},
                            "scan_provenance": {"bundle_hash": "sha256:" + bundle_hex},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_unchanged_mtimes_reuse_bundle_digest(self):
        from _catalog import _BUNDLE_SHA_CACHE

        _BUNDLE_SHA_CACHE.clear()
        original_read = pathlib.Path.read_bytes
        reads: list[str] = []

        def counting_read(self_path):
            reads.append(str(self_path))
            return original_read(self_path)

        with mock.patch.object(pathlib.Path, "read_bytes", autospec=True, side_effect=counting_read):
            first = core._bundle_sha256(self.folder, self.files)
            reads_after_first = len(reads)
            second = core._bundle_sha256(self.folder, self.files)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(reads_after_first, 2)
        self.assertEqual(len(reads), 2, "unchanged mtimes must reuse the cached digest")

        stat = (self.folder / "notes.md").stat()
        os.utime(self.folder / "notes.md", ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        with mock.patch.object(pathlib.Path, "read_bytes", autospec=True, side_effect=counting_read):
            third = core._bundle_sha256(self.folder, self.files)
        self.assertGreater(len(reads), 2, "a changed mtime must re-hash the bundle")
        self.assertEqual(third, first)

    def test_modified_bundle_blocks_selection_when_enabled(self):
        self._write_lock("0" * 64)
        engine = SkillProofEngine(
            {"test": self.root},
            hub_lock_path=self.lock,
            block_modified_hub_skills=True,
            policy_store=InMemoryPolicyStore(),
        )
        turn = engine.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $demo-skill")
        self.assertEqual(turn.selection.status, "blocked")
        self.assertEqual(turn.selection.reason, "hub_modified")
        self.assertIsNone(turn.selection.selected)

        allowed = SkillProofEngine(
            {"test": self.root}, hub_lock_path=self.lock, policy_store=InMemoryPolicyStore()
        )
        ok_turn = allowed.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $demo-skill")
        self.assertEqual(ok_turn.selection.status, "selected")

        self._write_lock(core._bundle_sha256(self.folder, self.files))
        matching = SkillProofEngine(
            {"test": self.root},
            hub_lock_path=self.lock,
            block_modified_hub_skills=True,
            policy_store=InMemoryPolicyStore(),
        )
        match_turn = matching.begin_turn(turn_id="1", session_id="s", task_id="t", query="Use $demo-skill")
        self.assertEqual(match_turn.selection.status, "selected")


class AuditChainCalibrateTests(unittest.TestCase):
    """v0.16 Part 3.1: audit hash chain + /skill-proof calibrate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = pathlib.Path(self.tmp.name) / "audit.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _plugin(self):
        ctx = FakeContext({"audit_log": True, "audit_path": str(self.audit_path)})
        return plugin_module.SkillProofPlugin(ctx, clock=_FakeClock())

    @staticmethod
    def _receipt(turn_id, status, name=None, score=None, override=None):
        return {
            "turn_id": turn_id,
            "decision": {"status": status},
            "selected": {"name": name, "score": score} if name else {},
            "evidence": {"override_loaded": override} if override else {},
        }

    def test_chain_links_and_tamper_detection(self):
        plugin = self._plugin()
        plugin._write_audit(self._receipt("1", "selected", "alpha", 0.5), "s")
        plugin._write_audit(self._receipt("2", "no_match"), "s")
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        first, second = json.loads(lines[0]), json.loads(lines[1])
        self.assertIsNone(first.get("prev_sha256"))
        self.assertEqual(second["prev_sha256"], hashlib.sha256(lines[0].encode("utf-8")).hexdigest()[:16])
        report = plugin_module.verify_audit_chain(self.audit_path)
        self.assertTrue(report["ok"])
        self.assertEqual(report["lines_checked"], 2)

        tampered = lines[0].replace('"alpha"', '"evil"')
        self.audit_path.write_text("\n".join([tampered, lines[1]]) + "\n", encoding="utf-8")
        broken = plugin_module.verify_audit_chain(self.audit_path)
        self.assertFalse(broken["ok"])
        self.assertEqual(broken["broken_links"], 1)

    def test_new_instance_resumes_chain_from_disk(self):
        first = self._plugin()
        first._write_audit(self._receipt("1", "selected", "alpha", 0.5), "s")
        head_line = self.audit_path.read_text(encoding="utf-8").splitlines()[0]
        second = self._plugin()
        second._write_audit(self._receipt("2", "no_match"), "s")
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            json.loads(lines[1])["prev_sha256"], hashlib.sha256(head_line.encode("utf-8")).hexdigest()[:16]
        )

    def test_command_surfaces_chain_and_calibration(self):
        plugin = self._plugin()
        for i in range(6):
            plugin._write_audit(self._receipt(f"n{i}", "no_match"), "s")
        for i in range(2):
            plugin._write_audit(self._receipt(f"s{i}", "selected", "alpha", 0.4), "s")
        for i in range(3):
            plugin._write_audit(self._receipt(f"o{i}", "selected", "alpha", 0.5, override="beta"), "s")

        verify_text = plugin.handle_command("audit-verify")
        self.assertIn("intact", verify_text)

        payload = plugin.calibrate()
        self.assertEqual(payload["turns"], 11)
        kinds = {item["setting"] for item in payload["suggestions"]}
        self.assertIn("min_score", kinds)
        self.assertIn("synonyms", kinds)
        self.assertEqual(payload["median_selected_score"], 0.5)
        json.dumps(payload)

        text = plugin.handle_command("calibrate")
        self.assertIn("calibration", text)


if __name__ == "__main__":
    unittest.main()
