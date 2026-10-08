"""Tests for the v0.6.0 features.

The gated routing quality harness (tiered labeled corpus, recall / precision /
abstain gate, threshold sweep) and dictionary-free character n-gram matching for
spaceless scripts (Thai, Lao, Myanmar, Khmer, CJK, Hangul).
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import unittest

import core
import routing_benchmark as bench
from core import _char_ngrams, _ngram_dice, scan_catalog, select_skill

_PACKAGE = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = _PACKAGE / "routing_cases.json"

_UNRELATED = "zebra quokka vicuna narwhal"


def _fixture(skills, cases):
    return {"version": 2, "skills": skills, "cases": cases}


_METRIC_FIXTURE = _fixture(
    [
        {"name": "alpha", "description": "alpha beta gamma"},
        {"name": "delta", "description": "delta epsilon zeta"},
    ],
    [
        {"query": "alpha beta gamma", "expected": "alpha", "category": "direct", "tier": "core"},
        {"query": _UNRELATED, "expected": "alpha", "category": "direct", "tier": "core"},
        {"query": "delta epsilon zeta", "expected": "alpha", "category": "direct", "tier": "core"},
        {"query": _UNRELATED, "expected": None, "category": "abstain", "tier": "core"},
    ],
)


class ShippedFixtureTests(unittest.TestCase):
    """The shipped corpus is the quality contract; shrink it and the gate lies."""

    def setUp(self):
        self.data = bench.load_fixture(FIXTURE)

    def test_corpus_is_wide_enough_to_mean_something(self):
        cases = self.data["cases"]
        self.assertGreaterEqual(len(self.data["skills"]), 10)
        self.assertGreaterEqual(len(cases), 48)
        categories = {case.get("category") for case in cases}
        self.assertGreaterEqual(len(categories), 6)
        self.assertTrue({"explicit", "negation", "abstain", "thai"} <= categories)

    def test_every_case_is_labeled_and_unique(self):
        queries = [case["query"] for case in self.data["cases"]]
        self.assertEqual(len(queries), len(set(queries)), "duplicate queries hide regressions")
        names = {skill["name"] for skill in self.data["skills"]}
        for case in self.data["cases"]:
            self.assertIn(case.get("tier"), {"core", "stretch"})
            self.assertTrue(case.get("category"), case)
            if case["expected"] is not None:
                self.assertIn(case["expected"], names, case)

    def test_core_gate_passes(self):
        report = bench.evaluate(self.data)
        verdict = bench.gate(report)
        self.assertTrue(verdict["passed"], bench.format_report(report))
        self.assertEqual(report["metrics"]["false_selections"], 0)
        self.assertEqual(report["metrics"]["abstain_accuracy"], 1.0)
        self.assertEqual(report["metrics"]["recall"], 1.0)

    def test_stretch_cases_are_reported_not_gated(self):
        report = bench.evaluate(self.data, tiers=("core", "stretch"))
        metrics = report["metrics"]["by_tier"]
        self.assertIn("stretch", metrics)
        self.assertIn("core", metrics)
        for case in self.data["cases"]:
            if case["tier"] == "stretch":
                self.assertTrue(case.get("note"), "every stretch case documents why it is a gap")
        self.assertEqual(metrics["core"]["false_selections"], 0)

    def test_gate_ignores_stretch_tier_by_default(self):
        report = bench.evaluate(self.data)
        self.assertEqual(report["tiers"], list(bench.GATED_TIERS))
        self.assertTrue(bench.gate(report)["passed"])

    def test_shipped_defaults_sit_inside_a_stable_plateau(self):
        """The shipped thresholds must not sit on a cliff edge."""
        report = bench.evaluate(self.data, min_score=0.20, min_margin=0.05)
        self.assertTrue(bench.gate(report)["passed"])
        report = bench.evaluate(self.data, min_score=0.32, min_margin=0.15)
        self.assertTrue(bench.gate(report)["passed"])


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.report = bench.evaluate(_METRIC_FIXTURE)
        self.metrics = self.report["metrics"]

    def test_recall_counts_only_expected_selections(self):
        # 3 cases expect a skill, 1 of them was chosen correctly.
        self.assertEqual(self.metrics["expected_selections"], 3)
        self.assertEqual(self.metrics["correct"], 1)
        self.assertAlmostEqual(self.metrics["recall"], 1 / 3, places=4)

    def test_precision_and_error_counts(self):
        self.assertEqual(self.metrics["selections"], 2)
        self.assertAlmostEqual(self.metrics["precision"], 0.5, places=4)
        self.assertEqual(self.metrics["misses"], 1)
        self.assertEqual(self.metrics["false_selections"], 1)

    def test_abstain_is_measured_separately(self):
        self.assertEqual(self.metrics["expected_abstains"], 1)
        self.assertEqual(self.metrics["abstain_correct"], 1)
        self.assertEqual(self.metrics["abstain_accuracy"], 1.0)
        self.assertAlmostEqual(self.metrics["decision_accuracy"], 0.5, places=4)

    def test_records_carry_candidates_for_diagnosis(self):
        miss = next(r for r in self.report["records"] if r["outcome"] == "miss")
        self.assertEqual(miss["actual"], None)
        self.assertEqual(miss["expected"], "alpha")
        self.assertEqual(miss["reason"], "below_threshold")
        self.assertEqual(miss["candidates"], [], "a zero-overlap query has no candidates at all")
        wrong = next(r for r in self.report["records"] if r["outcome"] == "false_selection")
        self.assertEqual(wrong["actual"], "delta")
        self.assertEqual(wrong["candidates"][0]["name"], "delta")
        self.assertIn("description_terms", wrong["candidates"][0]["reasons"])


class GateTests(unittest.TestCase):
    def test_gate_exposes_each_check(self):
        verdict = bench.gate(bench.evaluate(_METRIC_FIXTURE))
        self.assertFalse(verdict["passed"])
        names = {check["name"] for check in verdict["checks"]}
        self.assertEqual(names, {"recall", "false_selections", "abstain_accuracy"})
        for check in verdict["checks"]:
            self.assertIn("actual", check)
            self.assertIn("requirement", check)
            self.assertIsInstance(check["passed"], bool)

    def test_gate_fails_closed_on_false_selections(self):
        verdict = bench.gate(bench.evaluate(_METRIC_FIXTURE), max_false_selections=0)
        failing = [check["name"] for check in verdict["checks"] if not check["passed"]]
        self.assertIn("false_selections", failing)
        self.assertIn("recall", failing)

    def test_cli_exit_code_follows_the_gate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench.main([str(FIXTURE), "--gate", "--quiet"]), 0)
            self.assertEqual(
                bench.main([str(FIXTURE), "--gate", "--quiet", "--min-recall", "1.01"]), 1
            )

    def test_json_report_is_written(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="skill_proof_report_") as tmp:
            target = pathlib.Path(tmp) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = bench.main([str(FIXTURE), "--json", str(target), "--quiet"])
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["metrics"]["false_selections"], 0)
        self.assertTrue(payload["gate"]["passed"])
        self.assertEqual(payload["skills"], len(bench.load_fixture(FIXTURE)["skills"]))


class SweepTests(unittest.TestCase):
    def test_sweep_grid_is_complete(self):
        rows = bench.sweep(_METRIC_FIXTURE, scores=(0.20, 0.28), margins=(0.0, 0.10))
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(
                set(row),
                {
                    "min_score",
                    "min_margin",
                    "recall",
                    "precision",
                    "abstain_accuracy",
                    "decision_accuracy",
                    "false_selections",
                    "misses",
                },
            )

    def test_zero_margin_costs_precision_on_near_duplicates(self):
        """min_margin=0 silently tie-breaks near-duplicate skills: keep the evidence."""
        data = bench.load_fixture(FIXTURE)
        rows = bench.sweep(data, scores=(0.28,), margins=(0.0, 0.05))
        by_margin = {row["min_margin"]: row for row in rows}
        self.assertGreater(by_margin[0.0]["false_selections"], 0)
        self.assertEqual(by_margin[0.05]["false_selections"], 0)


class FixtureValidationTests(unittest.TestCase):
    def _write(self, payload):
        import tempfile

        tmp = tempfile.TemporaryDirectory(prefix="skill_proof_fixture_")
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "cases.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_missing_sections_are_rejected(self):
        for payload in ({"skills": []}, {"cases": []}, {"skills": [], "cases": "nope"}):
            with self.assertRaises(ValueError):
                bench.load_fixture(self._write(payload))

    def test_case_without_expected_is_rejected(self):
        payload = _fixture([{"name": "a", "description": "b"}], [{"query": "b"}])
        with self.assertRaises(ValueError):
            bench.load_fixture(self._write(payload))

    def test_skill_without_description_is_rejected(self):
        payload = _fixture([{"name": "a"}], [{"query": "b", "expected": "a"}])
        with self.assertRaises(ValueError):
            bench.load_fixture(self._write(payload))

    def test_legacy_fixture_shape_still_loads(self):
        payload = {
            "skills": [
                {"name": "python-tdd", "description": "Write Python tests first"},
                {"name": "thai-docs", "description": "สร้างและตรวจ เอกสาร ภาษาไทย"},
            ],
            "cases": [
                {"query": "write Python tests first", "expected": "python-tdd"},
                {"query": "book a flight to Chiang Mai", "expected": None},
            ],
        }
        report = bench.evaluate(bench.load_fixture(self._write(payload)))
        self.assertEqual(report["tiers"], ["core"])
        self.assertTrue(bench.gate(report)["passed"], bench.format_report(report))


class SpacelessScriptTests(unittest.TestCase):
    def test_latin_text_has_no_spaceless_ngrams(self):
        self.assertEqual(_char_ngrams("Design a landing page"), frozenset())
        self.assertEqual(_char_ngrams(""), frozenset())
        self.assertEqual(_char_ngrams("ab"), frozenset())
        self.assertEqual(_char_ngrams("ก"), frozenset(), "too short for a bigram")
        self.assertEqual(_char_ngrams("กข"), frozenset({"กข"}))

    def test_ngrams_ignore_spaced_characters(self):
        mixed = _char_ngrams("ตรวจ เอกสาร PDF tables")
        spaced = _char_ngrams("ตรวจเอกสาร")
        self.assertTrue(mixed)
        self.assertTrue(spaced <= mixed)

    def test_dice_is_bounded_and_symmetric(self):
        left = _char_ngrams("เอกสารภาษาไทย")
        right = _char_ngrams("เอกสารภาษาไทย")
        self.assertEqual(_ngram_dice(left, right), 1.0)
        self.assertEqual(_ngram_dice(left, frozenset()), 0.0)
        self.assertEqual(_ngram_dice(left, _char_ngrams("ตั๋วเครื่องบิน")), 0.0)

    def test_reworded_thai_query_selects_the_thai_skill(self):
        data = _fixture(
            [
                {"name": "thai-docs", "description": "สร้างและตรวจ เอกสาร ภาษาไทย"},
                {"name": "unrelated", "description": "Book flights and hotels", "tags": ["travel"]},
            ],
            [
                {"query": "ช่วยเขียน เอกสาร ภาษาไทย", "expected": "thai-docs"},
                {"query": "สร้างเอกสารภาษาไทย", "expected": "thai-docs"},
                {"query": "ช่วยจองตั๋วเครื่องบินไปเชียงใหม่", "expected": None},
            ],
        )
        report = bench.evaluate(data)
        self.assertEqual(report["metrics"]["recall"], 1.0, bench.format_report(report))
        self.assertEqual(report["metrics"]["false_selections"], 0)

    def test_ngram_overlap_alone_can_select(self):
        with bench.fixture_catalog(
            _fixture([{"name": "thai-docs", "description": "เอกสาร ภาษาไทย"}], [])
        ) as catalog:
            selection = select_skill(catalog, "ช่วยเขียนเอกสารภาษาไทยให้หน่อย")
        self.assertIsNotNone(selection.selected)
        self.assertIn("spaceless_script_overlap", selection.selected.reasons)

    def test_unrelated_thai_query_still_abstains(self):
        with bench.fixture_catalog(
            _fixture([{"name": "thai-docs", "description": "สร้างและตรวจ เอกสาร ภาษาไทย"}], [])
        ) as catalog:
            selection = select_skill(catalog, "ช่วยจองตั๋วเครื่องบินไปเชียงใหม่")
        self.assertIsNone(selection.selected)
        self.assertEqual(selection.reason, "below_threshold")

    def test_spaced_script_catalogs_are_unaffected(self):
        with bench.fixture_catalog(
            _fixture([{"name": "python-tdd", "description": "Write Python tests first"}], [])
        ) as catalog:
            hit = select_skill(catalog, "write Python tests first")
            miss = select_skill(catalog, "จองตั๋วเครื่องบิน")
        self.assertIn("description_phrase", hit.selected.reasons)
        self.assertIsNone(miss.selected)


class MaterializeTests(unittest.TestCase):
    def test_aliases_and_tags_reach_the_catalog(self):
        data = _fixture(
            [
                {
                    "name": "slides",
                    "description": "Create strategic HTML presentations with charts",
                    "aliases": ["deck", "presentation"],
                    "tags": ["slides"],
                }
            ],
            [],
        )
        with bench.fixture_catalog(data) as catalog:
            self.assertEqual(len(catalog.skills), 1)
            record = catalog.skills[0]
            self.assertEqual(record.aliases, ("deck", "presentation"))
            self.assertEqual(record.tags, ("slides",))
            selection = select_skill(catalog, "prepare a deck for the board")
        self.assertIsNotNone(selection.selected)
        self.assertEqual(selection.selected.skill.name, "slides")
        self.assertIn("alias_phrase", selection.selected.reasons)

    def test_fixture_tree_is_throwaway(self):
        data = _fixture([{"name": "alpha", "description": "alpha beta gamma"}], [])
        with bench.fixture_catalog(data) as catalog:
            record = catalog.skills[0]
            self.assertTrue(pathlib.Path(record.source_path).is_file())
            throwaway = pathlib.Path(record.source_path).parent
        self.assertFalse(throwaway.exists())

    def test_repeated_runs_are_deterministic(self):
        first = bench.sweep(_METRIC_FIXTURE, scores=(0.28,), margins=(0.05,))
        second = bench.sweep(_METRIC_FIXTURE, scores=(0.28,), margins=(0.05,))
        self.assertEqual(first, second)

    def test_records_are_decoupled_from_the_throwaway_tree(self):
        report = bench.evaluate(_METRIC_FIXTURE)
        # The fixture tree is deleted before the report is returned: nothing in
        # the report may depend on files still existing on disk.
        for record in report["records"]:
            for candidate in record["candidates"]:
                self.assertTrue(candidate["name"])

    def test_fixture_data_is_never_mutated(self):
        snapshot = json.loads(json.dumps(_METRIC_FIXTURE, ensure_ascii=False))
        bench.evaluate(_METRIC_FIXTURE)
        self.assertEqual(_METRIC_FIXTURE, snapshot)
        self.assertTrue(core.__version__)


if __name__ == "__main__":
    unittest.main()
