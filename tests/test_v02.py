import pathlib
import tempfile
import unittest
from unittest.mock import patch
from core import SkillProofEngine


class V02Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.path = self.root / 'alpha' / 'SKILL.md'
        self.path.parent.mkdir()
        self.write('alpha')
        self.engine = SkillProofEngine({'test': self.root})

    def write(self, name):
        self.path.write_text(f'---\nname: {name}\ndescription: Testing workflow\n---\nInstructions.\n', encoding='utf-8')

    def begin(self, **kwargs):
        return self.engine.begin_turn(turn_id='1', session_id='s', task_id='t', query='Use $alpha', **kwargs)

    def test_cache_reuses_parse_but_detects_edit(self):
        self.begin()
        with patch('core._parse_skill', side_effect=AssertionError('reparsed')):
            self.assertEqual(self.begin().selection.status, 'selected')
        self.write('beta')
        self.assertEqual(self.begin().selection.status, 'blocked')

    def test_cache_detects_delete(self):
        self.begin()
        self.path.unlink()
        self.assertEqual(self.begin().selection.status, 'blocked')

    def test_cache_detects_new_skill(self):
        self.begin()
        other = self.root / 'beta'
        other.mkdir()
        (other / 'SKILL.md').write_text('---\nname: beta\ndescription: Another workflow\n---\nInstructions.\n', encoding='utf-8')
        turn = self.engine.begin_turn(turn_id='2', session_id='s', task_id='t', query='Use $beta')
        self.assertEqual(turn.selection.selected.skill.name, 'beta')

    def test_candidate_display_limit_cannot_hide_ambiguity(self):
        from core import scan_catalog, select_skill
        other = self.root / 'beta'
        other.mkdir()
        (other / 'SKILL.md').write_text('---\nname: beta\ndescription: Testing workflow\n---\nInstructions.\n', encoding='utf-8')
        result = select_skill(scan_catalog({'test': self.root}), 'Testing workflow', limit=1)
        self.assertEqual(result.status, 'ambiguous')
        self.assertEqual(len(result.candidates), 1)

    def test_refresh_clears_cache(self):
        self.begin()
        self.engine.refresh_catalog()
        import core
        with patch('core._parse_skill', wraps=core._parse_skill) as parse:
            self.begin()
        self.assertEqual(parse.call_count, 1)

    def test_host_unlisted_skill_not_selected(self):
        turn = self.begin(available_names=set())
        self.assertEqual(turn.selection.status, 'blocked')
        self.assertEqual(self.engine.receipt(turn_id='1')['catalog']['availability'], 'hermes_list')

    def test_host_listed_skill_selected(self):
        self.assertEqual(self.begin(available_names={'alpha'}).selection.status, 'selected')

    def test_completed_turns_are_bounded(self):
        engine = SkillProofEngine({'test': self.root}, retained_turn_limit=2)
        for i in range(5):
            engine.begin_turn(turn_id=str(i), session_id='s', task_id='t', query='Use $alpha')
            engine.finish_turn(turn_id=str(i))
        self.assertEqual(engine.receipt(turn_id='0'), {})
        self.assertTrue(engine.receipt(turn_id='4'))


if __name__ == '__main__':
    unittest.main()
