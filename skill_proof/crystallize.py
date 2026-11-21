from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import Any, Optional

from . import _catalog, vault as _vault


def get_vault_root() -> pathlib.Path:
    return _catalog._canonical_vault_root()


DEFAULT_VAULT_ROOT = get_vault_root()


@dataclasses.dataclass(frozen=True)
class SkillProof:
    skill_name: str
    verification_cmd: str
    exit_code: Optional[int]
    timestamp: str
    commit: str
    status: str
    proof_hash: str

    @classmethod
    def create(
        cls,
        skill_name: str,
        verification_cmd: str,
        exit_code: Optional[int] = None,
        commit: str = "",
        timestamp: str | None = None,
        executed: bool = False,
    ) -> SkillProof:
        ts = timestamp or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if executed:
            status = "verified" if exit_code == 0 else "unverified"
        elif exit_code is not None and exit_code != 0:
            status = "unverified"
        else:
            status = "declared"
        seed = f"{skill_name}:{verification_cmd}:{exit_code}:{executed}:{ts}:{commit}"
        p_hash = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()
        return cls(
            skill_name=skill_name,
            verification_cmd=verification_cmd,
            exit_code=exit_code,
            timestamp=ts,
            commit=commit,
            status=status,
            proof_hash=p_hash,
        )


