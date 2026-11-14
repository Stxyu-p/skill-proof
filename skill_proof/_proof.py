"""Deterministic Proof Contract schema and execution-evidence evaluator (Phase 1)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence


_MUTATION_TOOLS = frozenset({
    "write_file",
    "patch",
    "edit",
    "edit_file",
    "write_to_file",
    "replace_file_content",
    "skill_manage",
})


def _norm_id(value: Any) -> str:
    return str(value or "").strip().casefold()


def _norm_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


@dataclass(frozen=True)
class ProofRequirement:
    id: str
    description: str
    verifier: str
    must_pass: bool = True
    tool: Optional[str] = None
    expected_exit_code: Optional[int] = 0
    before: Optional[str] = None
    after: Optional[str] = None
    path: Optional[str] = None


@dataclass(frozen=True)
class ProofContract:
    schema_version: str = "skill-proof.contract.v1"
    skill: str = ""
    requirements: tuple[ProofRequirement, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.requirements


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    evidence_id: str
    call_id: str
    tool: str
    phase: str
    status: str
    exit_code: Optional[int] = None
    path: str = ""
    run_id: str = ""
    trust: str = "runtime_observed"


@dataclass
class GoalRun:
    run_id: str
    session_id: str
    skill: str
    contract: ProofContract
    turn_ids: list[str] = field(default_factory=list)
    events: list[ExecutionEvent] = field(default_factory=list)
    pending_calls: dict[str, ExecutionEvent] = field(default_factory=dict)
    unmatched_calls: list[str] = field(default_factory=list)
    status: str = "running"


def parse_contract(raw: Any, *, skill: str = "") -> ProofContract:
    """Parse a bounded proof contract dict or JSON string. Never raises."""
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return ProofContract(skill=str(skill or ""))
    if not isinstance(data, Mapping):
        return ProofContract(skill=str(skill or ""))
    raw_reqs = data.get("requirements")
    if not isinstance(raw_reqs, Sequence) or isinstance(raw_reqs, (str, bytes)):
        return ProofContract(
            schema_version=str(data.get("schema_version") or "skill-proof.contract.v1"),
            skill=str(data.get("skill") or skill or ""),
        )
    reqs: list[ProofRequirement] = []
    for item in raw_reqs[:32]:
        if not isinstance(item, Mapping):
            continue
        req_id = str(item.get("id") or "").strip()
        verifier = _norm_id(item.get("verifier"))
        if not req_id or not verifier:
            continue
        expected = item.get("expected")
        expected_exit: Optional[int] = 0 if verifier == "tool_result" else None
        if isinstance(expected, Mapping) and "exit_code" in expected:
            ec = expected.get("exit_code")
            expected_exit = ec if isinstance(ec, int) and not isinstance(ec, bool) else None
        elif "expected_exit_code" in item:
            ec = item.get("expected_exit_code")
            expected_exit = ec if isinstance(ec, int) and not isinstance(ec, bool) else None
        reqs.append(
            ProofRequirement(
                id=req_id,
                description=str(item.get("description") or req_id)[:200],
                verifier=verifier,
                must_pass=bool(item.get("must_pass", True)),
                tool=_norm_id(item.get("tool")) or None,
                expected_exit_code=expected_exit,
                before=_norm_id(item.get("before")) or None,
                after=_norm_id(item.get("after")) or None,
                path=str(item.get("path") or "").strip() or None,
            )
        )
    return ProofContract(
        schema_version=str(data.get("schema_version") or "skill-proof.contract.v1"),
        skill=str(data.get("skill") or skill or ""),
        requirements=tuple(reqs),
    )


def extract_event_path(args: Optional[Mapping[str, Any]]) -> str:
    """Extract a bounded target path string from tool args without content."""
    if not isinstance(args, Mapping):
        return ""
    for key in ("path", "file_path", "target_file", "filename"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().replace("\\", "/")[-240:]
    return ""


def _parse_event_spec(spec: Optional[str]) -> tuple[str, Optional[str]]:
    clean = _norm_id(spec)
    if ":" in clean:
        tool, st = clean.split(":", 1)
        return tool.strip(), st.strip() or None
    return clean, None


def _matches_event_spec(ev: ExecutionEvent, spec: Optional[str]) -> bool:
    tool, wanted_status = _parse_event_spec(spec)
    if not tool or _norm_id(ev.tool) != tool:
        return False
    if wanted_status is not None:
        return ev.phase == "post" and _norm_id(ev.status) == wanted_status
    return ev.status != "blocked"


def _eval_tool_result(req: ProofRequirement, events: Sequence[ExecutionEvent]) -> dict[str, Any]:
    tool = _norm_id(req.tool)
    post = [ev for ev in events if ev.phase == "post" and _norm_id(ev.tool) == tool]
    if not post:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "unverified",
            "evidence_refs": [],
            "reason": f"No execution result observed for tool '{tool}'",
        }
    refs = [ev.evidence_id for ev in post]
    if req.expected_exit_code is not None:
        matched_ok = [
            ev for ev in post
            if ev.status == "success" and ev.exit_code == req.expected_exit_code
        ]
        if matched_ok:
            return {
                "id": req.id,
                "description": req.description,
                "must_pass": req.must_pass,
                "status": "verified",
                "evidence_refs": [ev.evidence_id for ev in matched_ok],
                "reason": f"{tool} exited {req.expected_exit_code}",
            }
        explicit_fail = [
            ev for ev in post
            if ev.status == "failed" or (ev.exit_code is not None and ev.exit_code != req.expected_exit_code)
        ]
        if explicit_fail:
            return {
                "id": req.id,
                "description": req.description,
                "must_pass": req.must_pass,
                "status": "failed",
                "evidence_refs": [ev.evidence_id for ev in explicit_fail],
                "reason": f"{tool} failed or exited non-matching code",
            }
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "unverified",
            "evidence_refs": refs,
            "reason": f"{tool} succeeded without observed exit_code",
        }

    ok = [ev for ev in post if ev.status == "success"]
    if ok:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "verified",
            "evidence_refs": [ev.evidence_id for ev in ok],
            "reason": f"{tool} completed successfully",
        }
    failed = [ev for ev in post if ev.status == "failed"]
    return {
        "id": req.id,
        "description": req.description,
        "must_pass": req.must_pass,
        "status": "failed" if failed else "unverified",
        "evidence_refs": refs,
        "reason": f"{tool} execution failed" if failed else f"{tool} outcome unknown",
    }


def _eval_event_order(req: ProofRequirement, events: Sequence[ExecutionEvent]) -> dict[str, Any]:
    before_matches = [(i, ev) for i, ev in enumerate(events) if _matches_event_spec(ev, req.before)]
    after_matches = [(i, ev) for i, ev in enumerate(events) if _matches_event_spec(ev, req.after)]
    if not before_matches and not after_matches:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "unverified",
            "evidence_refs": [],
            "reason": f"Neither '{req.before}' nor '{req.after}' observed",
        }
    if after_matches and not before_matches:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "failed",
            "evidence_refs": [ev.evidence_id for _, ev in after_matches],
            "reason": f"'{req.after}' occurred before '{req.before}' was satisfied",
        }
    if before_matches and not after_matches:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "unverified",
            "evidence_refs": [ev.evidence_id for _, ev in before_matches],
            "reason": f"Waiting for '{req.after}' after '{req.before}'",
        }
    first_before_idx, first_before_ev = before_matches[0]
    first_after_idx, first_after_ev = after_matches[0]
    if first_before_idx < first_after_idx:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "verified",
            "evidence_refs": [first_before_ev.evidence_id, first_after_ev.evidence_id],
            "reason": f"{first_before_ev.event_id} -> {first_after_ev.event_id}",
        }
    return {
        "id": req.id,
        "description": req.description,
        "must_pass": req.must_pass,
        "status": "failed",
        "evidence_refs": [first_after_ev.evidence_id, first_before_ev.evidence_id],
        "reason": f"Order violated: {first_after_ev.event_id} occurred before {first_before_ev.event_id}",
    }


def _path_matches(target: str, observed: str) -> bool:
    t = _norm_path(target).lstrip("./")
    o = _norm_path(observed).lstrip("./")
    if not t or not o:
        return False
    if t.endswith("/"):
        return f"/{t}" in f"/{o}" or o.startswith(t)
    return o == t or o.endswith("/" + t) or (t in o.split("/"))


def _eval_artifact_diff(req: ProofRequirement, events: Sequence[ExecutionEvent]) -> dict[str, Any]:
    target = req.path or ""
    mutations = [
        ev for ev in events
        if ev.phase == "post" and _norm_id(ev.tool) in _MUTATION_TOOLS and ev.path
    ]
    matching = [ev for ev in mutations if _path_matches(target, ev.path)]
    ok = [ev for ev in matching if ev.status == "success"]
    if ok:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "verified",
            "evidence_refs": [ev.evidence_id for ev in ok],
            "reason": f"Modified path matching '{target}'",
        }
    failed = [ev for ev in matching if ev.status == "failed"]
    if failed:
        return {
            "id": req.id,
            "description": req.description,
            "must_pass": req.must_pass,
            "status": "failed",
            "evidence_refs": [ev.evidence_id for ev in failed],
            "reason": f"Modification failed for '{target}'",
        }
    return {
        "id": req.id,
        "description": req.description,
        "must_pass": req.must_pass,
        "status": "unverified",
        "evidence_refs": [],
        "reason": f"No verified modification observed matching '{target}'",
    }


def evaluate_contract(
    contract: ProofContract,
    events: Sequence[ExecutionEvent],
    *,
    run_id: str = "",
    unmatched_calls: Sequence[str] = (),
) -> dict[str, Any]:
    """Deterministically evaluate a ProofContract against runtime ExecutionEvents."""
    # Stale Evidence Guard: ignore events stamped with a different run_id.
    scoped_events = [
        ev for ev in events
        if not run_id or not getattr(ev, "run_id", "") or ev.run_id == run_id
    ]
    results: list[dict[str, Any]] = []
    for req in contract.requirements:
        if req.verifier == "tool_result":
            results.append(_eval_tool_result(req, scoped_events))
        elif req.verifier == "event_order":
            results.append(_eval_event_order(req, scoped_events))
        elif req.verifier == "artifact_diff":
            results.append(_eval_artifact_diff(req, scoped_events))
        else:
            results.append({
                "id": req.id,
                "description": req.description,
                "must_pass": req.must_pass,
                "status": "unverified",
                "evidence_refs": [],
                "reason": f"Unsupported verifier '{req.verifier}'",
            })

    total = len(results)
    verified_count = sum(1 for r in results if r["status"] == "verified")
    must_reqs = [r for r in results if r["must_pass"]]
    unmatched_list = [str(item) for item in unmatched_calls if str(item)]
    if not results:
        verdict = "NEEDS_REVIEW"
    elif any(r["status"] == "failed" for r in must_reqs):
        verdict = "FAIL"
    elif unmatched_list:
        verdict = "NEEDS_REVIEW"
    elif all(r["status"] == "verified" for r in must_reqs) and verified_count == total:
        verdict = "PASS"
    elif must_reqs and all(r["status"] == "verified" for r in must_reqs):
        verdict = "PASS"
    else:
        verdict = "NEEDS_REVIEW"

    payload: dict[str, Any] = {
        "schema_version": "skill-proof.proof.v1",
        "skill": contract.skill,
        "verdict": verdict,
        "verified_count": verified_count,
        "total_count": total,
        "requirements": results,
    }
    if run_id:
        payload["run_id"] = run_id
    if unmatched_list:
        payload["unmatched_calls"] = unmatched_list
    return payload


_POSITIVE_CLAIM_RE = re.compile(
    r"(?:"
    r"\ball\s+tests?\s+pass(?:ed)?\b|"
    r"\btests?\s+pass(?:ed)?\b|"
    r"\ball\s+checks?\s+pass(?:ed)?\b|"
    r"\b(?:bug|issue)\s+(?:is\s+)?fixed\b|"
    r"\bfixed\s+the\s+(?:bug|issue)\b|"
    r"\bcompleted\s+successfully\b|"
    r"\bverified\s+(?:working|passing|fixed)\b|"
    r"ทดสอบผ่าน(?:หมด)?|"
    r"ผ่านหมด(?:แล้ว)?|"
    r"แก้(?:ไข)?(?:บั๊ก|ปัญหา)?เรียบร้อย|"
    r"แก้เสร็จ(?:แล้ว)?|"
    r"ยืนยันแล้วว่าผ่าน"
    r")",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"(?:\b(?:not|never|no|didn't|did\s+not|cannot|can't|unable\s+to|fail(?:ed|ing)?)\b|"
    r"ยังไม่|ไม่ได้|ไม่ผ่าน|ล้มเหลว)",
    re.IGNORECASE,
)


def check_claim_contradiction(
    response_text: str,
    proof_report: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Check whether agent completion claims contradict observed ProofContract verdict."""
    if not isinstance(proof_report, Mapping) or not proof_report:
        return {"checked": False, "claimed_success": False, "contradicted": False, "reason": ""}

    clauses = [c.strip() for c in re.split(r"[.\n;!?]+", str(response_text or "")) if c.strip()]
    claimed = any(_POSITIVE_CLAIM_RE.search(c) and not _NEGATION_RE.search(c) for c in clauses)
    verdict = str(proof_report.get("verdict") or "NEEDS_REVIEW")
    if not claimed or verdict == "PASS":
        return {"checked": True, "claimed_success": claimed, "contradicted": False, "reason": ""}

    reqs = proof_report.get("requirements") or ()
    if verdict == "FAIL":
        failed = [r for r in reqs if isinstance(r, Mapping) and r.get("status") == "failed"]
        ids = [str(r.get("id") or "unknown") for r in failed]
        refs = [ref for r in failed for ref in (r.get("evidence_refs") or ())]
        return {
            "checked": True,
            "claimed_success": True,
            "contradicted": True,
            "reason": f"Claim contradicted by failed proof: {', '.join(ids) or 'FAIL'}",
            "contradicted_requirements": ids,
            "evidence_refs": refs,
        }

    missing = [str(r.get("id") or "unknown") for r in reqs if isinstance(r, Mapping) and r.get("status") != "verified"]
    return {
        "checked": True,
        "claimed_success": True,
        "contradicted": True,
        "reason": f"Claim unsubstantiated ({verdict}; unverified: {', '.join(missing) or 'none'})",
        "contradicted_requirements": missing,
        "evidence_refs": [],
    }


