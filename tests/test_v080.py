"""Tests for the v0.8.0 features.

``/skill-proof why <skill>`` (an exact per-skill explanation built only from
stored derived numbers) and the append-only JSONL decision audit (never
prompts, never skill bodies).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

import core
from core import SkillProofEngine

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package_v080",
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

PROMPT = "don't use python-tdd, design a polished landing page for the launch"


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_why_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        for name, description in _SKILLS:
            folder = self.root / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")
            )
        self.engine = SkillProofEngine({"fixture": self.root})
        self.engine.begin_turn(turn_id="t1", session_id="s", task_id="task", query=PROMPT)
        self.engine.finish_turn(turn_id="t1")

    def test_no_turn_reported_honestly(self):
        report = SkillProofEngine({"fixture": self.root}).explain("python-tdd")
        self.assertEqual(report, {"available": False, "reason": "no_turn_observed"})

    def test_report_holds_thresholds_and_decision(self):
        report = self.engine.explain(turn_id="t1")
        self.assertTrue(report["available"])
        self.assertEqual(report["decision"]["status"], "no_match")
        self.assertEqual(report["decision"]["reason"], "negated_skill")
        self.assertEqual(report["thresholds"]["min_score"], 0.28)
        self.assertEqual(report["thresholds"]["min_margin"], 0.05)
        self.assertEqual(report["catalog_size"], len(_SKILLS))

    def test_vetoed_skill_is_reported_as_vetoed(self):
        report = self.engine.explain("python-tdd", turn_id="t1")
        skill = report["skill"]
        self.assertEqual(skill["verdict"], "vetoed")
        self.assertIsNone(skill["score"])
        self.assertIn("negated_in_query", skill["reasons"])
        self.assertIn("does not store prompts", skill["note"])
        self.assertIn("python-tdd", report["vetoed"])

    def test_ranked_but_below_threshold_skill(self):
        report = self.engine.explain("frontend-design", turn_id="t1")
        skill = report["skill"]
        self.assertEqual(skill["verdict"], "below_threshold")
        self.assertEqual(skill["name"], "frontend-design")
        self.assertEqual(skill["rank"], 1)
        self.assertEqual(skill["threshold"], 0.28)
        self.assertFalse(skill["threshold_met"])
        self.assertGreater(skill["score"], 0.0)
        self.assertLess(skill["score"], 0.28)
        self.assertEqual(skill["gap_to_top"], 0.0)
        self.assertIn("description_terms", skill["reasons"])

    def test_skill_with_no_signal_at_all(self):
        report = self.engine.explain("slides", turn_id="t1")
        skill = report["skill"]
        self.assertEqual(skill["verdict"], "no_signal")
        self.assertEqual(skill["score"], 0.0)
        self.assertFalse(skill["score_is_ceiling"])
        self.assertTrue(skill["in_catalog"])

    def test_unknown_skill_is_named_as_unknown(self):
        report = self.engine.explain("ghost-skill", turn_id="t1")
        skill = report["skill"]
        self.assertEqual(skill["verdict"], "unknown_skill")
        self.assertFalse(skill["in_catalog"])

    def test_explicit_turn_reports_not_ranked(self):
        self.engine.begin_turn(turn_id="t2", session_id="s", task_id="task", query="$slides")
        self.engine.finish_turn(turn_id="t2")
        report = self.engine.explain("python-tdd", turn_id="t2")
        self.assertEqual(report["skill"]["verdict"], "not_ranked")
        self.assertEqual(report["ranked_count"], 0)
        self.assertIn("explicit_skill", report["skill"]["note"])

    def test_explain_never_returns_the_prompt(self):
        for name in ("", "python-tdd", "frontend-design", "slides", "ghost-skill"):
            payload = json.dumps(self.engine.explain(name, turn_id="t1"), ensure_ascii=False)
            self.assertNotIn("landing page", payload)
            self.assertNotIn("don't use", payload)

    def test_report_exposes_the_rank_table(self):
        report = self.engine.explain(turn_id="t1")
        names = [row["name"] for row in report["rank_table"]]
        self.assertIn("frontend-design", names)
        self.assertNotIn("python-tdd", names, "a vetoed skill has no rank row")
        for row in report["rank_table"]:
            self.assertGreater(row["score"], 0.0)
            self.assertTrue(row["reasons"])

    def test_rank_table_is_bounded(self):
        strong = "Design a polished landing page and frontend interface"
        weak = "frontend interface for design work"
        for index in range(21):
            folder = self.root / f"strong-{index}"
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: strong-{index}\ndescription: {strong}\n---\nBody.\n".encode("utf-8")
            )
        for index in range(5):
            folder = self.root / f"weak-{index}"
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: weak-{index}\ndescription: {weak}\n---\nBody.\n".encode("utf-8")
            )
        self.engine.refresh_catalog()
        self.engine.begin_turn(
            turn_id="t3", session_id="s", task_id="task",
            query="design a polished landing page and frontend interface",
        )
        report = self.engine.explain("weak-0", turn_id="t3")
        self.assertEqual(len(report["rank_table"]), core._RANK_TABLE_LIMIT)
        self.assertEqual(report["skill"]["verdict"], "ranked_below_cap")
        self.assertTrue(report["skill"]["score_is_ceiling"])
        self.assertTrue(report["skill"]["in_catalog"])
        self.assertGreater(report["skill"]["score"], 0.0)
        self.engine.finish_turn(turn_id="t3")

    def test_host_listing_exclusion_is_named_separately(self):
        # On disk under a configured root, but this turn's host listing skipped it.
        self.engine.begin_turn(
            turn_id="t4", session_id="s", task_id="task",
            query="design a polished landing page", available_names={"slides"},
        )
        report = self.engine.explain("frontend-design", turn_id="t4")
        skill = report["skill"]
        self.assertEqual(skill["verdict"], "not_listed_by_host")
        self.assertTrue(skill["indexed"])
        self.assertFalse(skill["in_catalog"])
        self.assertIn("host's", skill["note"])
        self.engine.finish_turn(turn_id="t4")

    def test_host_listing_exclusion_still_reports_unknown_for_absent_names(self):
        self.engine.begin_turn(
            turn_id="t5", session_id="s", task_id="task",
            query="design a polished landing page", available_names={"slides"},
        )
        report = self.engine.explain("never-existed", turn_id="t5")
        self.assertEqual(report["skill"]["verdict"], "unknown_skill")
        self.assertFalse(report["skill"]["indexed"])
        self.engine.finish_turn(turn_id="t5")

    def test_explain_finds_the_turn_by_session(self):
        report = self.engine.explain("frontend-design", session_id="s")
        self.assertEqual(report["turn_id"], "t1")
        self.assertEqual(report["skill"]["verdict"], "below_threshold")


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeContext:
    def __init__(self, settings=None, names=("python-tdd", "frontend-design", "slides")):
        self.settings = settings or {}
        self.names = list(names)
        self.hooks = {}
        self.commands = {}
        self.state = FakeState()

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def dispatch_tool(self, name, args):
        assert name == "skills_list"
        return json.dumps({"success": True, "skills": [{"name": n} for n in self.names]})

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, **kwargs):
        self.commands[name] = kwargs


class PluginHarness(unittest.TestCase):
    """Shared fixture: three fixture skills, an audit file, a driver."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_v080_")
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
        self.audit = root / "audit" / "audit.jsonl"

    def plugin(self, **extra):
        settings = {"skill_roots": [str(self.skills)], "audit_path": str(self.audit)}
        settings.update(extra)
        context = FakeContext(settings)
        return plugin_module.SkillProofPlugin(context), context

    def run_turn(self, plugin, index, query, session_id="s"):
        plugin.on_pre_llm_call(
            session_id=session_id, task_id="t", turn_id=f"t{index}", user_message=query
        )
        plugin.on_post_llm_call(session_id=session_id, task_id="t", turn_id=f"t{index}")


