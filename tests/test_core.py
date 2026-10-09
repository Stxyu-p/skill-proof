from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
import hashlib
import json

from core import (
    Catalog,
    SkillProofEngine,
    extract_explicit_skill_names,
    scan_catalog,
    select_skill,
)


def _skill_bytes(name: str, description: str, body: str = "Follow the workflow.") -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "metadata:\n"
        "  hermes:\n"
        "    tags: [testing, workflow]\n"
        "---\n\n"
        f"# {name}\n\n{body}\n"
    ).encode("utf-8")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_catalog_")
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_skill(
        self,
        relative_dir: str,
        name: str,
        description: str,
        body: str = "Follow the workflow.",
    ) -> pathlib.Path:
        skill_dir = self.root / relative_dir
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        path.write_bytes(_skill_bytes(name, description, body))
        return path

    def test_discovers_nested_skill_md_with_stable_relative_provenance(self):
        self.write_skill("software/python-tdd", "python-tdd", "Test Python changes with TDD")
        (self.root / "notes.md").write_text("not a skill", encoding="utf-8")

        catalog = scan_catalog({"local": self.root})

        self.assertEqual([skill.name for skill in catalog.skills], ["python-tdd"])
        skill = catalog.skills[0]
        self.assertEqual(skill.root_id, "local")
        self.assertEqual(skill.relative_path, "software/python-tdd/SKILL.md")
        self.assertNotIn(str(self.root), skill.skill_id)

    def test_skill_directory_is_a_discovery_boundary(self):
        self.write_skill("outer", "outer", "Outer workflow")
        nested = self.root / "outer" / "references" / "fake"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_bytes(_skill_bytes("nested", "Must not be indexed"))

        catalog = scan_catalog({"local": self.root})

        self.assertEqual([skill.name for skill in catalog.skills], ["outer"])

    def test_malformed_skill_is_excluded_without_breaking_valid_sibling(self):
        self.write_skill("valid", "valid-skill", "Valid workflow")
        malformed = self.root / "bad"
        malformed.mkdir()
        (malformed / "SKILL.md").write_text(
            "---\nname: bad\ndescription: missing closing delimiter\n",
            encoding="utf-8",
        )

        catalog = scan_catalog({"local": self.root})

        self.assertEqual([skill.name for skill in catalog.skills], ["valid-skill"])
        self.assertIn("invalid_frontmatter", {item.code for item in catalog.diagnostics})

    def test_spaced_skill_name_is_valid(self):
        self.write_skill("sf", "Skill Factory", "A meta-skill that watches workflows")

        catalog = scan_catalog({"local": self.root})

        self.assertEqual([skill.name for skill in catalog.skills], ["Skill Factory"])
        self.assertEqual(catalog.skills[0].normalized_name, "skill factory")

    def test_invalid_utf8_is_excluded(self):
        bad = self.root / "bad-utf8"
        bad.mkdir()
        (bad / "SKILL.md").write_bytes(b"---\nname: bad\ndescription: bad\n---\n\xff")

        catalog = scan_catalog({"local": self.root})

        self.assertEqual(catalog.skills, ())
        self.assertIn("invalid_utf8", {item.code for item in catalog.diagnostics})

    def test_duplicate_canonical_names_remain_ambiguous(self):
        self.write_skill("one", "Python-TDD", "First")
        self.write_skill("two", "python-tdd", "Second")

        catalog = scan_catalog({"local": self.root})

        self.assertEqual(len(catalog.skills), 2)
        self.assertEqual(len(catalog.by_name("python-tdd")), 2)
        self.assertIn("duplicate_name", {item.code for item in catalog.diagnostics})

    def test_catalog_order_and_hash_do_not_depend_on_creation_order(self):
        self.write_skill("z-last", "z-last", "Z workflow")
        self.write_skill("a-first", "a-first", "A workflow")
        first = scan_catalog({"local": self.root})

        second_tmp = tempfile.TemporaryDirectory(prefix="skill_proof_catalog_second_")
        try:
            second_root = pathlib.Path(second_tmp.name)
            for directory, name, description in (
                ("a-first", "a-first", "A workflow"),
                ("z-last", "z-last", "Z workflow"),
            ):
                path = second_root / directory
                path.mkdir(parents=True)
                (path / "SKILL.md").write_bytes(_skill_bytes(name, description))
            second = scan_catalog({"local": second_root})
        finally:
            second_tmp.cleanup()

        self.assertEqual([s.name for s in first.skills], ["a-first", "z-last"])
        self.assertEqual(first.catalog_hash, second.catalog_hash)

    def test_symlinked_skill_file_is_rejected(self):
        target = self.write_skill("target", "safe-target", "Safe target")
        link_dir = self.root / "linked"
        link_dir.mkdir()
        link = link_dir / "SKILL.md"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        catalog = scan_catalog({"local": self.root})

        self.assertEqual([s.name for s in catalog.skills], ["safe-target"])
        self.assertIn("unsafe_symlink", {item.code for item in catalog.diagnostics})

    def test_symlinked_root_is_rejected(self):
        real_root = self.root / "real"
        real_root.mkdir()
        skill = real_root / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_bytes(_skill_bytes("real", "Real skill"))
        linked_root = self.root / "root-link"
        try:
            os.symlink(real_root, linked_root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        catalog = scan_catalog({"linked": linked_root})

        self.assertEqual(catalog.skills, ())
        self.assertIn("unsafe_root", {item.code for item in catalog.diagnostics})

    def test_disabled_skills_excluded_from_scan(self):
        self.write_skill("active", "active-skill", "Active workflow")
        self.write_skill("disabled", "disabled-skill", "Disabled workflow")
        all_skills = scan_catalog({"local": self.root}, disabled=())
        self.assertEqual(len(all_skills.skills), 2)
        filtered = scan_catalog({"local": self.root}, disabled=["disabled-skill"])
        self.assertEqual([s.name for s in filtered.skills], ["active-skill"])


class SelectionTests(unittest.TestCase):
    def catalog(self, rows: list[tuple[str, str]]) -> tuple[Catalog, tempfile.TemporaryDirectory]:
        tmp = tempfile.TemporaryDirectory(prefix="skill_proof_selection_")
        root = pathlib.Path(tmp.name)
        for index, (name, description) in enumerate(rows):
            directory = root / f"skill-{index}"
            directory.mkdir()
            (directory / "SKILL.md").write_bytes(_skill_bytes(name, description))
        return scan_catalog({"local": root}), tmp

    def test_explicit_unique_name_overrides_lexical_ranking(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Write tests before Python implementation"),
            ("frontend-design", "Design a polished landing page and frontend"),
        ])
        try:
            result = select_skill(
                catalog,
                "Design a landing page but use the requested skill",
                explicit_names=("python-tdd",),
            )
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "python-tdd")
        self.assertTrue(result.explicit)

    def test_unknown_explicit_name_does_not_fallback(self):
        catalog, tmp = self.catalog([("frontend-design", "Design a polished landing page")])
        try:
            result = select_skill(
                catalog,
                "Design a landing page",
                explicit_names=("missing-skill",),
            )
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "unknown_explicit_skill")
        self.assertIsNone(result.selected)

    def test_ambiguous_explicit_name_is_not_chosen_by_path_order(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "First copy"),
            ("Python-TDD", "Second copy"),
        ])
        try:
            result = select_skill(catalog, "Use tests", explicit_names=("python-tdd",))
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "ambiguous_explicit_skill")

    def test_multiple_explicit_names_fail_closed(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Testing"),
            ("frontend-design", "Frontend"),
        ])
        try:
            result = select_skill(
                catalog,
                "Use two skills",
                explicit_names=("python-tdd", "frontend-design"),
            )
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "multiple_explicit_skills")

    def test_exact_name_match_outranks_description_only_match(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Test Python changes"),
            ("general-testing", "A broad workflow mentioning python-tdd many times"),
        ])
        try:
            result = select_skill(catalog, "Please use python-tdd for this change")
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "python-tdd")

    def test_negated_skill_name_is_not_selected(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Test Python changes"),
            ("frontend-design", "Design a polished landing page"),
        ])
        try:
            result = select_skill(catalog, "Please do not use python-tdd for this change")
        finally:
            tmp.cleanup()

        self.assertIsNone(result.selected)
        self.assertEqual(result.status, "no_match")
        self.assertEqual(result.reason, "negated_skill")

    def test_thai_negation_excludes_skill(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Test Python changes"),
            ("frontend-design", "Design a polished landing page"),
        ])
        try:
            result = select_skill(catalog, "ไม่เอา python-tdd")
        finally:
            tmp.cleanup()

        self.assertIsNone(result.selected)
        self.assertEqual(result.status, "no_match")
        self.assertEqual(result.reason, "negated_skill")

    def test_mixed_spaceless_query_selects_spaced_skill(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Test Python changes with test-driven development tdd"),
            ("frontend-design", "Design a polished landing page and frontend"),
        ])
        try:
            result = select_skill(catalog, "ช่วยทำ tdd ในโปรเจกต์นี้ให้หน่อย")
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "python-tdd")

    def test_negated_explicit_name_falls_back_to_lexical(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Test Python changes"),
            ("frontend-design", "Design a polished landing page"),
        ])
        try:
            query = "Don't use $python-tdd here"
            names = extract_explicit_skill_names(query, known_names=("python-tdd", "frontend-design"))
            result = select_skill(catalog, query, explicit_names=names)
        finally:
            tmp.cleanup()

        self.assertEqual(names, ("python-tdd",))
        self.assertIsNone(result.selected)
        self.assertEqual(result.status, "no_match")

    def test_explicit_request_survives_other_skill_negation(self):
        catalog, tmp = self.catalog([
            ("python-tdd", "Test Python changes"),
            ("frontend-design", "Design a polished landing page"),
        ])
        try:
            query = "Use $python-tdd but without frontend-design"
            names = extract_explicit_skill_names(query)
            result = select_skill(catalog, query, explicit_names=names)
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "python-tdd")
        self.assertEqual(result.reason, "explicit_skill")

    def test_short_infix_does_not_trigger_description_phrase(self):
        catalog, tmp = self.catalog([("guide", "Build a landing page")])
        try:
            result = select_skill(catalog, "ui")
        finally:
            tmp.cleanup()

        self.assertIsNone(result.selected)
        self.assertEqual(result.status, "no_match")

    def test_zero_overlap_returns_no_selection(self):
        catalog, tmp = self.catalog([("python-tdd", "Test Python changes")])
        try:
            result = select_skill(catalog, "Book a table for dinner tonight")
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "no_match")
        self.assertIsNone(result.selected)

    def test_unicode_thai_description_can_match_without_claiming_stemming(self):
        catalog, tmp = self.catalog([
            ("thai-docs", "สร้างและตรวจเอกสารภาษาไทย"),
            ("python-tdd", "Test Python changes"),
        ])
        try:
            result = select_skill(catalog, "ช่วยสร้างและตรวจเอกสารภาษาไทย")
        finally:
            tmp.cleanup()

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "thai-docs")

    def test_explicit_name_parser_supports_dollar_english_and_thai_forms(self):
        self.assertEqual(extract_explicit_skill_names("Use $python-tdd now"), ("python-tdd",))
        self.assertEqual(extract_explicit_skill_names("use skill frontend-design"), ("frontend-design",))
        self.assertEqual(extract_explicit_skill_names("ใช้สกิล python-tdd"), ("python-tdd",))

    def test_default_synonyms_bridge_natural_language_queries(self):
        catalog, tmp = self.catalog([
            ("ui-ux-pro-max", "UI/UX design intelligence"),
            ("github-pr-workflow", "GitHub PR lifecycle"),
        ])
        try:
            # Query uses "dashboard" which is in DEFAULT_SYNONYMS for ui-ux-pro-max
            res = select_skill(catalog, "ช่วยทำ dashboard สวยๆ")
            self.assertEqual(res.status, "selected")
            self.assertEqual(res.selected.skill.name, "ui-ux-pro-max")

            # Query uses "pull request" which is in DEFAULT_SYNONYMS for github-pr-workflow
            res_pr = select_skill(catalog, "please review my pull request")
            self.assertEqual(res_pr.status, "selected")
            self.assertEqual(res_pr.selected.skill.name, "github-pr-workflow")
        finally:
            tmp.cleanup()

    def test_negation_vetoes_skill_via_alias(self):
        catalog, tmp = self.catalog([
            ("test-driven-development", "TDD tests before code"),
            ("python-debugpy", "Debug Python code and bugs"),
        ])
        try:
            # Query explicitly vetoes "TDD" ("ไม่เอา TDD") which is an alias for test-driven-development
            res = select_skill(catalog, "ช่วยดูโค้ด Python ให้หน่อย ไม่เอา TDD ขอแค่หาจุดบั๊ก")
            # test-driven-development MUST NOT be selected
            if res.selected:
                self.assertNotEqual(res.selected.skill.name, "test-driven-development")
                self.assertEqual(res.selected.skill.name, "python-debugpy")
        finally:
            tmp.cleanup()

    def test_select_skill_and_negation_handle_none_query_safely(self):
        catalog, tmp = self.catalog([("sample-skill", "Sample description")])
        try:
            # None query should abstain cleanly without raising AttributeError
            res_none = select_skill(catalog, None)
            self.assertEqual(res_none.status, "no_match")
            # Empty query should also abstain cleanly
            res_empty = select_skill(catalog, "")
            self.assertEqual(res_empty.status, "no_match")
            # Negation probe on None query should return False
            from core import _skill_is_negated, _query_might_contain_negation
            self.assertFalse(_query_might_contain_negation(None))
            self.assertFalse(_skill_is_negated("sample-skill", None))
        finally:
            tmp.cleanup()

    def test_type_hints_and_suggest_roots_resilience(self):
        import typing
        from core import normalize_identifiers, scan_catalog, suggest_roots
        # Must not raise NameError: name 'Iterable' is not defined
        hints_norm = typing.get_type_hints(normalize_identifiers)
        self.assertIn("names", hints_norm)
        hints_scan = typing.get_type_hints(scan_catalog)
        self.assertIn("roots", hints_scan)
        # suggest_roots must safely handle None and empty mapping
        self.assertEqual(suggest_roots(None), [])
        self.assertEqual(suggest_roots({}), [])

    def test_lru_caches_on_duplicates_and_spaceless_ngrams(self):
        from core import _catalog_duplicate_names, _skill_spaceless_ngrams
        # _catalog_duplicate_names should return a frozenset
        dupes = _catalog_duplicate_names(())
        self.assertIsInstance(dupes, frozenset)
        # _skill_spaceless_ngrams should cache and return a frozenset
        ngrams = _skill_spaceless_ngrams("ทดสอบการใช้งาน", ("แท็ก",), ("ชื่ออื่น",))
        self.assertIsInstance(ngrams, frozenset)
        self.assertGreater(len(ngrams), 0)

    def test_cli_select_supports_both_positional_and_flag_queries(self):
        import cli
        import io
        import contextlib
        catalog, tmp = self.catalog([("demo-cli-skill", "Testing CLI positional query")])
        try:
            # Positional query
            buf_pos = io.StringIO()
            with contextlib.redirect_stdout(buf_pos):
                code = cli.main(["select", "demo-cli-skill", "--root", str(tmp.name), "--json"])
            self.assertEqual(code, 0)
            data_pos = json.loads(buf_pos.getvalue())
            self.assertEqual(data_pos["decision"]["status"], "selected")
            self.assertEqual(data_pos["selected"]["name"], "demo-cli-skill")

            # Flag query (--query)
            buf_flag = io.StringIO()
            with contextlib.redirect_stdout(buf_flag):
                code = cli.main(["select", "--query", "demo-cli-skill", "--root", str(tmp.name), "--json"])
            self.assertEqual(code, 0)
            data_flag = json.loads(buf_flag.getvalue())
            self.assertEqual(data_flag["decision"]["status"], "selected")
            self.assertEqual(data_flag["selected"]["name"], "demo-cli-skill")

            # Missing query fails with exit code 2
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main(["select", "--root", str(tmp.name)])
            self.assertEqual(code, 2)
            self.assertIn("query is required", err.getvalue())
        finally:
            tmp.cleanup()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_engine_")
        self.root = pathlib.Path(self.tmp.name)
        self.skill_path = self.root / "python-tdd" / "SKILL.md"
        self.skill_path.parent.mkdir()
        self.raw_skill = _skill_bytes(
            "python-tdd",
            "Write Python tests before implementation",
            "Start with a failing test, then make it pass.",
        )
        self.skill_path.write_bytes(self.raw_skill)

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, mode: str = "nudge", **kwargs) -> SkillProofEngine:
        return SkillProofEngine({"local": self.root}, mode=mode, **kwargs)

    def begin(self, engine: SkillProofEngine, query: str = "Use $python-tdd for this change"):
        return engine.begin_turn(
            turn_id="turn-1",
            session_id="session-1",
            task_id="task-1",
            query=query,
        )

    def test_begin_turn_injects_bounded_load_instruction(self):
        engine = self.engine(context_budget_bytes=800)

        turn = self.begin(engine)

        self.assertEqual(turn.selection.status, "selected")
        self.assertIn("skill_view", turn.context)
        self.assertIn("python-tdd", turn.context)
        self.assertLessEqual(len(turn.context.encode("utf-8")), 800)
        self.assertNotIn(str(self.root), turn.context)

    def test_context_budget_uses_a_safe_compact_selection_message(self):
        engine = self.engine(context_budget_bytes=40)

        turn = self.begin(engine)

        self.assertEqual(turn.context, 'Use skill_view for "python-tdd" first.')
        self.assertLessEqual(len(turn.context.encode("utf-8")), 40)
        self.assertIn("context_budget_exceeded", turn.errors)

    def test_tiny_context_budget_does_not_bypass_enforcement(self):
        engine = self.engine(mode="enforce", context_budget_bytes=1)

        turn = self.begin(engine)
        blocked = engine.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1", tool_name="terminal"
        )

        self.assertEqual(turn.context, "")
        self.assertIn("context_budget_exceeded", turn.errors)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "selected_skill_not_loaded")

    def test_enforce_blocks_operational_tool_until_selected_skill_loads(self):
        engine = self.engine(mode="enforce")
        self.begin(engine)

        blocked = engine.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="terminal",
        )
        skill_tool = engine.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="skill_view",
        )

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "selected_skill_not_loaded")
        self.assertTrue(skill_tool.allowed)

    def test_enforce_allows_normal_work_when_no_skill_matches(self):
        engine = self.engine(mode="enforce")
        self.begin(engine, "Book a table for dinner tonight")

        result = engine.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="terminal",
        )

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "no_required_skill")

    def test_unknown_explicit_skill_fails_closed_only_in_enforce_mode(self):
        enforce = self.engine(mode="enforce")
        nudge = self.engine(mode="nudge")
        self.begin(enforce, "Use $missing-skill now")
        self.begin(nudge, "Use $missing-skill now")

        enforce_result = enforce.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="terminal",
        )
        nudge_result = nudge.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="terminal",
        )

        self.assertFalse(enforce_result.allowed)
        self.assertEqual(enforce_result.reason, "unknown_explicit_skill")
        self.assertTrue(nudge_result.allowed)

    def test_authoritative_loaded_event_records_exact_source_hash(self):
        engine = self.engine(mode="enforce")
        self.begin(engine)

        recorded = engine.observe_skill_loaded(
            session_id="session-1",
            task_id="task-1",
            skill_name="python-tdd",
            provenance="local",
            use_count=4,
        )
        receipt = engine.receipt(turn_id="turn-1")

        self.assertTrue(recorded)
        self.assertTrue(receipt["evidence"]["hermes_loaded_event"])
        self.assertEqual(
            receipt["evidence"]["source_sha256"],
            hashlib.sha256(self.raw_skill).hexdigest(),
        )
        self.assertEqual(receipt["evidence"]["source_bytes"], len(self.raw_skill))

    def test_unrelated_loaded_event_does_not_satisfy_selected_skill(self):
        engine = self.engine(mode="enforce")
        self.begin(engine)

        recorded = engine.observe_skill_loaded(
            session_id="session-1",
            task_id="task-1",
            skill_name="other-skill",
            provenance="local",
        )

        self.assertFalse(recorded)
        self.assertFalse(engine.receipt(turn_id="turn-1")["evidence"]["hermes_loaded_event"])

    def test_unknown_task_cannot_load_another_tasks_selection(self):
        engine = self.engine()
        self.begin(engine)
        self.assertFalse(engine.observe_skill_loaded(
            session_id="session-1", task_id="other-task",
            skill_name="python-tdd", provenance="local",
        ))

    def test_unknown_turn_does_not_fall_back_to_current_turn(self):
        engine = self.engine()
        self.begin(engine)
        self.assertEqual(engine.receipt(
            turn_id="old-turn", session_id="session-1", task_id="task-1",
        ), {})

    def test_source_change_between_selection_and_load_is_reported(self):
        engine = self.engine(mode="enforce")
        self.begin(engine)
        self.skill_path.write_bytes(_skill_bytes("renamed-skill", "Changed identity"))

        recorded = engine.observe_skill_loaded(
            session_id="session-1",
            task_id="task-1",
            skill_name="python-tdd",
            provenance="local",
        )
        receipt = engine.receipt(turn_id="turn-1")

        self.assertTrue(recorded)  # Hermes' event remains authoritative for the load itself.
        self.assertEqual(receipt["evidence"]["source_error"], "skill_changed")
        self.assertIsNone(receipt["evidence"]["source_sha256"])

    def test_post_tool_result_hash_is_labeled_pre_transform(self):
        engine = self.engine()
        self.begin(engine)
        engine.observe_skill_loaded(
            session_id="session-1", task_id="task-1",
            skill_name="python-tdd", provenance="local",
        )
        result = json.dumps({"success": True, "name": "python-tdd", "content": "instructions"})

        engine.observe_tool_result(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="skill_view", args={"name": "python-tdd"}, result=result,
            status="success",
        )
        evidence = engine.receipt(turn_id="turn-1")["evidence"]

        self.assertEqual(
            evidence["tool_result_sha256_pre_transform"],
            hashlib.sha256(result.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(evidence["tool_result_bytes_pre_transform"], len(result.encode("utf-8")))

    def test_first_operational_tool_after_load_marks_active_but_not_compliant(self):
        engine = self.engine(mode="enforce")
        self.begin(engine)
        engine.observe_skill_loaded(
            session_id="session-1", task_id="task-1",
            skill_name="python-tdd", provenance="local",
        )

        allowed = engine.guard_tool(
            turn_id="turn-1", session_id="session-1", task_id="task-1",
            tool_name="write_file",
        )
        receipt = engine.receipt(turn_id="turn-1")

        self.assertTrue(allowed.allowed)
        self.assertTrue(receipt["runtime"]["active"])
        self.assertEqual(receipt["runtime"]["active_evidence"], "post_load_tool_call")
        self.assertEqual(receipt["compliance"], "unassessed")
        self.assertEqual(receipt["verification"], "unverified")

    def test_receipt_is_bounded_truthful_and_contains_no_raw_query_or_absolute_path(self):
        engine = self.engine()
        self.begin(engine, "Use $python-tdd and keep secret prompt marker XYZ")
        engine.observe_skill_loaded(
            session_id="session-1", task_id="task-1",
            skill_name="python-tdd", provenance="local",
        )

        receipt = engine.receipt(turn_id="turn-1")
        serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)

        self.assertEqual(receipt["schema_version"], "skill-proof.receipt.v1")
        self.assertNotIn("XYZ", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("model_read", serialized)
        self.assertEqual(receipt["compliance"], "unassessed")
        self.assertEqual(receipt["verification"], "unverified")

    def test_finish_turn_is_idempotent_and_summary_states_limits(self):
        engine = self.engine()
        self.begin(engine)
        engine.observe_skill_loaded(
            session_id="session-1", task_id="task-1",
            skill_name="python-tdd", provenance="local",
        )

        first = engine.finish_turn(turn_id="turn-1")
        second = engine.finish_turn(turn_id="turn-1")
        summary = engine.summary(turn_id="turn-1")

        self.assertEqual(first, second)
        self.assertIn("loaded=yes", summary)
        self.assertIn("compliance=unassessed", summary)
        self.assertIn("verification=unverified", summary)

    def test_contains_spaceless_script_and_identifier_helpers(self):
        from core import _contains_spaceless_script, _identifier_in_query, _skill_is_negated, normalize_identifier
        self.assertTrue(_contains_spaceless_script("ทดสอบระบบ"))
        self.assertFalse(_contains_spaceless_script("pure english query"))
        self.assertFalse(_contains_spaceless_script(""))
        self.assertFalse(_contains_spaceless_script(None))

        q = "please use python-tdd for testing"
        norm_q = normalize_identifier(q)
        self.assertTrue(_identifier_in_query("python-tdd", q, norm_q))
        self.assertFalse(_identifier_in_query("systematic-debugging", q, norm_q))

        neg_q = "อย่าใช้ python-tdd นะ"
        norm_neg = normalize_identifier(neg_q)
        self.assertTrue(_skill_is_negated("python-tdd", neg_q, (), norm_neg))
        self.assertFalse(_skill_is_negated("systematic-debugging", neg_q, (), norm_neg))


if __name__ == "__main__":
    unittest.main()
