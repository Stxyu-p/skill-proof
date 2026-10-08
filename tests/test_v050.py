"""Tests for the v0.5.0 features.

Hub provenance/drift, aliases and synonyms (language-agnostic matching),
candidate hints on ambiguity, and canonical root suggestions.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

import core
from core import SkillProofEngine, scan_catalog, select_skill, suggest_roots

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package_v050",
    _package_path / "__init__.py",
    submodule_search_locations=[str(_package_path)],
)
plugin_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plugin_module
_spec.loader.exec_module(plugin_module)


def _skill_bytes(name: str, description: str, extra: str = "") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\nBody text.\n".encode("utf-8")


def _bundle_hash(folder: pathlib.Path, names) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode("utf-8") + b"\0" + (folder / name).read_bytes())
    return digest.hexdigest()


def _hub_entry(folder: pathlib.Path, files) -> dict:
    return {
        "source": "skills.sh",
        "identifier": "skills-sh/example/demo/demo",
        "trust_level": "community",
        "scan_verdict": "safe",
        "content_hash": "sha256:" + _bundle_hash(folder, files)[:16],
        "files": list(files),
        "metadata": {"source_revision": "abc123"},
        "scan_provenance": {"bundle_hash": "sha256:" + _bundle_hash(folder, files)},
    }


class HubProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_hub_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.folder = self.root / "demo-skill"
        self.folder.mkdir()
        (self.folder / "SKILL.md").write_bytes(_skill_bytes("demo-skill", "Demo workflow"))
        (self.folder / "notes.md").write_text("notes v1", encoding="utf-8")
        self.lock = self.root / "lock.json"
        self.write_lock()

    def write_lock(self):
        self.lock.write_text(
            json.dumps({"version": 1, "installed": {"demo-skill": _hub_entry(self.folder, ["SKILL.md", "notes.md"])}}),
            encoding="utf-8",
        )

    def engine(self, hub_lock_path=None):
        return SkillProofEngine({"local": self.root}, hub_lock_path=hub_lock_path)

    def load(self, engine, name="demo-skill"):
        engine.begin_turn(turn_id="t1", session_id="s", task_id="k", query=f"Use ${name}")
        engine.observe_skill_loaded(session_id="s", task_id="k", skill_name=name)
        return engine.receipt(turn_id="t1")["evidence"]["hub"]

    def test_bundle_match_is_reported(self):
        hub = self.load(self.engine(self.lock))
        self.assertTrue(hub["available"])
        self.assertEqual(hub["bundle"], "match")
        self.assertEqual(hub["trust_level"], "community")
        self.assertEqual(hub["scan_verdict"], "safe")
        self.assertEqual(hub["source_revision"], "abc123")

    def test_bundle_modified_is_reported(self):
        (self.folder / "notes.md").write_text("notes tampered", encoding="utf-8")
        hub = self.load(self.engine(self.lock))
        self.assertEqual(hub["bundle"], "modified")

    def test_missing_lock_is_silent(self):
        hub = self.load(self.engine(self.root / "no-such-lock.json"))
        self.assertEqual(hub, {})

    def test_malformed_lock_degrades(self):
        self.lock.write_text("{not json", encoding="utf-8")
        hub = self.load(self.engine(self.lock))
        self.assertEqual(hub, {})
        self.assertEqual(self.engine(self.lock).hub_status()["entries"], 0)

    def test_traversal_entry_is_unknown(self):
        lock = {"version": 1, "installed": {"demo-skill": _hub_entry(self.folder, ["SKILL.md"])}}
        lock["installed"]["demo-skill"]["files"] = ["../outside.md"]
        self.lock.write_text(json.dumps(lock), encoding="utf-8")
        hub = self.load(self.engine(self.lock))
        self.assertEqual(hub["bundle"], "unknown")

    def test_hub_status(self):
        self.assertEqual(self.engine().hub_status(), {"enabled": False})
        status = self.engine(self.lock).hub_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["entries"], 1)


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeContext:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.hooks = {}
        self.commands = {}
        self.state = FakeState()

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def dispatch_tool(self, name, args):
        return json.dumps({"success": True, "skills": [{"name": "demo-skill"}]})

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, **metadata):
        self.commands[name] = (handler, metadata)


class HubFooterTests(unittest.TestCase):
    def test_modified_bundle_is_flagged_in_the_footer(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_hub_footer_") as tmp:
            root = pathlib.Path(tmp)
            folder = root / "demo-skill"
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(_skill_bytes("demo-skill", "Demo workflow"))
            (folder / "notes.md").write_text("notes v1", encoding="utf-8")
            lock = root / "lock.json"
            lock.write_text(
                json.dumps({"version": 1, "installed": {"demo-skill": _hub_entry(folder, ["SKILL.md", "notes.md"])}}),
                encoding="utf-8",
            )
            ctx = FakeContext(
                {
                    "skill_roots": [str(root)],
                    "hub_provenance": True,
                    "hub_lock_path": str(lock),
                    "visible_receipt": True,
                    "receipt_style": "verbose",
                }
            )
            plugin = plugin_module.SkillProofPlugin(ctx)
            plugin.on_pre_llm_call(session_id="s", task_id="t", turn_id="turn-1", user_message="Use $demo-skill")
            (folder / "notes.md").write_text("notes tampered", encoding="utf-8")
            plugin.on_skill_lifecycle(action="loaded", skill_name="demo-skill", session_id="s", task_id="t")
            output = plugin.on_transform_llm_output(response_text="done", session_id="s")

            self.assertIsNotNone(output)
            self.assertIn("hub_bundle_modified", output)

    def test_health_reports_hub_and_root_suggestions(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_health_") as tmp:
            root = pathlib.Path(tmp)
            folder = root / "demo-skill"
            folder.mkdir()
            (folder / "SKILL.md").write_bytes(_skill_bytes("demo-skill", "Demo workflow"))
            ctx = FakeContext({"skill_roots": [str(root)], "hub_provenance": False})
            plugin = plugin_module.SkillProofPlugin(ctx)
            report = json.loads(plugin.handle_command("health"))
            self.assertEqual(report["version"], "0.5.0")
            self.assertEqual(report["hub"], {"enabled": False})
            self.assertEqual(report["root_suggestions"], [])


class AliasTests(unittest.TestCase):
    def make_root(self, rows):
        tmp = tempfile.TemporaryDirectory(prefix="skill_proof_alias_")
        root = pathlib.Path(tmp.name)
        for directory, name, description, extra in rows:
            folder = root / directory
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_bytes(_skill_bytes(name, description, extra))
        return root, tmp

    def test_block_and_inline_aliases_parse(self):
        root, tmp = self.make_root(
            [
                ("a", "alpha", "Alpha workflow", "aliases:\n  - first\n  - uno\n"),
                ("b", "bravo", "Bravo workflow", "aliases: [second, dos]\n"),
            ]
        )
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        by_name = {skill.name: skill for skill in catalog.skills}
        self.assertEqual(by_name["alpha"].aliases, ("first", "uno"))
        self.assertEqual(by_name["bravo"].aliases, ("second", "dos"))

    def test_english_alias_selects(self):
        root, tmp = self.make_root([("a", "concise-writer", "Rewrite text", "aliases: [terse]\n")])
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        result = select_skill(catalog, "please make this terse")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "concise-writer")
        self.assertIn("alias_phrase", result.selected.reasons)

    def test_thai_alias_selects(self):
        root, tmp = self.make_root([("a", "concise-writer", "Rewrite text", "aliases: [ตัวพูดสั้น]\n")])
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        result = select_skill(catalog, "ช่วยใช้ตัวพูดสั้นหน่อย")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "concise-writer")

    def test_cjk_alias_selects(self):
        root, tmp = self.make_root([("a", "doc-helper", "Rewrite text", "aliases: [文档助手]\n")])
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        result = select_skill(catalog, "帮我用文档助手")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "doc-helper")

    def test_korean_alias_selects(self):
        root, tmp = self.make_root([("a", "doc-helper", "Rewrite text", "aliases: [문서도우미]\n")])
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        result = select_skill(catalog, "문서도우미 써줘")
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "doc-helper")

    def test_synonyms_config_merges_with_aliases(self):
        root, tmp = self.make_root([("a", "python-tdd", "Write Python tests before implementation", "")])
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        result = select_skill(catalog, "help me test first", synonyms={"python-tdd": ["test first"]})
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected.skill.name, "python-tdd")

    def test_short_alias_does_not_match_inside_words(self):
        root, tmp = self.make_root([("a", "poet", "Verse workflow", "aliases: [ui]\n")])
        self.addCleanup(tmp.cleanup)
        catalog = scan_catalog({"local": root})
        result = select_skill(catalog, "rebuild the project")
        self.assertIsNone(result.selected)
        self.assertFalse(core._phrase_present("ui", "rebuild"))


class AmbiguousCandidateTests(unittest.TestCase):
    def test_ambiguous_context_names_candidates(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_ambiguous_") as tmp:
            root = pathlib.Path(tmp)
            for name in ("alpha-workflow", "beta-workflow"):
                folder = root / name
                folder.mkdir()
                (folder / "SKILL.md").write_bytes(_skill_bytes(name, "Testing workflow"))
            engine = SkillProofEngine({"local": root}, max_candidates=2)
            turn = engine.begin_turn(turn_id="t1", session_id="s", task_id="k", query="Testing workflow")
            self.assertEqual(turn.selection.status, "ambiguous")
            self.assertIn("alpha-workflow", turn.context)
            self.assertIn("beta-workflow", turn.context)
            self.assertIn("Ask which skill to load", turn.context)

    def test_ambiguous_context_is_bounded_by_max_candidates(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_ambiguous_") as tmp:
            root = pathlib.Path(tmp)
            for name in ("alpha", "bravo", "charlie", "delta"):
                folder = root / name
                folder.mkdir()
                (folder / "SKILL.md").write_bytes(_skill_bytes(name, "Testing workflow"))
            engine = SkillProofEngine({"local": root}, max_candidates=2)
            turn = engine.begin_turn(turn_id="t1", session_id="s", task_id="k", query="Testing workflow")
            names = [name for name in ("alpha", "bravo", "charlie", "delta") if name in turn.context]
            self.assertEqual(len(names), 2)


class RootSuggestionTests(unittest.TestCase):
    def _junction(self, link: pathlib.Path, target: pathlib.Path) -> None:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation unavailable: {result.stdout}{result.stderr}")

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_junction_target_parent_is_suggested(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_roots_") as tmp:
            base = pathlib.Path(tmp)
            facade = base / "facade"
            facade.mkdir()
            physical = base / "physical"
            physical.mkdir()
            real = facade / "real-skill"
            real.mkdir()
            (real / "SKILL.md").write_bytes(_skill_bytes("real-skill", "Real workflow"))
            linked = physical / "linked-skill"
            linked.mkdir()
            (linked / "SKILL.md").write_bytes(_skill_bytes("linked-skill", "Linked workflow"))
            self._junction(facade / "linked-skill", linked)

            suggestions = suggest_roots({"facade": facade})

            self.assertEqual(len(suggestions), 1)
            self.assertEqual(pathlib.Path(suggestions[0]["path"]).resolve(), physical.resolve())
            self.assertEqual(suggestions[0]["skills"], 1)

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_configured_canonical_root_is_not_suggested(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_roots_") as tmp:
            base = pathlib.Path(tmp)
            facade = base / "facade"
            facade.mkdir()
            physical = base / "physical"
            physical.mkdir()
            linked = physical / "linked-skill"
            linked.mkdir()
            (linked / "SKILL.md").write_bytes(_skill_bytes("linked-skill", "Linked workflow"))
            self._junction(facade / "linked-skill", linked)

            self.assertEqual(suggest_roots({"facade": facade, "physical": physical}), [])


class MultilingualPhraseTests(unittest.TestCase):
    def test_spaceless_scripts_match_by_substring(self):
        self.assertTrue(core._phrase_present("文档", "帮我用文档"))
        self.assertTrue(core._phrase_present("สกิล", "ใช้สกิลนี้"))

    def test_spaced_scripts_keep_token_boundaries(self):
        # Korean is space-separated: a standalone term matches, while a term
        # inside a longer word (particle attached) does not; there is no
        # morphology or dictionary, only boundaries.
        self.assertTrue(core._phrase_present("문서도우미", "문서도우미 써줘"))
        self.assertFalse(core._phrase_present("문서", "문서를 써줘"))

    def test_latin_keeps_token_boundaries(self):
        self.assertFalse(core._phrase_present("ui", "build"))
        self.assertFalse(core._phrase_present("build", "rebuild"))
        self.assertTrue(core._phrase_present("build", "please build it"))


if __name__ == "__main__":
    unittest.main()