class WhyCommandTests(PluginHarness):
    def test_why_formats_selected_vetoed_and_unknown(self):
        plugin, _ = self.plugin()
        self.run_turn(plugin, 1, PROMPT)
        vetoed = plugin.handle_command("why python-tdd")
        self.assertIn("vetoed by this turn's wording", vetoed)
        self.assertIn("Vetoed this turn: python-tdd", vetoed)
        below = plugin.handle_command("why frontend-design")
        self.assertIn("ranked #1 at", below)
        self.assertIn("threshold_met=False", below)
        self.assertIn("(threshold 0.28)", below)
        self.assertIn("matched on name_terms,description_terms", below)
        self.assertIn("Top ranked this turn:", below)
        self.assertIn("never stores prompts", below)
        unknown = plugin.handle_command("why ghost-skill")
        self.assertIn("no indexed SKILL.md", unknown)
        none = plugin.handle_command("why slides")
        self.assertIn("No term in this turn overlapped", none)

    def test_why_without_argument_shows_usage(self):
        plugin, _ = self.plugin()
        output = plugin.handle_command("why")
        self.assertIn("Usage: /skill-proof why <skill name>", output)

    def test_why_without_a_live_turn_says_so(self):
        plugin, _ = self.plugin()
        self.assertIn("No live Skill Proof turn", plugin.handle_command("why slides"))

    def test_why_of_a_selected_skill(self):
        plugin, _ = self.plugin()
        self.run_turn(plugin, 1, "design a polished landing page and frontend interface")
        output = plugin.handle_command("why frontend-design")
        self.assertIn("Selected: frontend-design", output)
        self.assertIn("-> selected: score=", output)

    def test_other_commands_survive_argument_splitting(self):
        plugin, _ = self.plugin()
        self.run_turn(plugin, 1, "design a polished landing page and frontend interface")
        self.assertTrue(plugin.handle_command("status").startswith("Skill Proof:"))
        self.assertIn("Decision:", plugin.handle_command("explain"))
        self.assertIn("turn_id", plugin.handle_command("trace"))
        self.assertIn("cache cleared", plugin.handle_command("refresh").lower())
        health = json.loads(plugin.handle_command("health"))
        self.assertIn("audit", health)
        self.assertIn("version", health)
        self.assertIn("Unknown subcommand", plugin.handle_command("bogus"))
        self.assertIn("/skill-proof status", plugin.handle_command("help"))


