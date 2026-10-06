import json
import unittest
from tests import test_v02 as fixtures
from tests.test_plugin import FakeContext, plugin_module


class V03CoreTests(unittest.TestCase):
    setUp = fixtures.V02Tests.setUp
    write = fixtures.V02Tests.write
    begin = fixtures.V02Tests.begin
    def test_multiline_description_styles(self):
        for style in ('>', '>-', '>+', '|', '|-', '|+'):
            with self.subTest(style=style):
                self.path.write_text(f'---\nname: alpha\ndescription: {style}\n  Test Python\n  before implementation\n---\nInstructions.\n', encoding='utf-8')
                turn = self.begin()
                self.assertEqual(turn.selection.status, 'selected')
                self.assertIn('before implementation', turn.selection.selected.skill.description)

    def test_health_counts_cache_and_bounded_diagnostics(self):
        self.begin(available_names={'alpha', 'external'})
        first = self.engine.receipt(turn_id='1')
        self.assertEqual(first['catalog']['local_skill_count'], 1)
        self.assertEqual(first['catalog']['host_name_count'], 2)
        self.assertEqual(first['performance']['cache_misses'], 1)
        self.begin(available_names={'alpha'})
        self.assertEqual(self.engine.receipt(turn_id='1')['performance']['cache_hits'], 1)
        self.path.write_text('bad skill', encoding='utf-8')
        self.begin()
        catalog = self.engine.receipt(turn_id='1')['catalog']
        self.assertEqual(catalog['diagnostics'][0]['code'], 'invalid_frontmatter')
        self.assertNotIn(str(self.root), json.dumps(catalog))

    def test_source_path_identity_is_compared_without_storing_path(self):
        self.begin()
        for path, expected in ((self.path, 'match'), (self.root / 'other' / 'SKILL.md', 'mismatch')):
            self.engine.observe_tool_result(turn_id='1', session_id='s', task_id='t', tool_name='skill_view',
                args={'name':'alpha'}, result=json.dumps({'success':True, '_source_path':str(path)}), status='success')
            receipt = self.engine.receipt(turn_id='1')
            self.assertEqual(receipt['evidence']['source_path_match'], expected)
            self.assertNotIn(str(path), json.dumps(receipt))


class V03AdapterTests(unittest.TestCase):
    def test_health_before_first_turn_and_after_hook(self):
        ctx = FakeContext({'skill_roots': ['nonexistent-test-root']})
        plugin = plugin_module.register(ctx)
        self.assertIn('No turn observed', plugin.handle_command('health'))
        plugin.on_pre_llm_call(session_id='s', task_id='t', turn_id='1', user_message='hello')
        report = plugin.handle_command('health')
        self.assertIn('listing_ms', report)
        self.assertIn('missing_root', report)
        self.assertIn('total_ms', report)

    def test_hook_wrapper_records_timestamp(self):
        ctx = FakeContext({'skill_roots': ['nonexistent-test-root']})
        plugin_module.register(ctx)
        ctx.hooks['pre_llm_call'](session_id='s', task_id='t', turn_id='1', user_message='hello')
        report = ctx.commands['skill-proof'][0]('health')
        self.assertIn('pre_llm_call', report)
        self.assertIn('last_seen_utc', report)


if __name__ == '__main__':
    unittest.main()
