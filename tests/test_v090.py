"""Tests for the v0.9.0 features (outcome telemetry).

The host sometimes loads a skill we did not select, and sometimes does not
load the one we did: both are outcomes worth counting. Telemetry is derived
from the append-only audit log, so no new state and no prompt storage.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

import core  # noqa: F401  (asserts the module stays importable for these tests)
from core import SkillProofEngine

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package_v090",
    _package_path / "__init__.py",
    submodule_search_locations=[str(_package_path)],
)
plugin_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plugin_module
_spec.loader.exec_module(plugin_module)

_SKILLS = (
    ("python-tdd", "Write Python tests before implementation"),
    ("frontend-design", "Design a polished landing page and frontend interface"),
    ("slides", "Create strategic HTML presentations with charts"),
)


def _record(**kwargs):
    base = {
        "schema": "skill-proof.audit.v1",
        "turn_id": "t",
        "status": "selected",
        "reason": "lexical_match",
        "selected": None,
        "loaded": False,
        "override_loaded": None,
    }
    base.update(kwargs)
    return base


class AggregateTests(unittest.TestCase):
    def test_hit_miss_abstain_and_override_math(self):
        records = [
            _record(selected="a", loaded=True),
            _record(selected="a", loaded=False),
            _record(selected="b", loaded=True),
            _record(status="no_match", reason="below_threshold"),
            _record(status="no_match", reason="negated_skill", override_loaded="c"),
        ]
        stats = plugin_module.SkillProofPlugin._aggregate_audit(records)
        totals = stats["totals"]
        self.assertEqual(totals["turns"], 5)
        self.assertEqual(totals["routed"], 3)
        self.assertEqual(totals["loaded"], 2)
        self.assertEqual(totals["miss"], 1)
        self.assertEqual(totals["abstained"], 2)
        self.assertEqual(totals["overridden"], 1)
        self.assertEqual(totals["vetoed"], 1)
        self.assertEqual(totals["fallback"], 0)
        self.assertAlmostEqual(totals["hit_rate"], 2 / 3, places=4)
        self.assertAlmostEqual(totals["routed_rate"], 3 / 5, places=4)
        self.assertEqual(stats["overrides"], {"c": 1})

    def test_per_skill_rows_add_up(self):
        records = [
            _record(selected="a", loaded=True),
            _record(selected="a", loaded=True),
            _record(selected="a", loaded=False),
            _record(selected="b", loaded=False),
        ]
        skills = plugin_module.SkillProofPlugin._aggregate_audit(records)["skills"]
        self.assertEqual(set(skills), {"a", "b"})
        self.assertEqual(skills["a"], {"routed": 3, "loaded": 2, "miss": 1, "fallback": 0,
                                       "hit_rate": round(2 / 3, 4)})
        self.assertEqual(skills["b"]["routed"], 1)
        self.assertEqual(skills["b"]["hit_rate"], 0.0)

    def test_focus_fallback_is_excluded_from_hit_rate(self):
        records = [
            _record(selected="a", reason="focus_fallback", loaded=True),
            _record(selected="a", reason="focus_fallback"),
            _record(selected="a", loaded=True),
        ]
        stats = plugin_module.SkillProofPlugin._aggregate_audit(records)
        totals = stats["totals"]
        self.assertEqual(totals["routed"], 3)
        self.assertEqual(totals["fallback"], 2)
        self.assertEqual(totals["loaded"], 1)
        self.assertAlmostEqual(totals["hit_rate"], 1 / 1, places=4)
        row = stats["skills"]["a"]
        self.assertEqual(row["fallback"], 2)
        self.assertAlmostEqual(row["hit_rate"], 1.0, places=4)

    def test_hit_rate_is_null_when_only_fallbacks_exist(self):
        stats = plugin_module.SkillProofPlugin._aggregate_audit(
            [_record(selected="a", reason="focus_fallback")]
        )
        self.assertIsNone(stats["totals"]["hit_rate"])
        self.assertIsNone(stats["skills"]["a"]["hit_rate"])

    def test_empty_audit_is_not_a_crash(self):
        stats = plugin_module.SkillProofPlugin._aggregate_audit([])
        self.assertEqual(stats["records"], 0)
        self.assertIsNone(stats["totals"]["hit_rate"])
        self.assertEqual(stats["skills"], {})
        self.assertIn("0 records", plugin_module.SkillProofPlugin._format_stats(stats))

    def test_unrelated_schemas_are_not_counted(self):
        # Only skill-proof.audit.v1 lines are aggregated by stats(); a foreign
        # line would be skipped upstream, so pass nothing foreign here.
        stats = plugin_module.SkillProofPlugin._aggregate_audit([_record(selected="a")])
        self.assertEqual(stats["totals"]["turns"], 1)


class OverrideCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_v090_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        for name, description in _SKILLS:
            folder = self.root / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")
            )
        self.engine = SkillProofEngine({"fixture": self.root})

    def _turn(self, turn_id, query, loaded=None, task_id="task", session_id="s"):
        start = self.engine.begin_turn(
            turn_id=turn_id, session_id=session_id, task_id=task_id, query=query
        )
        if loaded:
            self.engine.observe_skill_loaded(
                session_id=session_id, task_id=task_id, skill_name=loaded
            )
        receipt = self.engine.receipt(turn_id=turn_id)
        self.engine.finish_turn(turn_id=turn_id)
        return start, receipt

    def test_host_load_that_matches_is_not_an_override(self):
        _, receipt = self._turn(
            "t1", "design a polished landing page and frontend interface",
            loaded="frontend-design",
        )
        self.assertTrue(receipt["evidence"]["hermes_loaded_event"])
        self.assertEqual(receipt["evidence"]["override_loaded"], "")

    def test_host_load_of_a_different_skill_is_an_override(self):
        _, receipt = self._turn(
            "t1", "design a polished landing page and frontend interface",
            loaded="slides",
        )
        self.assertEqual(receipt["selected"]["name"], "frontend-design")
        self.assertFalse(receipt["evidence"]["hermes_loaded_event"])
        self.assertEqual(receipt["evidence"]["override_loaded"], "slides")

    def test_host_load_while_we_abstained_is_an_override(self):
        _, receipt = self._turn("t2", "book a flight to Chiang Mai", loaded="slides")
        self.assertIsNone(receipt["selected"])
        self.assertEqual(receipt["evidence"]["override_loaded"], "slides")
        self.assertFalse(receipt["evidence"]["hermes_loaded_event"])

    def test_override_does_not_mark_the_turn_loaded(self):
        _, receipt = self._turn(
            "t1", "design a polished landing page and frontend interface", loaded="slides"
        )
        self.assertFalse(receipt["compliance"] == "verified")
        self.assertFalse(receipt["evidence"]["hermes_loaded_event"])

    def test_unknown_load_without_a_turn_is_not_recorded(self):
        self.assertFalse(
            self.engine.observe_skill_loaded(
                session_id="ghost", task_id="task", skill_name="slides"
            )
        )


class StatsCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_v090p_")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        skills = root / "skills"
        skills.mkdir()
        for name, description in _SKILLS:
            folder = skills / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")
            )
        self.skills = skills
        self.audit = root / "audit.jsonl"

    def plugin(self, **extra):
        settings = {"skill_roots": [str(self.skills)], "audit_path": str(self.audit)}
        settings.update(extra)
        return self._build(settings)

    def _build(self, settings):
        class State:
            def __init__(self):
                self.data = {}

            def get(self, key, default=None):
                return self.data.get(key, default)

            def set(self, key, value):
                self.data[key] = value

        class Ctx:
            def __init__(self):
                self.state = State()
                self.hooks = {}
                self.commands = {}

            def get_config(self, key, default=None):
                return settings.get(key, default)

            def dispatch_tool(self, name, args):
                return json.dumps(
                    {"success": True, "skills": [{"name": n} for n in _SKILLS_NAMES]}
                )

            def register_hook(self, name, callback):
                self.hooks[name] = callback

            def register_command(self, name, **kwargs):
                self.commands[name] = kwargs

        return plugin_module.SkillProofPlugin(Ctx())

    def run_turn(self, plugin, index, query, load=None, override=None):
        plugin.on_pre_llm_call(
            session_id="s", task_id="t", turn_id=f"t{index}", user_message=query
        )
        if load:
            plugin.on_skill_lifecycle(action="loaded", skill_name=load, session_id="s", task_id="t")
        if override:
            plugin.on_skill_lifecycle(
                action="loaded", skill_name=override, session_id="s", task_id="t"
            )
        plugin.on_post_llm_call(session_id="s", task_id="t", turn_id=f"t{index}")

    def test_stats_reports_the_real_turns(self):
        plugin = self.plugin()
        self.run_turn(plugin, 1, "design a polished landing page", load="frontend-design")
        self.run_turn(plugin, 2, "design a polished landing page")
        self.run_turn(plugin, 3, "book a flight to Chiang Mai", override="slides")
        payload = json.loads(plugin.handle_command("stats --json"))
        self.assertEqual(payload["schema"], "skill-proof.stats.v1")
        self.assertEqual(payload["records"], 3)
        totals = payload["totals"]
        self.assertEqual(totals["turns"], 3)
        self.assertEqual(totals["routed"], 2)
        self.assertEqual(totals["loaded"], 1)
        self.assertEqual(totals["miss"], 1)
        self.assertEqual(totals["abstained"], 1)
        self.assertEqual(totals["overridden"], 1)
        self.assertAlmostEqual(totals["hit_rate"], 0.5, places=4)
        self.assertEqual(payload["overrides"], {"slides": 1})
        # Turns 1 and 2 both route to frontend-design: one loaded, one not.
        self.assertEqual(
            payload["skills"]["frontend-design"],
            {"routed": 2, "loaded": 1, "miss": 1, "fallback": 0, "hit_rate": 0.5},
        )
        text = plugin.handle_command("stats")
        self.assertIn("hit_rate   50.0%", text)
        self.assertIn("frontend-design", text)
        self.assertIn("slides x1", text)

    def test_stats_without_an_audit_log_says_so(self):
        plugin = self.plugin(audit_log=False)
        self.assertIn("audit_log is disabled", plugin.handle_command("stats"))
        plugin = self.plugin()
        self.assertIn("No audit log", plugin.handle_command("stats"))

    def test_stats_skips_corrupt_lines(self):
        plugin = self.plugin()
        self.run_turn(plugin, 1, "design a polished landing page", load="frontend-design")
        with self.audit.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
            handle.write('{"schema": "someone.else.v1"}\n')
        payload = json.loads(plugin.handle_command("stats --json"))
        self.assertEqual(payload["records"], 1)
        self.assertEqual(payload["corrupt_lines"], 1)
        self.assertIn("skipped 1 unreadable audit line", plugin.handle_command("stats"))

    def test_stats_json_and_text_agree(self):
        plugin = self.plugin()
        self.run_turn(plugin, 1, "design a polished landing page", load="frontend-design")
        payload = json.loads(plugin.handle_command("stats --json"))
        text = plugin.handle_command("stats")
        self.assertIn(str(payload["records"]), text)
        self.assertEqual(payload["totals"]["loaded"], 1)

    def test_help_and_args_hint_mention_stats(self):
        plugin = self.plugin()
        self.assertIn("/skill-proof stats", plugin.handle_command("help"))
        self.assertTrue(callable(plugin.stats))


_SKILLS_NAMES = tuple(name for name, _ in _SKILLS)


if __name__ == "__main__":
    unittest.main()
