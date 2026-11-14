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


if __name__ == "__main__":
    unittest.main()
