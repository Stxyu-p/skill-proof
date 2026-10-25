from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import importlib.util
import sys

_package_path = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_proof_test_package", _package_path / "__init__.py",
    submodule_search_locations=[str(_package_path)],
)
plugin_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = plugin_module
_spec.loader.exec_module(plugin_module)


def _skill_bytes(name: str, description: str) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        "Follow this workflow exactly.\n"
    ).encode("utf-8")


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeContext:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.settings.setdefault("audit_log", False)
        self.hooks = {}
        self.middleware = {}
        self.commands = {}
        self.state = FakeState()

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def dispatch_tool(self, name, args):
        assert name == 'skills_list'
        return json.dumps({'success': True, 'skills': [{'name': 'python-tdd'}]})

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_middleware(self, name, callback):
        self.middleware[name] = callback

    def register_command(self, name, handler, **metadata):
        self.commands[name] = (handler, metadata)


class PluginAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_plugin_")
        self.root = pathlib.Path(self.tmp.name)
        skill_dir = self.root / "python-tdd"
        skill_dir.mkdir()
        self.raw_skill = _skill_bytes("python-tdd", "Write Python tests before implementation")
        (skill_dir / "SKILL.md").write_bytes(self.raw_skill)

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, **settings):
        base = {"skill_roots": [str(self.root)], "visible_receipt": True, "receipt_style": "verbose"}
        base.update(settings)
        return FakeContext(base)

    def test_fake_context_disables_audit_by_default(self):
        instance = plugin_module.SkillProofPlugin(self.context())
        self.assertFalse(instance.audit_enabled)

    def test_on_pre_llm_call_preserves_observe_and_focus_fallback_context_and_budget(self):
        observe = plugin_module.SkillProofPlugin(self.context(mode="observe")).on_pre_llm_call(
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd",
        )
        self.assertIn("Skill Proof candidate", observe["context"])
        self.assertIn("python-tdd", observe["context"])

        budgeted = plugin_module.SkillProofPlugin(self.context(context_budget_bytes=40)).on_pre_llm_call(
            session_id="s1", task_id="task1", turn_id="turn2",
            user_message="Use $python-tdd",
        )
        self.assertEqual(budgeted["context"], 'Use skill_view for "python-tdd" first.')
        self.assertLessEqual(len(budgeted["context"].encode("utf-8")), 40)

        tiny = plugin_module.SkillProofPlugin(self.context(context_budget_bytes=20)).on_pre_llm_call(
            session_id="s1", task_id="task1", turn_id="turn3",
            user_message="Use $python-tdd",
        )
        self.assertIsNone(tiny, "an over-tight budget must inject nothing, not the unbounded message")

        plugin = plugin_module.SkillProofPlugin(self.context())
        plugin.on_pre_llm_call(
            session_id="s1", task_id="task1", turn_id="turn4",
            user_message="keep using python-tdd",
        )
        fallback = plugin.on_pre_llm_call(
            session_id="s1", task_id="task2", turn_id="turn5",
            user_message="book a flight to Chiang Mai",
        )
        self.assertIn("score is 0", fallback["context"])

    def test_register_declares_complete_runtime_surface(self):
        ctx = self.context()

        instance = plugin_module.register(ctx)

        self.assertIsInstance(instance, plugin_module.SkillProofPlugin)
        self.assertEqual(
            set(ctx.hooks),
            {
                "pre_llm_call",
                "pre_tool_call",
                "post_tool_call",
                "transform_llm_output",
                "post_llm_call",
                "on_skill_lifecycle",
                "on_session_end",
            },
        )
        self.assertIn("skill-proof", ctx.commands)
        self.assertEqual(ctx.middleware, {})

    def test_registers_request_middleware_only_when_prompt_reduction_is_enabled(self):
        ctx = self.context(reduce_skill_prompt=True)

        plugin_module.register(ctx)

        self.assertIn("llm_request", ctx.middleware)

    def test_reduces_skill_index_to_selected_skill_and_records_character_savings(self):
        ctx = self.context(reduce_skill_prompt=True)
        plugin = plugin_module.register(ctx)
        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd",
        )
        original_system = (
            "stable instructions\n<available_skills>\n"
            "  - python-tdd: Write Python tests before implementation\n"
            "  - unrelated: A long unrelated skill description\n"
            "</available_skills>\nkeep this ending"
        )
        request = {
            "messages": [
                {"role": "system", "content": original_system},
                {"role": "user", "content": "Use $python-tdd"},
            ],
            "tools": [{"name": "skill_view"}],
        }

        result = ctx.middleware["llm_request"](
            request=request, turn_id="turn1", session_id="s1",
        )

        rewritten = result["request"]["messages"][0]["content"]
        self.assertIn("stable instructions", rewritten)
        self.assertIn("python-tdd", rewritten)
        self.assertNotIn("unrelated", rewritten)
        self.assertIn("keep this ending", rewritten)
        self.assertEqual(request["messages"][0]["content"], original_system)
        self.assertEqual(result["reason"], f"skill_index_chars_removed={len(original_system) - len(rewritten)}")
        receipt = plugin.engine.receipt(turn_id="turn1")
        self.assertEqual(
            receipt["performance"]["skill_index_chars_removed"],
            len(original_system) - len(rewritten),
        )

    def test_prompt_reduction_is_noop_without_confident_selection(self):
        ctx = self.context(reduce_skill_prompt=True)
        plugin_module.register(ctx)
        request = {"instructions": "<available_skills>\n- all skills\n</available_skills>"}

        result = ctx.middleware["llm_request"](
            request=request, turn_id="missing-turn", session_id="s1",
        )

        self.assertIsNone(result)
        self.assertIn("all skills", request["instructions"])

    def test_prompt_reduction_preserves_provider_text_block_metadata(self):
        request = {
            "system": [
                {
                    "type": "text",
                    "text": (
                        "before <available_skills>many unrelated skill descriptions with substantial text"
                        "</available_skills> after"
                    ),
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "image", "source": {"type": "base64", "data": "unchanged"}},
            ]
        }

        rewritten, removed = plugin_module._rewrite_llm_request(request, "python-tdd")

        self.assertGreater(removed, 0)
        self.assertIn("python-tdd", rewritten["system"][0]["text"])
        self.assertEqual(rewritten["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(rewritten["system"][1], request["system"][1])
        self.assertIn("many unrelated skill descriptions", request["system"][0]["text"])

    def test_hook_flow_blocks_tools_then_accepts_authoritative_load(self):
        ctx = self.context(mode="enforce-tools")
        plugin_module.register(ctx)

        context = ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd for this change",
        )
        blocked = ctx.hooks["pre_tool_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            tool_name="terminal", args={},
        )
        skill_allowed = ctx.hooks["pre_tool_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            tool_name="skill_view", args={"name": "python-tdd"},
        )
        ctx.hooks["on_skill_lifecycle"](
            action="loaded", session_id="s1", task_id="task1",
            skill_name="python-tdd", provenance="local", use_count=2,
            reused=False, reuse_after_patch=False,
        )
        allowed = ctx.hooks["pre_tool_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            tool_name="terminal", args={},
        )

        self.assertIn("skill_view", context["context"])
        self.assertEqual(blocked["action"], "block")
        self.assertIsNone(skill_allowed)
        self.assertIsNone(allowed)

    def test_enforcement_callback_errors_fail_closed(self):
        ctx = self.context(mode="enforce-tools")
        instance = plugin_module.register(ctx)
        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd",
        )

        with mock.patch.object(instance.engine, "guard_tool", side_effect=RuntimeError("boom")):
            result = ctx.hooks["pre_tool_call"](
                session_id="s1", task_id="task1", turn_id="turn1",
                tool_name="terminal", args={},
            )

        self.assertEqual(result["action"], "block")
        self.assertIn("internal policy error", result["message"])

    def test_nudge_callback_errors_fail_open(self):
        ctx = self.context(mode="nudge")
        instance = plugin_module.register(ctx)

        with mock.patch.object(instance.engine, "guard_tool", side_effect=RuntimeError("boom")):
            result = ctx.hooks["pre_tool_call"](
                session_id="s1", task_id="task1", turn_id="turn1",
                tool_name="terminal", args={},
            )

        self.assertIsNone(result)

    def test_post_tool_hash_and_visible_footer_are_truthful(self):
        ctx = self.context(mode="enforce-tools")
        instance = plugin_module.register(ctx)
        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd",
        )
        ctx.hooks["on_skill_lifecycle"](
            action="loaded", session_id="s1", task_id="task1",
            skill_name="python-tdd", provenance="local",
        )
        raw_result = json.dumps({"success": True, "content": "instructions"})
        ctx.hooks["post_tool_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            tool_name="skill_view", args={"name": "python-tdd"},
            result=raw_result, status="success",
        )
        ctx.hooks["pre_tool_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            tool_name="write_file", args={},
        )

        transformed = ctx.hooks["transform_llm_output"](
            session_id="s1", response_text="Done.", model="test", platform="cli",
        )
        receipt = instance.engine.receipt(turn_id="turn1")

        self.assertTrue(transformed.startswith("Done."))
        self.assertIn("loaded=yes", transformed)
        self.assertIn("active=yes", transformed)
        self.assertIn("compliance=compliant", transformed)
        self.assertIn("verification=unverified", transformed)
        self.assertNotIn("model read", transformed.casefold())
        self.assertEqual(
            receipt["evidence"]["tool_result_sha256_pre_transform"],
            hashlib.sha256(raw_result.encode("utf-8")).hexdigest(),
        )

    def test_no_match_does_not_add_visible_footer(self):
        ctx = self.context()
        plugin_module.register(ctx)
        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Book a dinner table",
        )

        transformed = ctx.hooks["transform_llm_output"](
            session_id="s1", response_text="Done.",
        )

        self.assertIsNone(transformed)

    def test_post_llm_persists_bounded_receipt_and_trace_has_no_sensitive_text(self):
        ctx = self.context(receipt_history_limit=2)
        plugin_module.register(ctx)
        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd secret marker XYZ",
        )

        ctx.hooks["post_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            assistant_response="Done.",
        )
        trace_handler = ctx.commands["skill-proof"][0]
        trace = trace_handler("trace")

        self.assertEqual(len(ctx.state.data["receipts"]), 1)
        self.assertTrue(ctx.state.data["receipts"][0]["complete"])
        self.assertNotIn("XYZ", trace)
        self.assertNotIn(str(self.root), trace)
        self.assertIn("skill-proof.receipt.v1", trace)

    def test_status_and_explain_commands_are_human_readable(self):
        ctx = self.context()
        plugin_module.register(ctx)
        handler = ctx.commands["skill-proof"][0]
        self.assertIn("No Skill Proof receipt", handler("status"))

        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="task1", turn_id="turn1",
            user_message="Use $python-tdd",
        )

        self.assertIn("selected=python-tdd", handler("status"))
        self.assertIn("explicit_skill", handler("explain"))
        self.assertIn("Usage:", handler("unknown"))

    def test_default_root_uses_hermes_home_without_importing_host_internals(self):
        ctx = FakeContext()
        fake_home = self.root / "profile"

        with mock.patch.dict("os.environ", {"HERMES_HOME": str(fake_home)}, clear=False):
            instance = plugin_module.SkillProofPlugin(ctx)

        self.assertEqual(instance.roots, {"profile": fake_home / "skills"})

    def test_host_filter_excludes_unlisted_skill(self):
        ctx = self.context()
        instance = plugin_module.register(ctx)
        with mock.patch.object(ctx, 'dispatch_tool', return_value='{"success": true, "skills": []}'):
            instance.on_pre_llm_call(session_id='s', task_id='t', turn_id='1', user_message='Use $python-tdd')
        self.assertIsNone(instance.engine.receipt(turn_id='1')['selected'])

    def test_host_filter_excludes_lexical_match_for_disabled_skill(self):
        # A disabled skill is absent from skills_list: even a lexical query
        # that would otherwise select it must not select anything, and the
        # receipt must record the exclusion (not_in_host_list), not a pass.
        ctx = self.context()
        instance = plugin_module.register(ctx)
        with mock.patch.object(ctx, 'dispatch_tool', return_value='{"success": true, "skills": []}'):
            instance.on_pre_llm_call(session_id='s', task_id='t', turn_id='1', user_message='Write Python tests before implementation')
        receipt = instance.engine.receipt(turn_id='1')
        self.assertIsNone(receipt['selected'])
        self.assertIn('not_in_host_list', receipt['catalog']['diagnostic_counts'])

    def test_host_error_visible_and_enforcement_closed(self):
        ctx = self.context(mode='enforce-tools')
        instance = plugin_module.register(ctx)
        with mock.patch.object(ctx, 'dispatch_tool', return_value='{"success": false}'):
            instance.on_pre_llm_call(session_id='s', task_id='t', turn_id='1', user_message='Use $python-tdd')
        self.assertIn('hermes_catalog_unavailable', instance.handle_command('trace'))
        self.assertEqual(instance.on_pre_tool_call(session_id='s', task_id='t', turn_id='1', tool_name='terminal')['action'], 'block')

    def test_compact_footer_preserves_honest_proof_limit(self):
        instance = plugin_module.register(self.context(receipt_style='compact'))
        instance.on_pre_llm_call(session_id='s', task_id='t', turn_id='1', user_message='Use $python-tdd')
        instance.on_skill_lifecycle(action='loaded', session_id='s', task_id='t', skill_name='python-tdd')
        footer = instance.on_transform_llm_output(response_text='Done.', session_id='s')
        self.assertIn('python-tdd', footer)
        self.assertIn('loaded', footer)
        self.assertIn('compliance compliant', footer)
        self.assertNotIn('active=', footer)

    def test_refresh_command_does_not_rewrite_receipt(self):
        instance = plugin_module.register(self.context())
        instance.on_pre_llm_call(session_id='s', task_id='t', turn_id='1', user_message='Use $python-tdd')
        before = instance.handle_command('trace')
        self.assertIn('next turn', instance.handle_command('refresh'))
        self.assertEqual(before, instance.handle_command('trace'))


if __name__ == "__main__":
    unittest.main()