class AuditLogTests(PluginHarness):
    def records(self):
        return [
            json.loads(line)
            for line in self.audit.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_one_line_per_turn_with_derived_numbers(self):
        plugin, _ = self.plugin()
        self.run_turn(plugin, 1, PROMPT)
        self.run_turn(plugin, 2, "design a polished landing page and frontend interface")
        records = self.records()
        self.assertEqual(len(records), 2)
        first, second = records
        self.assertEqual(first["schema"], "skill-proof.audit.v1")
        self.assertEqual(first["turn_id"], "t1")
        self.assertEqual(first["status"], "no_match")
        self.assertEqual(first["reason"], "negated_skill")
        self.assertIsNone(first["selected"])
        self.assertEqual(second["status"], "selected")
        self.assertEqual(second["selected"], "frontend-design")
        self.assertEqual(second["reason"], "lexical_match")
        self.assertTrue(second["reasons"])
        self.assertEqual(second["catalog_size"], len(_SKILLS))
        self.assertIsNotNone(second["total_ms"])
        self.assertTrue(first["session_sha256"])
        self.assertNotEqual(first["session_sha256"], "s", "session ids are hashed")

    def test_audit_never_contains_the_prompt(self):
        plugin, _ = self.plugin()
        self.run_turn(plugin, 1, PROMPT)
        self.run_turn(plugin, 2, "ช่วยสร้าง landing page ภาษาไทยให้หน่อย")
        text = self.audit.read_text(encoding="utf-8")
        for fragment in ("landing page", "don't use", "ช่วยสร้าง", "ภาษาไทย"):
            self.assertNotIn(fragment, text)
        for record in self.records():
            self.assertNotIn("query", record, "only query_sha256 is recorded")

    def test_audit_can_be_disabled(self):
        plugin, _ = self.plugin(audit_log=False)
        self.run_turn(plugin, 1, PROMPT)
        self.assertFalse(self.audit.exists())
        self.assertEqual(plugin._audit_tail(), [])

    def test_rotation_keeps_the_configured_limit(self):
        plugin, _ = self.plugin(audit_limit=10)
        for index in range(1, 4):
            self.run_turn(plugin, index, PROMPT)
        # Force the periodic check: it runs every 50 appends.
        plugin._audit_writes = 49
        self.run_turn(plugin, 4, PROMPT)
        records = self.records()
        self.assertLessEqual(len(records), 10)

    def test_rotate_audit_directly(self):
        plugin, _ = self.plugin(audit_limit=10)
        for index in range(1, 16):
            self.run_turn(plugin, index, PROMPT)
        plugin._rotate_audit()
        self.assertEqual(len(self.records()), 10)
        kept = self.records()
        self.assertEqual(kept[-1]["turn_id"], "t15")

    def test_broken_audit_path_never_breaks_a_turn(self):
        blocker = pathlib.Path(self.audit.parent)  # parent will become a file
        blocker.parent.mkdir(parents=True, exist_ok=True)
        if blocker.exists() and blocker.is_dir():
            blocker.rmdir()
        blocker.write_text("not a directory", encoding="utf-8")
        plugin, _ = self.plugin()
        self.run_turn(plugin, 1, PROMPT)
        self.assertTrue(plugin._audit_broken)
        self.assertIn("Skill Proof:", plugin.handle_command("status"))
        self.run_turn(plugin, 2, PROMPT)
        self.assertTrue(plugin._audit_broken, "it stops retrying quietly")

    def test_audit_tail_reads_recent_records(self):
        plugin, _ = self.plugin()
        for index in range(1, 4):
            self.run_turn(plugin, index, PROMPT)
        tail = plugin._audit_tail(2)
        self.assertEqual(len(tail), 2)
        self.assertEqual(tail[-1]["turn_id"], "t3")

    def test_health_reports_audit_configuration(self):
        plugin, _ = self.plugin(audit_limit=42)
        self.run_turn(plugin, 1, PROMPT)
        health = json.loads(plugin.handle_command("health"))
        self.assertTrue(health["audit"]["enabled"])
        self.assertEqual(health["audit"]["limit"], 42)
        self.assertEqual(str(health["audit"]["path"]), str(self.audit))
        self.assertFalse(health["audit"]["broken"])
        self.assertEqual(len(health["audit"]["recent"]), 1)

    def test_invalid_audit_settings_fall_back_to_defaults(self):
        plugin, _ = self.plugin(audit_log="no", audit_limit="lots")
        self.assertTrue(plugin.audit_enabled)
        self.assertEqual(plugin.audit_limit, 500)


if __name__ == "__main__":
    unittest.main()
