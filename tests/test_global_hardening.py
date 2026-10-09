"""Tests for Global Use Hardening Wave 1:

- Universal Multilingual Tokenization & Script Support (Hindi/Devanagari, Chinese, Korean, Japanese, Thai)
- Multilingual Stopwords and Negation Filtering
- Expanded Host Bridges (gemini, cline, windsurf, copilot, auto-detection)
- Weird and Non-standard Folder State Resilience
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import bridge
import cli
import core


def _skill_bytes(name: str, description: str, *, tags: str = "", aliases: str = "", related: str = "") -> bytes:
    tags_line = f"tags: [{tags}]\n" if tags else ""
    alias_line = f"aliases: [{aliases}]\n" if aliases else ""
    related_line = f"related_skills: [{related}]\n" if related else ""
    return (
        f"---\nname: {name}\ndescription: {description}\n{tags_line}{alias_line}{related_line}---\n\n"
        "Instruction body text.\n"
    ).encode("utf-8")


class MultilingualHardeningTests(unittest.TestCase):
    def test_hindi_devanagari_tokenization_preserves_matras(self):
        # Hindi: "कोड की समीक्षा करें और बग ठीक करें" (Review code and fix bug)
        # Previously, \w would split 'कोड' into ['क', 'ड'] because the matra was considered \W.
        tokens = core._tokens("कोड की समीक्षा करें और बग ठीक करें")
        self.assertIn("कोड", tokens)
        self.assertIn("समीक्षा", tokens)
        self.assertIn("बग", tokens)
        self.assertIn("ठीक", tokens)
        # Stopwords 'की', 'और', 'करें' must be filtered out
        self.assertNotIn("की", tokens)
        self.assertNotIn("और", tokens)

    def test_chinese_stopword_filtering(self):
        # Chinese query with conversational filler
        tokens = core._tokens("请帮我 进行 代码审查 和 单元测试")
        self.assertIn("代码审查", tokens)
        self.assertIn("单元测试", tokens)
        self.assertNotIn("请", tokens)
        self.assertNotIn("帮", tokens)
        self.assertNotIn("和", tokens)

    def test_korean_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            d = root / "code-review"
            d.mkdir()
            (d / "SKILL.md").write_bytes(
                _skill_bytes("code-review", "Code review and inspection", aliases="코드리뷰, 코드검토")
            )
            catalog = core.scan_catalog({"local": root})
            selection = core.select_skill(catalog, "코드리뷰 부탁드립니다")
            self.assertEqual(selection.status, "selected")
            self.assertEqual(selection.selected.skill.name, "code-review")

    def test_multilingual_negation(self):
        # Chinese: 不要用 / 别用
        self.assertTrue(core._skill_is_negated("python-tdd", "不要用 python-tdd 做这个任务"))
        # Thai: ห้ามใช้ / อย่าใช้
        self.assertTrue(core._skill_is_negated("python-tdd", "ห้ามใช้ python-tdd ในงานนี้"))
        # English: don't use
        self.assertTrue(core._skill_is_negated("python-tdd", "don't use python-tdd for this"))


class HostBridgeGlobalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gemini_host_bridge(self):
        actions = bridge.init_host("gemini", self.target)
        gemini_md = self.target / "GEMINI.md"
        self.assertTrue(gemini_md.is_file())
        self.assertIn("Skill Proof", gemini_md.read_text(encoding="utf-8"))

        # Status check
        st = bridge.status(self.target)
        self.assertEqual(st["hosts"]["gemini"]["section"], "present")

        # Clean removal
        remove_actions = bridge.remove_host("gemini", self.target)
        self.assertIn("removed", "".join(remove_actions))

    def test_cline_and_windsurf_bridges(self):
        bridge.init_host("cline", self.target)
        self.assertTrue((self.target / ".clinerules").is_file())

        bridge.init_host("windsurf", self.target)
        self.assertTrue((self.target / ".windsurfrules").is_file())

        st = bridge.status(self.target)
        self.assertTrue(st["hosts"]["cline"]["present"])
        self.assertTrue(st["hosts"]["windsurf"]["present"])

    def test_auto_host_detection(self):
        # Create a marker for cursor in a new folder
        cursor_dir = self.target / ".cursor"
        cursor_dir.mkdir(parents=True)

        detected = bridge.detect_host_environments(self.target)
        self.assertIn("cursor", detected)

        # Run init with host 'auto'
        actions = bridge.init_host("auto", self.target)
        self.assertTrue(any("cursor" in a.lower() for a in actions))


class FolderStateResilienceTests(unittest.TestCase):
    def test_deep_nonexistent_target_auto_creates(self):
        deep_target = pathlib.Path(tempfile.gettempdir()) / "sp_test_deep" / "nested" / "project"
        try:
            actions = bridge.init_host("codex", deep_target)
            self.assertTrue((deep_target / "AGENTS.md").is_file())
        finally:
            import shutil
            shutil.rmtree(pathlib.Path(tempfile.gettempdir()) / "sp_test_deep", ignore_errors=True)

    def test_unreadable_and_binary_files_in_catalog_produce_diagnostics_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bad_dir = root / "corrupt-skill"
            bad_dir.mkdir()
            # Write completely broken binary bytes that cannot be decoded as utf-8
            (bad_dir / "SKILL.md").write_bytes(b"\x80\x81\xff\xfe\x00\x01\xfa")

            # Must not raise UnicodeDecodeError or crash
            catalog = core.scan_catalog({"local": root})
            self.assertTrue(any(d.code in ("invalid_utf8", "unreadable_file", "invalid_frontmatter") for d in catalog.diagnostics))


if __name__ == "__main__":
    unittest.main()
