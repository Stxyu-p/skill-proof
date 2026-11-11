"""Analyze the retained audit window and preview/archive unselected skills.

A missing skill from a finite log is only an unobserved candidate, not proof
that it is permanently unused; destructive archival is explicit and fail-closed.
"""
from __future__ import annotations

from collections import Counter
import os
import pathlib
import shutil
from typing import Any, Optional

from . import _compliance
from . import vault

def audit_stats(
    audit_path: Optional[pathlib.Path | str] = None,
    vault_dir: Optional[pathlib.Path | str] = None,
) -> dict[str, Any]:
    default_audit = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "plugin-data" / "skill-proof" / "audit.jsonl"
    a_path = pathlib.Path(audit_path).expanduser() if audit_path else default_audit
    v_dir = (pathlib.Path(vault_dir).expanduser() if vault_dir else vault.DEFAULT_VAULT_ROOT).resolve()

    records, corrupt = _compliance.read_audit_records(a_path)
    usable_statuses = {"selected", "no_match", "ambiguous", "blocked"}
    unusable_records = sum(1 for record in records if record.get("status") not in usable_statuses)
    records = [record for record in records if record.get("status") in usable_statuses]
    if not a_path.is_file():
        prune_block_reason = "audit_log_missing"
    elif corrupt:
        prune_block_reason = "audit_log_corrupt"
    elif unusable_records:
        prune_block_reason = "audit_log_has_unusable_records"
    elif not records:
        prune_block_reason = "audit_log_has_no_usable_records"
    else:
        prune_block_reason = None
    status_counts = Counter()
    skill_counts = Counter()

    for r in records:
        st = r.get("status")
        if st:
            status_counts[st] += 1
        used_names = set()
        selected = r.get("selected")
        if isinstance(selected, str) and selected:
            used_names.add(selected)
        override = r.get("override_loaded")
        if isinstance(override, str) and override:
            used_names.add(override)
        elif isinstance(override, (list, tuple, set)):
            used_names.update(name for name in override if isinstance(name, str) and name)
        for name in used_names:
            skill_counts[name] += 1

    total_turns = len(records)
    selected_turns = status_counts.get("selected", 0)
    selection_rate = (selected_turns / total_turns) if total_turns > 0 else 0.0

    # Vault skills
    vault_skills = [s["name"] for s in vault.list_vault_skills(v_dir)]
    active_in_vault = set(vault_skills)
    
    if prune_block_reason is None:
        dead_skills = [s for s in vault_skills if s not in skill_counts]
        used_skills_count = len(active_in_vault - set(dead_skills))
        dead_skills_count = len(dead_skills)
    else:
        dead_skills = []
        used_skills_count = None
        dead_skills_count = None

    return {
        "audit_path": str(a_path),
        "vault_dir": str(v_dir),
        "total_turns": total_turns,
        "first_record_ts": records[0].get("ts") if records else None,
        "last_record_ts": records[-1].get("ts") if records else None,
        "corrupt_records": corrupt,
        "unusable_records": unusable_records,
        "prune_safe": prune_block_reason is None,
        "prune_block_reason": prune_block_reason,
        "selection_rate": selection_rate,
        "status_counts": dict(status_counts),
        "top_skills": skill_counts.most_common(15),
        "vault_skills_count": len(vault_skills),
        "used_skills_count": used_skills_count,
        "dead_skills_count": dead_skills_count,
        "dead_skills": dead_skills,
    }


def prune_dead_skills(
    skills_to_prune: Optional[list[str]] = None,
    vault_dir: Optional[pathlib.Path | str] = None,
    archive_dir: Optional[pathlib.Path | str] = None,
    dry_run: bool = True,
    audit_path: Optional[pathlib.Path | str] = None,
) -> dict[str, Any]:
    v_dir = (pathlib.Path(vault_dir).expanduser() if vault_dir else vault.DEFAULT_VAULT_ROOT).resolve()
    a_dir = (pathlib.Path(archive_dir).expanduser() if archive_dir else v_dir.parent / "archive").resolve()

    # Block archive path escapes: archive must sit OUTSIDE the vault but never inside a vault skill.
    try:
        if v_dir.is_relative_to(a_dir) or a_dir.is_relative_to(v_dir):
            raise ValueError(f"refusing unsafe archive dir overlapping vault: {a_dir}")
    except AttributeError:
        # Python 3.9 fallback: compare commonpath
        import os as _os
        try:
            if _os.path.commonpath([str(v_dir), str(a_dir)]) in (str(v_dir), str(a_dir)):
                raise ValueError(f"refusing unsafe archive dir overlapping vault: {a_dir}")
        except (OSError, ValueError):
            pass

    if skills_to_prune is None:
        stats = audit_stats(audit_path=audit_path, vault_dir=v_dir)
        if not stats["prune_safe"]:
            return {
                "vault_dir": str(v_dir),
                "archive_dir": str(a_dir),
                "pruned_count": 0,
                "skipped_count": 0,
                "pruned": [],
                "skipped": [],
                "dry_run": True,
                "refused": True,
                "reason": stats["prune_block_reason"],
            }
        target_skills = stats["dead_skills"]
    else:
        target_skills = list(skills_to_prune)

    pruned = []
    skipped = []

    for name in target_skills:
        safe = vault._safe_skill_name(name)
        if safe is None:
            skipped.append({"skill": name, "reason": "invalid_skill_name"})
            continue
        src = v_dir / safe
        if not src.exists():
            skipped.append({"skill": safe, "reason": "not_found"})
            continue
        # Don't prune immutable SOPs
        if vault.is_skill_immutable(src):
            skipped.append({"skill": safe, "reason": "immutable_sop_locked"})
            continue

        dest = a_dir / safe
        if dest.exists():
            skipped.append({"skill": safe, "reason": "archive_destination_exists"})
            continue
        if not dry_run:
            a_dir.mkdir(parents=True, exist_ok=True)
            try:
                if vault._is_reparse(src):
                    src.rmdir()
                else:
                    shutil.move(str(src), str(dest))
                pruned.append({"skill": safe, "destination": str(dest)})
            except Exception as exc:
                skipped.append({"skill": safe, "reason": str(exc)})
        else:
            pruned.append({"skill": safe, "destination": str(dest), "dry_run": True})

    return {
        "vault_dir": str(v_dir),
        "archive_dir": str(a_dir),
        "pruned_count": len(pruned),
        "skipped_count": len(skipped),
        "pruned": pruned,
        "skipped": skipped,
        "dry_run": dry_run,
    }
