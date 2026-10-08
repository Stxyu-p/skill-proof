"""Tests for the v0.7.0 features.

Session skill history, dialogue references (``use the same skill``,
``อันที่แล้ว``, ``keep using X``, ``stop using X``), bounded focus with decay
and expiry, and the plugin plumbing that carries them.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

import core
from core import SkillProofEngine, parse_dialogue_reference

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package_v070",
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


class SessionHarness:
    """Fixture catalog plus a driver that runs turns and keeps receipts."""

    def __init__(self, skills=None, **engine_kwargs):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_session_")
        self.root = pathlib.Path(self.tmp.name)
        for name, description in (skills or _SKILLS):
            folder = self.root / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")
            )
        self.engine = SkillProofEngine({"fixture": self.root}, **engine_kwargs)
        self.counter = 0

    def close(self):
        self.tmp.cleanup()

    def turn(self, query, *, session_id="s1"):
        self.counter += 1
        turn_id = f"turn-{self.counter}"
        start = self.engine.begin_turn(
            turn_id=turn_id, session_id=session_id, task_id="task", query=query
        )
        receipt = self.engine.receipt(turn_id=turn_id)
        self.engine.finish_turn(turn_id=turn_id)
        return receipt, start.context

    def select(self, query, *, session_id="s1"):
        receipt, _ = self.turn(query, session_id=session_id)
        selected = receipt.get("selected") or {}
        return (
            selected.get("name"),
            receipt["decision"]["reason"],
            receipt["evidence"].get("session", {}),
        )


class DialogueParserTests(unittest.TestCase):
    def test_repeat_phrases(self):
        for query in (
            "use the same skill",
            "the same one",
            "same as before",
            "do the same",
            "เหมือนเดิม",
            "ตัวเดิม",
            "ใช้ตัวเดิม",
        ):
            with self.subTest(query=query):
                reference = parse_dialogue_reference(query)
                self.assertIsNotNone(reference)
                self.assertEqual(reference.kind, "repeat")

    def test_previous_phrases(self):
        for query in ("the previous skill", "the one before", "อันที่แล้ว", "ตัวที่แล้ว", "ครั้งก่อน"):
            with self.subTest(query=query):
                reference = parse_dialogue_reference(query)
                self.assertIsNotNone(reference)
                self.assertEqual(reference.kind, "previous")

    def test_focus_and_release_phrases(self):
        for query in ("keep using pdf-extraction", "stick with slides", "ใช้ต่อไป", "ทำต่อด้วย"):
            with self.subTest(query=query):
                reference = parse_dialogue_reference(query, known_names=("slides", "pdf-extraction"))
                self.assertIsNotNone(reference)
                self.assertEqual(reference.kind, "focus")
        # เลิกทำ and ทำต่อด้วย contain SARA AM, which NFKC decomposes; the phrase
        # side must be normalized identically or the match silently disappears.
        for query in ("stop using slides", "unfocus", "เลิกใช้ slides", "เลิกทำ"):
            with self.subTest(query=query):
                reference = parse_dialogue_reference(query, known_names=("slides",))
                self.assertIsNotNone(reference)
                self.assertEqual(reference.kind, "release")

    def test_plain_task_sentences_are_not_references(self):
        for query in (
            "run the build again",
            "do that again",
            "api-contracts คืออะไร",
            "ใช้ html ซ้ำ",
            "the previous release notes",
        ):
            with self.subTest(query=query):
                self.assertIsNone(parse_dialogue_reference(query))

    def test_reference_captures_the_named_skill(self):
        reference = parse_dialogue_reference(
            "keep using pdf-extraction", known_names=("pdf-extraction", "slides")
        )
        self.assertEqual(reference.name, "pdf-extraction")

    def test_unknown_skill_name_is_still_captured(self):
        # frontend-design is not in this fixture: the request must report an
        # unknown skill instead of pretending it meant session history.
        reference = parse_dialogue_reference(
            "keep using frontend-design", known_names=("slides",)
        )
        self.assertEqual(reference.kind, "focus")
        self.assertEqual(reference.name, "frontend-design")

    def test_filler_words_before_the_skill_are_skipped(self):
        reference = parse_dialogue_reference(
            "keep using the skill slides", known_names=("slides",)
        )
        self.assertEqual(reference.name, "slides")

    def test_unknown_token_after_a_release_is_not_a_focus_name(self):
        reference = parse_dialogue_reference(
            "stop using the skill now", known_names=("slides",)
        )
        self.assertEqual(reference.kind, "release")
        self.assertEqual(reference.name, "now")

    def test_empty_query(self):
        self.assertIsNone(parse_dialogue_reference(""))
        self.assertIsNone(parse_dialogue_reference(None))

    def test_release_remainder_keeps_the_rest_of_the_turn(self):
        reference = parse_dialogue_reference(
            "stop using python-tdd and design a polished landing page",
            known_names=("python-tdd",),
        )
        remainder = core._release_remainder(
            "stop using python-tdd and design a polished landing page",
            reference,
            "python-tdd",
        )
        self.assertEqual(remainder, "and design a polished landing page")

    def test_release_remainder_falls_back_to_the_phrase(self):
        reference = parse_dialogue_reference("stop using the skill now")
        self.assertEqual(
            core._release_remainder("stop using the skill now", reference, ""),
            "the skill now",
        )
        self.assertEqual(core._release_remainder("stop using", reference, ""), "")
        self.assertEqual(core._release_remainder("", reference, "slides"), "")


class DialogueReferenceTests(unittest.TestCase):
    def setUp(self):
        self.harness = SessionHarness()
        self.addCleanup(self.harness.close)

    def test_repeat_selects_the_last_skill(self):
        self.harness.select("write Python tests before implementation")
        name, reason, session = self.harness.select("use the same skill")
        self.assertEqual(name, "python-tdd")
        self.assertEqual(reason, "dialogue_reference")
        self.assertEqual(session["reference"]["kind"], "repeat")

    def test_thai_previous_reference(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("design a polished landing page and frontend interface")
        name, reason, _ = self.harness.select("อันที่แล้ว")
        self.assertEqual(name, "python-tdd")
        self.assertEqual(reason, "dialogue_reference_previous")

    def test_previous_fails_closed_with_one_entry(self):
        self.harness.select("write Python tests before implementation")
        name, reason, session = self.harness.select("อันที่แล้ว")
        self.assertIsNone(name)
        self.assertEqual(reason, "no_previous_selection")
        self.assertEqual(session["reference"]["kind"], "previous")

    def test_reference_without_history_fails_closed(self):
        for query in ("use the same skill", "อันที่แล้ว", "keep using"):
            with self.subTest(query=query):
                name, reason, _ = self.harness.select(query)
                self.assertIsNone(name)
                self.assertEqual(reason, "no_previous_selection")

    def test_selection_context_explains_the_reference(self):
        self.harness.select("write Python tests before implementation")
        _, context = self.harness.turn("use the same skill")
        self.assertIn("already used in this session", context)
        self.assertIn("do not present it as a fresh match", context)

    def test_reference_context_asks_instead_of_guessing(self):
        _, context = self.harness.turn("use the same skill")
        self.assertIn("no skill history yet", context)
        self.assertIn("Ask which skill", context)

    def test_explicit_name_beats_a_reference(self):
        self.harness.select("write Python tests before implementation")
        name, reason, session = self.harness.select("$slides and also the same skill")
        self.assertEqual(name, "slides")
        self.assertEqual(reason, "explicit_skill")
        self.assertNotIn("reference", session)

    def test_history_is_scoped_to_each_session(self):
        self.harness.select("write Python tests before implementation", session_id="alpha")
        name, reason, _ = self.harness.select("use the same skill", session_id="beta")
        self.assertIsNone(name)
        self.assertEqual(reason, "no_previous_selection")

    def test_forget_session_drops_history(self):
        self.harness.select("write Python tests before implementation")
        self.assertTrue(self.harness.engine.forget_session("s1"))
        self.assertEqual(self.harness.engine.session_state("s1")["history"], [])
        name, reason, _ = self.harness.select("use the same skill")
        self.assertEqual(reason, "no_previous_selection")
        self.assertFalse(self.harness.engine.forget_session("unknown-session"))

    def test_session_state_view(self):
        self.harness.select("write Python tests before implementation")
        state = self.harness.engine.session_state("s1")
        self.assertTrue(state["enabled"])
        self.assertEqual(state["history"], ["python-tdd"])
        self.assertEqual(state["focus"], "")


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.harness = SessionHarness(focus_turns=3)
        self.addCleanup(self.harness.close)

    def test_focus_request_sets_focus_with_evidence(self):
        self.harness.select("write Python tests before implementation")
        name, reason, session = self.harness.select("keep using python-tdd")
        self.assertEqual(name, "python-tdd")
        self.assertEqual(reason, "focus_requested")
        self.assertEqual(session["focus"], "python-tdd")
        self.assertEqual(session["focus_remaining"], 3)

    def test_focus_selects_a_named_skill_from_history(self):
        self.harness.select("create strategic HTML presentations with charts")
        name, reason, session = self.harness.select("keep using slides")
        self.assertEqual(name, "slides")
        self.assertEqual(session["focus"], "slides")

    def test_focus_nudges_a_weak_lexical_signal(self):
        self.harness.select("write Python tests before implementation")
        # Without focus this wording stays below the threshold.
        baseline = SkillProofEngine({"fixture": self.harness.root}, focus_turns=3)
        baseline.begin_turn(turn_id="b1", session_id="x", task_id="t", query="tests for this module")
        self.assertIsNone(baseline.receipt(turn_id="b1").get("selected"))
        baseline.finish_turn(turn_id="b1")

        self.harness.select("keep using python-tdd")
        name, reason, session = self.harness.select("tests for this module")
        self.assertEqual(name, "python-tdd")
        self.assertEqual(session["focus"], "python-tdd")

    def test_focus_does_not_hijack_a_stronger_candidate(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        name, reason, _ = self.harness.select(
            "design a polished landing page and frontend interface"
        )
        self.assertEqual(name, "frontend-design")
        self.assertEqual(reason, "lexical_match")

    def test_focus_falls_back_only_when_nothing_else_matched(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        name, reason, session = self.harness.select("book a flight to Chiang Mai")
        self.assertEqual(name, "python-tdd")
        self.assertEqual(reason, "focus_fallback")
        self.assertEqual(session["focus"], "python-tdd")

    def test_focus_fallback_does_not_survive_its_own_veto(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        name, reason, session = self.harness.select("don't use python-tdd")
        self.assertIsNone(name)
        self.assertEqual(reason, "negated_skill")
        self.assertEqual(session["focus"], "python-tdd", "a veto does not silently drop the focus")

    def test_focus_fallback_loses_to_ambiguity(self):
        harness = SessionHarness(
            skills=(
                _SKILLS[0],
                ("schema-migrations", "Plan and run database schema migrations safely with rollback"),
                ("database-migrations", "Plan and run database schema migrations safely with rollback"),
            ),
            focus_turns=3,
        )
        self.addCleanup(harness.close)
        harness.select("write Python tests before implementation")
        harness.select("keep using python-tdd")
        name, reason, _ = harness.select("run the migrations safely with rollback")
        self.assertIsNone(name)
        self.assertEqual(reason, "insufficient_margin", "focus must not break a tie")

    def test_focus_fallback_context_warns_it_is_a_fallback(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        _, context = self.harness.turn("book a flight to Chiang Mai")
        self.assertIn("only because the session focus is active", context)
        self.assertIn("score is 0", context)

    def test_focus_expires_after_focus_turns(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        _, _, session = self.harness.select("tests for this module")
        self.assertEqual(session["focus"], "python-tdd")
        self.assertEqual(session["focus_remaining"], 2)
        _, _, session = self.harness.select("tests for this module")
        self.assertEqual(session["focus"], "python-tdd")
        self.assertEqual(session["focus_remaining"], 1)
        _, _, session = self.harness.select("tests for this module")
        self.assertNotIn("focus", session)

    def test_release_clears_focus_and_selects_nothing(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        name, reason, session = self.harness.select("stop using python-tdd")
        self.assertIsNone(name)
        self.assertEqual(reason, "focus_released")
        self.assertNotIn("focus", session)

    def test_release_does_not_ban_the_skill_later(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        self.harness.select("stop using python-tdd")
        name, reason, _ = self.harness.select(
            "write Python tests before implementation"
        )
        self.assertEqual(name, "python-tdd")
        self.assertEqual(reason, "lexical_match")

    def test_release_with_other_work_routed_to_that_work(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        name, reason, _ = self.harness.select(
            "stop using python-tdd and design a polished landing page"
        )
        self.assertEqual(name, "frontend-design")
        self.assertEqual(reason, "lexical_match")

    def test_focus_on_an_unknown_skill_is_reported_as_unknown(self):
        name, reason, _ = self.harness.select("keep using frontend-design-pro")
        self.assertIsNone(name)
        self.assertEqual(reason, "dialogue_reference_unknown")

    def test_release_of_an_unknown_skill_still_clears_the_focus(self):
        self.harness.select("write Python tests before implementation")
        self.harness.select("keep using python-tdd")
        name, reason, session = self.harness.select(
            "stop using frontend-design-pro"
        )
        self.assertIsNone(name)
        self.assertEqual(reason, "focus_released")
        self.assertNotIn("focus", session)

    def test_focus_turns_zero_means_no_expiry(self):
        harness = SessionHarness(focus_turns=0)
        self.addCleanup(harness.close)
        harness.select("write Python tests before implementation")
        harness.select("keep using python-tdd")
        for _ in range(4):
            _, _, session = harness.select("tests for this module")
            self.assertEqual(session["focus"], "python-tdd")
        self.assertEqual(session["focus_remaining"], 0)

    def test_focus_boost_decays_but_stays_positive(self):
        memory = core._SessionMemory(focus="python-tdd", focus_remaining=3)
        engine = SkillProofEngine({"fixture": self.harness.root}, focus_turns=3)
        self.assertEqual(engine._focus_bonus(memory), engine._FOCUS_BONUS)
        memory.focus_remaining = 2
        second = engine._focus_bonus(memory)
        memory.focus_remaining = 1
        third = engine._focus_bonus(memory)
        self.assertGreater(second, third)
        self.assertGreaterEqual(third, engine._FOCUS_BONUS_FLOOR)

    def test_focus_booster_is_skilled_when_history_is_gone(self):
        harness = SessionHarness(focus_turns=3)
        self.addCleanup(harness.close)
        harness.select("write Python tests before implementation")
        harness.select("keep using python-tdd")
        # The focused skill disappears from the catalog.
        for folder in (harness.root / "python-tdd").iterdir():
            folder.unlink()
        (harness.root / "python-tdd").rmdir()
        harness.engine.refresh_catalog()
        name, reason, session = harness.select("tests for this module")
        self.assertIsNone(name)
        self.assertNotIn("focus", session)


class SessionMemoryConfigTests(unittest.TestCase):
    def test_session_memory_false_disables_references(self):
        harness = SessionHarness(session_memory=False)
        self.addCleanup(harness.close)
        name, reason, session = harness.select(
            "write Python tests before implementation"
        )
        self.assertEqual(name, "python-tdd")
        name, reason, session = harness.select("use the same skill")
        self.assertIsNone(name)
        self.assertEqual(reason, "below_threshold")
        self.assertEqual(session, {})
        self.assertEqual(harness.engine.session_state("s1")["enabled"], False)

    def test_invalid_settings_are_rejected(self):
        roots = {"fixture": "."}
        with self.assertRaises(ValueError):
            SkillProofEngine(roots, session_memory="yes")
        for bad in (-1, 51, 2.5, True, "3"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                SkillProofEngine(roots, focus_turns=bad)
        self.assertEqual(SkillProofEngine(roots, focus_turns=0).focus_turns, 0)

    def test_session_map_is_bounded(self):
        harness = SessionHarness()
        self.addCleanup(harness.close)
        for index in range(harness.engine._SESSION_LIMIT + 5):
            harness.turn("write Python tests before implementation", session_id=f"s{index}")
        self.assertLessEqual(len(harness.engine._sessions), harness.engine._SESSION_LIMIT)

    def test_history_is_bounded(self):
        harness = SessionHarness()
        self.addCleanup(harness.close)
        for _ in range(20):
            harness.select("write Python tests before implementation")
            harness.select("design a polished landing page and frontend interface")
        self.assertLessEqual(
            len(harness.engine._sessions["s1"].stack), harness.engine._STACK_LIMIT
        )

    def test_receipt_carries_session_evidence(self):
        harness = SessionHarness()
        self.addCleanup(harness.close)
        harness.select("write Python tests before implementation")
        receipt, _ = harness.turn("use the same skill")
        session = receipt["evidence"]["session"]
        self.assertNotIn("focus", session, "no focus was requested, so none is reported")
        self.assertIn("python-tdd", session["history"])
        self.assertEqual(session["reference"]["kind"], "repeat")


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeContext:
    def __init__(self, settings=None, names=("python-tdd", "slides")):
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


class PluginWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_plugin070_")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        for name, description in _SKILLS:
            folder = root / name
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(
                f"---\nname: {name}\ndescription: {description}\n---\nBody.\n".encode("utf-8")
            )
        self.root = root

    def _plugin(self, **settings):
        settings.setdefault("skill_roots", [str(self.root)])
        context = FakeContext(settings)
        return plugin_module.SkillProofPlugin(context), context

    def test_config_is_passed_to_the_engine(self):
        plugin, _ = self._plugin(focus_turns=9, session_memory=True)
        self.assertEqual(plugin.engine.focus_turns, 9)
        self.assertTrue(plugin.engine.session_memory)
        plugin, _ = self._plugin(session_memory=False)
        self.assertFalse(plugin.engine.session_memory)

    def test_bad_config_falls_back_to_defaults(self):
        plugin, _ = self._plugin(focus_turns="many", session_memory="no")
        self.assertEqual(plugin.engine.focus_turns, 5)
        self.assertTrue(plugin.engine.session_memory)

    def test_end_to_end_turn_shows_focus_in_status_and_explain(self):
        plugin, context = self._plugin()
        def turn(index, query):
            plugin.on_pre_llm_call(
                session_id="s", task_id="t", turn_id=f"turn-{index}", user_message=query
            )
            plugin.on_post_llm_call(session_id="s", task_id="t", turn_id=f"turn-{index}")
        turn(1, "write Python tests before implementation")
        turn(2, "keep using python-tdd")
        status = plugin.handle_command("status")
        self.assertIn("focus=python-tdd", status)
        explain = plugin.handle_command("explain")
        self.assertIn("Focus: python-tdd", explain)
        self.assertIn("Dialogue reference: focus", explain)
        health = json.loads(plugin.handle_command("health"))
        self.assertEqual(health["session"]["focus"], "python-tdd")

    def test_session_end_forgets_history(self):
        plugin, _ = self._plugin()
        plugin.on_pre_llm_call(
            session_id="s", task_id="t", turn_id="turn-1", user_message="write Python tests"
        )
        plugin.on_post_llm_call(session_id="s", task_id="t", turn_id="turn-1")
        self.assertEqual(plugin.engine.session_state("s")["history"], ["python-tdd"])
        plugin.on_session_end(session_id="s")
        self.assertEqual(plugin.engine.session_state("s")["history"], [])

    def test_visible_footer_reports_focus_and_reference(self):
        plugin, _ = self._plugin()
        for index, query in enumerate(
            ("write Python tests before implementation", "keep using python-tdd"), start=1
        ):
            plugin.on_pre_llm_call(
                session_id="s", task_id="t", turn_id=f"turn-{index}", user_message=query
            )
            plugin.on_post_llm_call(session_id="s", task_id="t", turn_id=f"turn-{index}")
        output = plugin.on_transform_llm_output(
            response_text="Here is the plan.", session_id="s"
        )
        self.assertIsNotNone(output)
        self.assertIn("focus python-tdd", output)


if __name__ == "__main__":
    unittest.main()