def format_proof_report(receipt: Mapping[str, Any]) -> str:
    """Format a human-readable Proof Report from a turn receipt."""
    proof = receipt.get("proof")
    if not isinstance(proof, Mapping):
        sel_raw = receipt.get("selected")
        sel = sel_raw if isinstance(sel_raw, Mapping) else {}
        skill_name = sel.get("name") or "none"
        return (
            f"Skill Proof Report — skill={skill_name} | "
            f"compliance={receipt.get('compliance', 'unassessed')} | "
            f"verification={receipt.get('verification', 'unverified')}\n"
            "No proof_contract attached to this turn."
        )
    header_meta = f"Skill: {proof.get('skill') or 'unknown'}"
    if proof.get("run_id"):
        header_meta += f" | Run: {proof.get('run_id')} (turns: {proof.get('turn_count', 1)})"
    lines = [
        "GOAL / SKILL EXECUTION REPORT",
        f"Verdict: {proof.get('verdict', 'NEEDS_REVIEW')} ({proof.get('verified_count', 0)} / {proof.get('total_count', 0)} requirements verified)",
        header_meta,
        "",
    ]
    for req in proof.get("requirements") or ():
        refs = ", ".join(req.get("evidence_refs") or ()) or "none"
        lines.append(f"- [{str(req.get('status', 'unverified')).upper()}] {req.get('id')}: {req.get('description')}")
        lines.append(f"  Evidence: {refs} — {req.get('reason')}")
    unmatched = proof.get("unmatched_calls") or ()
    if unmatched:
        lines.append(f"\nUnmatched concurrent calls: {', '.join(str(x) for x in unmatched)}")
    claim_check = receipt.get("claim_check")
    if isinstance(claim_check, Mapping) and claim_check.get("contradicted"):
        lines.append(f"\nClaim Check: CONTRADICTED — {claim_check.get('reason')}")
    return "\n".join(lines)