def _clamp_description(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return "Operational capability workflow."
    if not cleaned.endswith("."):
        cleaned += "."
    if len(cleaned) <= 60:
        return cleaned
    truncated = cleaned[:59]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(",;- ") + "."


def _slugify_skill_name(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(name or "")).strip().lower()
    slug = re.sub(r"[-\s]+", "-", s)[:64].strip("-")
    return slug or "unnamed-skill"


def crystallize_skill(
    name: str,
    description: str,
    steps: list[str],
    *,
    verification_cmd: str = "",
    exit_code: Optional[int] = None,
    executed: bool = False,
    category: str = "devops",
    commit: str = "",
    when_to_use: str | None = None,
    pitfalls: list[str] | None = None,
    evidence: list[str] | None = None,
) -> tuple[str, SkillProof]:
    slug = _slugify_skill_name(name)
    desc = _clamp_description(description)
    proof = SkillProof.create(
        slug, verification_cmd, exit_code=exit_code, commit=commit, executed=executed
    )
    if evidence:
        proof = dataclasses.replace(proof, status="unverified")

    w_trigger = when_to_use or f"Use when executing {slug} workflow."
    p_items = pitfalls or ["Ensure environment dependencies and permissions are satisfied before running."]
    author = os.environ.get("SKILL_PROOF_AUTHOR", "").strip() or "Skill Proof"

    body_lines = [
        "---",
        f"name: {slug}",
        f"description: {json.dumps(desc, ensure_ascii=False)}",
        f"author: {author}",
        "license: MIT",
        "platforms: [linux, macos, windows]",
        "metadata:",
        "  hermes:",
        f"    tags: [{slug}, {category}]",
        "  proof:",
        f"    status: {proof.status}",
        f"    proof_hash: {proof.proof_hash}",
        f"    verification_cmd: {json.dumps(verification_cmd, ensure_ascii=False)}",
        f"    exit_code: {'null' if (evidence or proof.exit_code is None) else proof.exit_code}",
        f'    timestamp: "{proof.timestamp}"',
        "---",
        "",
        f"# {slug.replace('-', ' ').title()} Skill",
        "",
        f"{desc}",
        "",
        "## When to Use [INFERRED]",
        f"- {w_trigger}",
        "",
        "## How to Run",
        "Execute procedure steps via terminal tool.",
        "",
        "## Procedure [EXTRACTED]",
    ]

    for i, step in enumerate(steps, 1):
        body_lines.append(f"{i}. {step}")

    body_lines.extend([
        "",
        "## Pitfalls [INFERRED]",
    ])
    for pitfall in p_items:
        body_lines.append(f"- {pitfall}")

    if evidence:
        body_lines.extend(["", "## Evidence [EXTRACTED]"])
        body_lines.extend(evidence)

    body_lines.extend([
        "",
        "## Verification",
        f"Run: `{verification_cmd or 'true'}`",
    ])
    if evidence:
        body_lines.append(
            "Expected: exit code 0. Not asserted here: this skill was mined from "
            "observed runs and still needs one real verification before promotion."
        )
    elif proof.exit_code is None:
        body_lines.append("Expected: exit code 0 (declared only; not executed yet).")
    else:
        body_lines.append(f"Expected: exit code {proof.exit_code}.")

    body_lines.extend([
        "",
        f"<!-- skill_proof: {proof.proof_hash} -->",
        "",
    ])

    skill_md = "\n".join(body_lines)
    return skill_md, proof


def load_candidates_jsonl(source: str | pathlib.Path) -> list[dict[str, Any]]:
    """Read `skill-proof.candidate.v1` JSONL lines from a file path or '-' (stdin)."""
    if str(source) == "-":
        raw = sys.stdin.read()
    else:
        raw = pathlib.Path(source).expanduser().read_text(encoding="utf-8", errors="replace")
    candidates: list[dict[str, Any]] = []
    for idx, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        obj = json.loads(s)
        if not isinstance(obj, dict):
            raise ValueError(f"candidate line {idx} must be a JSON object")
        schema = str(obj.get("schema") or "skill-proof.candidate.v1")
        if schema != "skill-proof.candidate.v1":
            raise ValueError(f"unsupported candidate schema on line {idx}: {schema!r}")
        name = str(obj.get("slug") or obj.get("name") or "").strip()
        desc = str(obj.get("description") or "").strip()
        if not name or not desc:
            raise ValueError(f"candidate line {idx} requires non-empty name/slug and description")
        steps = [str(x).strip() for x in (obj.get("steps") or []) if str(x).strip()]
        if not steps:
            steps = ["Follow the documented procedure steps."]
        candidates.append({
            "name": name,
            "description": desc,
            "category": str(obj.get("category") or "devops").strip() or "devops",
            "when_to_use": obj.get("when_to_use"),
            "steps": steps,
            "pitfalls": [str(x).strip() for x in (obj.get("pitfalls") or []) if str(x).strip()] or None,
            "verification_cmd": str(obj.get("verification_cmd") or "").strip(),
            "evidence": [str(x).strip() for x in (obj.get("evidence_refs") or obj.get("evidence") or []) if str(x).strip()] or None,
            "provenance_sha256": str(obj.get("provenance_sha256") or ""),
            "source_tool": str(obj.get("source_tool") or ""),
        })
    return candidates


def save_crystallized_skill(
    skill_md: str,
    name: str,
    category: str = "devops",
    vault_dir: str | pathlib.Path | None = None,
    hermes_skills_dir: str | pathlib.Path | None = None,
) -> dict[str, str]:
    stub_hits = _catalog.find_stub_markers(skill_md)
    if stub_hits:
        raise ValueError(f"Refusing to save skill containing compressed Trimtok stubs: {stub_hits[0]}")

    slug = _slugify_skill_name(name)
    results: dict[str, str] = {}

    v_base = pathlib.Path(vault_dir if vault_dir is not None else get_vault_root()).expanduser().resolve()
    cat_dir = v_base / category if category else None
    if cat_dir is not None and cat_dir.is_dir() and not (cat_dir / "SKILL.md").is_file():
        v_skill_dir = cat_dir / slug
    else:
        v_skill_dir = v_base / slug
    if _vault.is_skill_immutable(v_skill_dir):
        raise PermissionError(f"Skill '{slug}' at '{v_skill_dir}' is immutable (governance: locked)")

    v_skill_dir.mkdir(parents=True, exist_ok=True)
    v_file = v_skill_dir / "SKILL.md"
    v_file.write_text(skill_md, encoding="utf-8")
    results["vault_file"] = str(v_file)

    if hermes_skills_dir is not None:
        h_base = pathlib.Path(hermes_skills_dir).expanduser().resolve()
        h_base.mkdir(parents=True, exist_ok=True)
        linked = False
        if v_skill_dir.parent == v_base:
            pin_res = _vault.pin(slug, h_base, vault_dir=v_base, force=True)
            if pin_res.get("success") and pin_res.get("target"):
                results["hermes_file"] = str(pathlib.Path(pin_res["target"]) / "SKILL.md")
                results["hermes_pinned"] = "true"
                linked = True
        else:
            target = h_base / slug
            ok = _vault.create_junction_or_link(v_skill_dir, target, force=True)
            if ok:
                results["hermes_file"] = str(target / "SKILL.md")
                results["hermes_pinned"] = "true"
                linked = True
        if not linked:
            h_skill_dir = h_base / slug
            if _vault.is_skill_immutable(h_skill_dir):
                raise PermissionError(f"Hermes skill '{slug}' at '{h_skill_dir}' is immutable")
            h_skill_dir.mkdir(parents=True, exist_ok=True)
            h_file = h_skill_dir / "SKILL.md"
            h_file.write_text(skill_md, encoding="utf-8")
            results["hermes_file"] = str(h_file)
            results["hermes_pinned"] = "false"

    return results
