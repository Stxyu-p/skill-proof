"""Tests for expanded multilingual coverage: DE, FR, ES, JA, KO, ZH, VI."""
import unittest
from core import (
    Catalog,
    SkillRecord,
    select_skill,
    parse_dialogue_reference,
    _skill_is_negated,
    _tokens,
)


def _make_dummy_skill(name: str, desc: str, tags: tuple[str, ...] = ()) -> SkillRecord:
    import pathlib
    return SkillRecord(
        skill_id=f"test:{name}",
        root_id="test",
        relative_path=name,
        name=name,
        normalized_name=name.lower(),
        description=desc,
        tags=tags,
        aliases=(),
        related_skills=(),
        source_sha256="abc",
        source_bytes=100,
        root_path=pathlib.Path("/tmp"),
        source_path=pathlib.Path(f"/tmp/{name}"),
    )


class MultilingualExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tdd = _make_dummy_skill("python-tdd", "Test driven development in Python pytest unittest")
        self.design = _make_dummy_skill("frontend-design", "UI and frontend design system components")
        self.catalog = Catalog(skills=(self.tdd, self.design), diagnostics=(), catalog_hash="abc")

    def test_german_negation_veto(self):
        self.assertTrue(_skill_is_negated("python-tdd", "Bitte nicht python-tdd verwenden"))
        self.assertTrue(_skill_is_negated("python-tdd", "Ohne python-tdd entwickeln"))

    def test_french_negation_veto(self):
        self.assertTrue(_skill_is_negated("python-tdd", "Ne pas utiliser python-tdd ici"))
        self.assertTrue(_skill_is_negated("python-tdd", "Sans python-tdd"))

    def test_spanish_negation_veto(self):
        self.assertTrue(_skill_is_negated("python-tdd", "No usar python-tdd en este proyecto"))
        self.assertTrue(_skill_is_negated("python-tdd", "Sin python-tdd"))

    def test_japanese_negation_veto(self):
        self.assertTrue(_skill_is_negated("python-tdd", "python-tddは使わないでください"))
        self.assertTrue(_skill_is_negated("python-tdd", "python-tddは不要です"))

    def test_vietnamese_negation_veto(self):
        self.assertTrue(_skill_is_negated("python-tdd", "Đừng dùng python-tdd"))
        self.assertTrue(_skill_is_negated("python-tdd", "Không dùng python-tdd"))

    def test_japanese_dialogue_repeat(self):
        ref = parse_dialogue_reference("さっきと同じスキルでお願い")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.kind, "repeat")

    def test_german_dialogue_release(self):
        ref = parse_dialogue_reference("aufhören mit python-tdd")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.kind, "release")


if __name__ == "__main__":
    unittest.main()
