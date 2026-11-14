import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from skill_proof.core import SkillProofEngine
from skill_proof._proof import ExecutionEvent, evaluate_contract, parse_contract
from tests.test_plugin import FakeContext, plugin_module


TDD_CONTRACT = {
    "schema_version": "skill-proof.contract.v1",
    "skill": "python-tdd",
    "requirements": [
        {
            "id": "TEST_EXECUTED",
            "description": "Run tests via terminal with exit code 0",
            "must_pass": True,
            "verifier": "tool_result",
            "tool": "terminal",
            "expected": {"exit_code": 0},
        },
        {
            "id": "TEST_BEFORE_WRITE",
            "description": "Tests must pass before editing source",
            "must_pass": True,
            "verifier": "event_order",
            "before": "terminal:success",
            "after": "write_file",
        },
        {
            "id": "SOURCE_CHANGED",
            "description": "Modify target file under src/",
            "must_pass": True,
            "verifier": "artifact_diff",
            "path": "src/",
        },
    ],
}


class ProofEvaluatorTests(unittest.TestCase):
    def test_no_tools_called_yields_needs_review(self):
        contract = parse_contract(TDD_CONTRACT)
        report = evaluate_contract(contract, [])
        self.assertEqual(report["verdict"], "NEEDS_REVIEW")
        self.assertEqual(report["verified_count"], 0)
        self.assertEqual(report["total_count"], 3)
        self.assertTrue(all(r["status"] == "unverified" for r in report["requirements"]))

    def test_failed_tool_yields_fail(self):
        contract = parse_contract(TDD_CONTRACT)
        events = [
            ExecutionEvent("evt_001", "ev_001", "s:k:c1", "terminal", "post", "failed", exit_code=1),
        ]
        report = evaluate_contract(contract, events)
        self.assertEqual(report["verdict"], "FAIL")
        req0 = report["requirements"][0]
        self.assertEqual(req0["id"], "TEST_EXECUTED")
        self.assertEqual(req0["status"], "failed")
        self.assertEqual(req0["evidence_refs"], ["ev_001"])

    def test_wrong_target_path_never_passes(self):
        contract = parse_contract(TDD_CONTRACT)
        events = [
            ExecutionEvent("evt_001", "ev_001", "s:k:c1", "terminal", "post", "success", exit_code=0),
            ExecutionEvent("evt_002", "ev_002", "s:k:c2", "write_file", "post", "success", path="docs/notes.md"),
        ]
        report = evaluate_contract(contract, events)
        self.assertEqual(report["verdict"], "NEEDS_REVIEW")
        self.assertEqual(report["verified_count"], 2)
        src_req = next(r for r in report["requirements"] if r["id"] == "SOURCE_CHANGED")
        self.assertEqual(src_req["status"], "unverified")

    def test_order_violation_write_before_test_yields_fail(self):
        contract = parse_contract(TDD_CONTRACT)
        events = [
            ExecutionEvent("evt_001", "ev_001", "s:k:c1", "write_file", "post", "success", path="src/app.py"),
            ExecutionEvent("evt_002", "ev_002", "s:k:c2", "terminal", "post", "success", exit_code=0),
        ]
        report = evaluate_contract(contract, events)
        self.assertEqual(report["verdict"], "FAIL")
        order_req = next(r for r in report["requirements"] if r["id"] == "TEST_BEFORE_WRITE")
        self.assertEqual(order_req["status"], "failed")

    def test_partial_completion_yields_needs_review(self):
        contract = parse_contract(TDD_CONTRACT)
        events = [
            ExecutionEvent("evt_001", "ev_001", "s:k:c1", "terminal", "post", "success", exit_code=0),
        ]
        report = evaluate_contract(contract, events)
        self.assertEqual(report["verdict"], "NEEDS_REVIEW")
        self.assertEqual(report["verified_count"], 1)
        self.assertEqual(report["total_count"], 3)

    def test_all_requirements_satisfied_yields_pass_with_evidence_refs(self):
        contract = parse_contract(TDD_CONTRACT)
        events = [
            ExecutionEvent("evt_001", "ev_001", "s:k:c1", "terminal", "post", "success", exit_code=0),
            ExecutionEvent("evt_002", "ev_002", "s:k:c2", "write_file", "post", "success", path="src/auth.py"),
        ]
        report = evaluate_contract(contract, events)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["verified_count"], 3)
        self.assertEqual(report["total_count"], 3)
        self.assertEqual(report["requirements"][0]["evidence_refs"], ["ev_001"])
        self.assertEqual(report["requirements"][1]["evidence_refs"], ["ev_001", "ev_002"])
        self.assertEqual(report["requirements"][2]["evidence_refs"], ["ev_002"])


class ProofEngineAndPluginTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="skill_proof_phase1_")
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "python-tdd").mkdir()
        (self.root / "python-tdd" / "SKILL.md").write_text(
            "---\n"
            "name: python-tdd\n"
            "description: Python TDD workflow with deterministic proof contract\n"
            f"proof_contract: {json.dumps(TDD_CONTRACT)}\n"
            "---\n"
            "Test first, then edit source.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_frontmatter_contract_evaluated_through_engine_and_command(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        ctx.hooks["pre_llm_call"](
            session_id="s1", task_id="k1", turn_id="t1",
            user_message="Use $python-tdd to fix src/auth.py",
        )
        ctx.hooks["on_skill_lifecycle"](
            action="loaded", session_id="s1", task_id="k1",
            skill_name="python-tdd", provenance="local",
        )
        ctx.hooks["pre_tool_call"](
            session_id="s1", task_id="k1", turn_id="t1",
            tool_name="terminal", tool_call_id="call_1",
            args={"command": "pytest"},
        )
        ctx.hooks["post_tool_call"](
            session_id="s1", task_id="k1", turn_id="t1",
            tool_name="terminal", tool_call_id="call_1",
            args={"command": "pytest"},
            result=json.dumps({"exit_code": 0, "output": "ok"}),
            status="success",
        )
        ctx.hooks["pre_tool_call"](
            session_id="s1", task_id="k1", turn_id="t1",
            tool_name="write_file", tool_call_id="call_2",
            args={"path": "src/auth.py", "content": "x"},
        )
        ctx.hooks["post_tool_call"](
            session_id="s1", task_id="k1", turn_id="t1",
            tool_name="write_file", tool_call_id="call_2",
            args={"path": "src/auth.py", "content": "x"},
            result=json.dumps({"success": True}),
            status="success",
        )

        receipt = plugin.engine.receipt(turn_id="t1")
        self.assertEqual(receipt["verification"], "verified")
        self.assertEqual(receipt["proof"]["verdict"], "PASS")
        self.assertEqual(receipt["proof"]["verified_count"], 3)

        report_text = plugin.handle_command("proof")
        self.assertIn("PASS", report_text)
        self.assertIn("3 / 3", report_text)
        self.assertIn("TEST_EXECUTED", report_text)
        self.assertIn("ev_002", report_text)

    def test_frontmatter_contract_failed_tool_reports_fail_via_command(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        ctx.hooks["pre_llm_call"](
            session_id="s2", task_id="k2", turn_id="t2",
            user_message="Use $python-tdd to fix src/auth.py",
        )
        ctx.hooks["on_skill_lifecycle"](
            action="loaded", session_id="s2", task_id="k2",
            skill_name="python-tdd", provenance="local",
        )
        ctx.hooks["pre_tool_call"](
            session_id="s2", task_id="k2", turn_id="t2",
            tool_name="terminal", tool_call_id="call_1",
            args={"command": "pytest"},
        )
        ctx.hooks["post_tool_call"](
            session_id="s2", task_id="k2", turn_id="t2",
            tool_name="terminal", tool_call_id="call_1",
            args={"command": "pytest"},
            result=json.dumps({"exit_code": 1, "output": "1 failed"}),
            status="failed",
        )

        receipt = plugin.engine.receipt(turn_id="t2")
        self.assertEqual(receipt["verification"], "failed")
        self.assertEqual(receipt["proof"]["verdict"], "FAIL")
        report_text = plugin.handle_command("proof")
        self.assertIn("Verdict: FAIL", report_text)
        self.assertIn("[FAILED] TEST_EXECUTED", report_text)


class Phase2And3GoalRunAndClaimGuardTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="skill_proof_phase23_")
        self.root = pathlib.Path(self.tempdir.name)
        (self.root / "python-tdd").mkdir()
        (self.root / "python-tdd" / "SKILL.md").write_text(
            "---\n"
            "name: python-tdd\n"
            "description: Python TDD workflow with deterministic proof contract\n"
            f"proof_contract: {json.dumps(TDD_CONTRACT)}\n"
            "---\n"
            "Test first, then edit source.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_multi_turn_goal_run_accumulates_and_resets_cleanly(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        # Turn 1: run test
        ctx.hooks["pre_llm_call"](session_id="s_mt", task_id="k1", turn_id="t1", user_message="Use $python-tdd")
        ctx.hooks["on_skill_lifecycle"](action="loaded", session_id="s_mt", task_id="k1", skill_name="python-tdd", provenance="local")
        ctx.hooks["pre_tool_call"](session_id="s_mt", task_id="k1", turn_id="t1", tool_name="terminal", tool_call_id="c1", args={"command": "pytest"})
        ctx.hooks["post_tool_call"](session_id="s_mt", task_id="k1", turn_id="t1", tool_name="terminal", tool_call_id="c1", args={"command": "pytest"}, result=json.dumps({"exit_code": 0}), status="success")
        ctx.hooks["post_llm_call"](session_id="s_mt", task_id="k1", turn_id="t1")

        # Turn 2: edit src/auth.py (no explicit $python-tdd mention; continues GoalRun)
        ctx.hooks["pre_llm_call"](session_id="s_mt", task_id="k2", turn_id="t2", user_message="Now edit src/auth.py")
        ctx.hooks["pre_tool_call"](session_id="s_mt", task_id="k2", turn_id="t2", tool_name="write_file", tool_call_id="c2", args={"path": "src/auth.py"})
        ctx.hooks["post_tool_call"](session_id="s_mt", task_id="k2", turn_id="t2", tool_name="write_file", tool_call_id="c2", args={"path": "src/auth.py"}, result=json.dumps({"success": True}), status="success")
        ctx.hooks["post_llm_call"](session_id="s_mt", task_id="k2", turn_id="t2")

        receipt2 = plugin.engine.receipt(turn_id="t2")
        self.assertEqual(receipt2["proof"]["verdict"], "PASS")
        self.assertEqual(receipt2["proof"]["turn_count"], 2)
        run1_id = receipt2["proof"]["run_id"]
        self.assertTrue(run1_id.startswith("run_"))
        self.assertIn(f"Run: {run1_id} (turns: 2)", plugin.handle_command("proof"))

        # Stale evidence guard: reset GoalRun so old test does not leak into Turn 3
        self.assertIn("GoalRun closed", plugin.handle_command("reset"))
        ctx.hooks["pre_llm_call"](session_id="s_mt", task_id="k3", turn_id="t3", user_message="Use $python-tdd again")
        ctx.hooks["pre_tool_call"](session_id="s_mt", task_id="k3", turn_id="t3", tool_name="write_file", tool_call_id="c3", args={"path": "src/auth.py"})
        ctx.hooks["post_tool_call"](session_id="s_mt", task_id="k3", turn_id="t3", tool_name="write_file", tool_call_id="c3", args={"path": "src/auth.py"}, result=json.dumps({"success": True}), status="success")
        receipt3 = plugin.engine.receipt(turn_id="t3")
        self.assertNotEqual(receipt3["proof"]["run_id"], run1_id)
        self.assertEqual(receipt3["proof"]["verdict"], "FAIL")

    def test_concurrent_unmatched_call_id_prevents_false_pass(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        ctx.hooks["pre_llm_call"](session_id="s_conc", task_id="k1", turn_id="t1", user_message="Use $python-tdd")
        ctx.hooks["pre_tool_call"](session_id="s_conc", task_id="k1", turn_id="t1", tool_name="terminal", tool_call_id="p1", args={"command": "pytest"})
        ctx.hooks["pre_tool_call"](session_id="s_conc", task_id="k1", turn_id="t1", tool_name="read_file", tool_call_id="p2", args={"path": "src/auth.py"})
        # Host drops tool_call_id while 2 calls are concurrently pending
        ctx.hooks["post_tool_call"](session_id="s_conc", task_id="k1", turn_id="t1", tool_name="terminal", tool_call_id="", args={"command": "pytest"}, result=json.dumps({"exit_code": 0}), status="success")
        ctx.hooks["post_tool_call"](session_id="s_conc", task_id="k1", turn_id="t1", tool_name="read_file", tool_call_id="p2", args={"path": "src/auth.py"}, result=json.dumps({"success": True}), status="success")
        ctx.hooks["pre_tool_call"](session_id="s_conc", task_id="k1", turn_id="t1", tool_name="write_file", tool_call_id="p3", args={"path": "src/auth.py"})
        ctx.hooks["post_tool_call"](session_id="s_conc", task_id="k1", turn_id="t1", tool_name="write_file", tool_call_id="p3", args={"path": "src/auth.py"}, result=json.dumps({"success": True}), status="success")

        receipt = plugin.engine.receipt(turn_id="t1")
        self.assertEqual(receipt["proof"]["verdict"], "NEEDS_REVIEW")
        self.assertIn("unmatched_concurrent:terminal", receipt["proof"]["unmatched_calls"])

    def test_stale_run_id_events_are_ignored_by_evaluate_contract(self):
        contract = parse_contract(TDD_CONTRACT)
        events = [
            ExecutionEvent("evt_001", "ev_001", "c1", "terminal", "post", "success", exit_code=0, run_id="run_old"),
            ExecutionEvent("evt_002", "ev_002", "c2", "write_file", "post", "success", path="src/app.py", run_id="run_new"),
        ]
        report = evaluate_contract(contract, events, run_id="run_new")
        self.assertEqual(report["verdict"], "FAIL")

    def test_claim_contradiction_guard_catches_false_success_and_allows_honest_failure(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        ctx.hooks["pre_llm_call"](session_id="s_claim", task_id="k1", turn_id="t1", user_message="Use $python-tdd")
        ctx.hooks["pre_tool_call"](session_id="s_claim", task_id="k1", turn_id="t1", tool_name="terminal", tool_call_id="c1", args={"command": "pytest"})
        ctx.hooks["post_tool_call"](session_id="s_claim", task_id="k1", turn_id="t1", tool_name="terminal", tool_call_id="c1", args={"command": "pytest"}, result=json.dumps({"exit_code": 1}), status="failed")

        # Honest report of failure must NOT trigger contradiction
        honest = ctx.hooks["transform_llm_output"](session_id="s_claim", response_text="Tests failed, bug is not fixed yet.")
        self.assertNotIn("CLAIM CONTRADICTION", honest)

        # False success claim MUST trigger contradiction warning and show in /skill-proof proof
        dishonest = ctx.hooks["transform_llm_output"](session_id="s_claim", response_text="All tests passed and the bug is fixed!")
        self.assertIn("CLAIM CONTRADICTION", dishonest)
        self.assertIn("/skill-proof proof", dishonest)
        self.assertIn("Claim Check: CONTRADICTED", plugin.handle_command("proof"))

    def test_thai_claim_contradiction_caught_on_needs_review(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        ctx.hooks["pre_llm_call"](session_id="s_th", task_id="k1", turn_id="t1", user_message="Use $python-tdd")
        dishonest_th = ctx.hooks["transform_llm_output"](session_id="s_th", response_text="ทดสอบผ่านหมดแล้ว แก้เรียบร้อย")
        self.assertIn("CLAIM CONTRADICTION", dishonest_th)

    def test_reset_only_closes_current_sessions_goal_run(self):
        ctx = FakeContext({"skill_roots": [str(self.root)], "mode": "nudge"})
        plugin = plugin_module.register(ctx)

        ctx.hooks["pre_llm_call"](session_id="sess_A", task_id="k1", turn_id="tA", user_message="Use $python-tdd")
        ctx.hooks["pre_tool_call"](session_id="sess_A", task_id="k1", turn_id="tA", tool_name="terminal", tool_call_id="cA", args={"command": "pytest"})
        ctx.hooks["post_tool_call"](session_id="sess_A", task_id="k1", turn_id="tA", tool_name="terminal", tool_call_id="cA", args={"command": "pytest"}, result=json.dumps({"exit_code": 0}), status="success")

        ctx.hooks["pre_llm_call"](session_id="sess_B", task_id="k1", turn_id="tB", user_message="Use $python-tdd")
        self.assertIn("GoalRun closed", plugin.handle_command("reset"))
        self.assertIn("sess_A", plugin.engine._goal_runs)
        self.assertNotIn("sess_B", plugin.engine._goal_runs)


if __name__ == "__main__":
    unittest.main()
