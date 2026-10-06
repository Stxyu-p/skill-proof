"""Optional integration smoke. Run with Hermes source on PYTHONPATH.

Uses Doctor's isolated runtime only for testing, never from plugin code.
"""
import json
import pathlib
from hermes_cli.plugin_dev import _doctor_runtime
from hermes_constants import get_hermes_home


def run():
    with _doctor_runtime(pathlib.Path(__file__).resolve().parent) as runtime:
        import tools.skills_tool  # register the real built-in tools
        root = get_hermes_home() / 'skills' / 'smoke-skill'
        root.mkdir(parents=True)
        (root / 'SKILL.md').write_text(
            '---\nname: smoke-skill\ndescription: >-\n  Smoke testing\n  workflow\n---\nTest instructions.\n',
            encoding='utf-8')
        hidden = root.parent / 'hidden-skill'
        hidden.mkdir()
        (hidden / 'SKILL.md').write_text(
            '---\nname: hidden-skill\ndescription: Disabled testing workflow\n---\nInstructions.\n', encoding='utf-8')
        (get_hermes_home() / 'config.yaml').write_text(
            json.dumps({'skills': {'disabled': ['hidden-skill']}}), encoding='utf-8')
        outputs = runtime.manager.invoke_hook(
            'pre_llm_call', session_id='smoke-session', task_id='smoke-task', turn_id='smoke-turn',
            user_message='Use $smoke-skill')
        assert any(isinstance(item, dict) and 'skill_view' in item.get('context', '') for item in outputs), outputs
        from tools.registry import registry
        viewed = registry.dispatch('skill_view', {'name': 'smoke-skill'}, scope=runtime.manager.scope_key,
                                   task_id='smoke-task', session_id='smoke-session')
        assert json.loads(viewed).get('success') is True, viewed
        runtime.manager.invoke_hook('post_tool_call', session_id='smoke-session', task_id='smoke-task',
            turn_id='smoke-turn', tool_name='skill_view', args={'name':'smoke-skill'}, result=viewed, status='success')
        command = runtime.manager._plugin_commands['skill-proof']['handler']
        receipt = json.loads(command('trace'))
        assert receipt['evidence']['source_path_match'] == 'match', receipt['evidence']
        health = json.loads(command('health'))
        assert health['performance']['total_ms'] >= health['performance']['listing_ms']
        hidden_outputs = runtime.manager.invoke_hook(
            'pre_llm_call', session_id='smoke-session', task_id='smoke-task', turn_id='hidden-turn',
            user_message='Use $hidden-skill')
        assert any(isinstance(item, dict) and 'unknown_explicit_skill' in item.get('context', '') for item in hidden_outputs), hidden_outputs
        print(json.dumps({'real_host_skills_list': 'PASS', 'local_skill_selected': 'PASS',
                          'disabled_skill_excluded': 'PASS',
                          'multiline_and_source_path': 'PASS', 'performance': health['performance'],
                          'network': 'blocked by Doctor', 'live_profile_modified': False}))


if __name__ == '__main__':
    run()
