"""Tests for the v0.10.0 features (overlap and pruning report).

Near-duplicate detection over the catalog, exposed through the CLI and
``/skill-proof overlap``, with usage from the audit log breaking the tie on
which side to drop.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

import cli
import core
from core import overlap_report, scan_catalog

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package_v0100",
    _package_path / "__init__.py",
    submodule_search_locations=[str(_package_path)],
)
plugin_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plugin_module
_spec.loader.exec_module(plugin_module)

_ALPHA = "Write Python tests before implementation"


def _skill_bytes(name, description):
    return f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")


def _write_skill(folder, name, description):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_bytes(_skill_bytes(name, description))


class OverlapCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_overlap_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "root"
        _write_skill(self.root / "alpha-a", "alpha-a", _ALPHA)
        _write_skill(self.root / "alpha-b", "alpha-b", _ALPHA)
        _write_skill(self.root / "solo", "solo", "Design a polished landing page and frontend interface")
        self.catalog = scan_catalog({"fixture": self.root})

    def test_identical_descriptions_are_reported(self):
        report = overlap_report(self.catalog, min_similarity=0.4)
        self.assertEqual(report["total_found"], 1)
        row = report["pairs"][0]
        self.assertEqual({row["a"], row["b"]}, {"alpha-a", "alpha-b"})
        self.assertEqual(row["similarity"], 1.0)
        self.assertIn("identical description", row["recommendation"])
        self.assertIn("write", row["shared"])
        self.assertEqual(report["skills"], 3)

    def test_similarity_threshold_filters(self):
        strict = overlap_report(self.catalog, min_similarity=1.0)
        self.assertEqual(strict["total_found"], 1)
        wide = overlap_report(self.catalog, min_similarity=0.0)
        self.assertGreaterEqual(wide["total_found"], 1)
        for row in wide["pairs"]:
            self.assertGreaterEqual(row["similarity"], 0.0)

    def test_unrelated_skills_are_not_reported(self):
        names = set()
        for row in overlap_report(self.catalog, min_similarity=0.0)["pairs"]:
            names.update((row["a"], row["b"]))
        self.assertNotIn("solo", names)

    def test_pairs_checked_covers_the_catalog(self):
        report = overlap_report(self.catalog, min_similarity=0.4)
        self.assertEqual(report["pairs_checked"], 3)  # 3 skills -> 3 pairs

    def test_rows_are_sorted_and_limited(self):
        report = overlap_report(self.catalog, min_similarity=0.0, limit=1)
        self.assertEqual(len(report["pairs"]), 1)
        self.assertEqual(report["limit"], 1)
        self.assertEqual(report["total_found"], 1)

    def test_truncated_flag(self):
        _write_skill(self.root / "alpha-c", "alpha-c", _ALPHA)
        catalog = scan_catalog({"fixture": self.root})
        report = overlap_report(catalog, min_similarity=0.4, limit=1)
        self.assertGreater(report["total_found"], 1)
        self.assertTrue(report["truncated"])
        self.assertEqual(len(report["pairs"]), 1)

    def test_report_is_deterministic(self):
        first = overlap_report(self.catalog, min_similarity=0.4)
        second = overlap_report(self.catalog, min_similarity=0.4)
        self.assertEqual(first, second)

    def test_invalid_arguments_are_rejected(self):
        for bad in (-0.1, 1.5, "x"):
            with self.subTest(min_similarity=bad), self.assertRaises(ValueError):
                overlap_report(self.catalog, min_similarity=bad)
        for bad in (0, -1, True, "5"):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                overlap_report(self.catalog, limit=bad)

    def test_exact_copies_collapse_before_overlap_sees_them(self):
        # Byte-identical copies across two roots: the catalog collapses them,
        # so the pair must never be reported as a near-duplicate.
        other = pathlib.Path(self.tmp.name) / "other"
        _write_skill(other / "alpha-a", "alpha-a", _ALPHA)
        catalog = scan_catalog({"fixture": self.root, "other": other})
        report = overlap_report(catalog, min_similarity=0.4)
        self.assertEqual(report["exact_copies"], 1)
        for row in report["pairs"]:
            self.assertLessEqual([row["a"], row["b"]].count("alpha-a"), 1)

    def test_engine_overlap_uses_its_roots(self):
        engine = core.SkillProofEngine({"fixture": self.root})
        report = engine.overlap(min_similarity=0.4)
        self.assertEqual(report["skills"], 3)
        self.assertEqual(report["total_found"], 1)


class OverlapCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_overlap_cli_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "root"
        _write_skill(self.root / "alpha-a", "alpha-a", _ALPHA)
        _write_skill(self.root / "alpha-b", "alpha-b", _ALPHA)

    def test_text_output_names_both_sides(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["overlap", "--root", str(self.root)])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("alpha-a", output)
        self.assertIn("alpha-b", output)
        self.assertIn("identical description", output)
        self.assertIn("exact copies already collapsed: 0", output)

    def test_json_output_is_machine_readable(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["overlap", "--root", str(self.root), "--json"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["version"], core.__version__)
        self.assertEqual(payload["pairs"][0]["similarity"], 1.0)
        self.assertIn("recommendation", payload["pairs"][0])

    def test_invalid_threshold_exits_with_two(self):
        import contextlib
        import io

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["overlap", "--root", str(self.root), "--min-similarity", "9"])
        self.assertEqual(code, 2)
        self.assertIn("min_similarity must be between 0 and 1", stderr.getvalue())


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class PluginOverlapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_overlap_p_")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        skills = root / "skills"
        _write_skill(skills / "alpha-a", "alpha-a", _ALPHA)
        _write_skill(skills / "alpha-b", "alpha-b", _ALPHA)
        _write_skill(skills / "solo", "solo", "Design a polished landing page and frontend interface")
        self.skills = skills
        self.audit = root / "audit.jsonl"
        self.plugin = self._build(
            {"skill_roots": [str(skills)], "audit_path": str(self.audit)}
        )

    def _build(self, settings):
        class Ctx:
            def __init__(self):
                self.state = FakeState()
                self.hooks = {}
                self.commands = {}

            def get_config(self, key, default=None):
                return settings.get(key, default)

            def dispatch_tool(self, name, args):
                return json.dumps(
                    {"success": True, "skills": [{"name": n} for n in ("alpha-a", "alpha-b", "solo")]}
                )

            def register_hook(self, name, callback):
                self.hooks[name] = callback

            def register_command(self, name, **kwargs):
                self.commands[name] = kwargs

        return plugin_module.SkillProofPlugin(Ctx())

    def _turn(self, index, query, load=None):
        plugin = self.plugin
        plugin.on_pre_llm_call(
            session_id="s", task_id="t", turn_id=f"t{index}", user_message=query
        )
        if load:
            plugin.on_skill_lifecycle(action="loaded", skill_name=load, session_id="s", task_id="t")
        plugin.on_post_llm_call(session_id="s", task_id="t", turn_id=f"t{index}")

    def test_overlap_reports_pairs_and_drop_candidate_from_usage(self):
        self._turn(1, "$alpha-a", load="alpha-a")   # used once
        self._turn(2, "$alpha-b")                    # never loaded
        output = self.plugin.handle_command("overlap")
        self.assertIn("alpha-a", output)
        self.assertIn("alpha-b", output)
        self.assertIn("usage: alpha-a loaded 1x, alpha-b loaded 0x", output)
        self.assertIn("consider dropping alpha-b", output)

    def test_overlap_without_audit_says_usage_is_unavailable(self):
        self.plugin.audit_enabled = False
        output = self.plugin.handle_command("overlap")
        self.assertIn("alpha-a", output)
        self.assertIn("No audit hits available yet", output)

    def test_overlap_json_and_options(self):
        payload = json.loads(self.plugin.handle_command("overlap --json --limit 5"))
        self.assertEqual(payload["version"], core.__version__)
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["total_found"], 1)
        row = payload["pairs"][0]
        self.assertEqual(row["similarity"], 1.0)
        self.assertIn("drop_candidate", row)

    def test_overlap_threshold_option(self):
        payload = json.loads(
            self.plugin.handle_command("overlap --min-similarity 1.0 --json")
        )
        self.assertEqual(payload["min_similarity"], 1.0)
        self.assertEqual(payload["total_found"], 1)

    def test_overlap_rejects_bad_options(self):
        output = self.plugin.handle_command("overlap --min-similarity nope")
        self.assertIn("Usage: /skill-proof overlap", output)
        output = self.plugin.handle_command("overlap --min-similarity 5")
        self.assertIn("min_similarity must be between 0 and 1", output)

    def test_help_lists_overlap(self):
        self.assertIn("/skill-proof overlap", self.plugin.handle_command("help"))


if __name__ == "__main__":
    unittest.main()
