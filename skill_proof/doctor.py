"""Standalone health diagnostics (`doctor --json`) for Skill Proof."""
from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Any, Mapping, Optional

from . import _catalog, _compliance, _vector, vault


def _package_version() -> str:
    try:
        from . import core
        return str(getattr(core, "__version__", "2.1.2"))
    except Exception:
        return "2.1.2"


def resolve_audit_path(audit_path: Optional[pathlib.Path | str] = None) -> pathlib.Path:
    """Resolve the Skill Proof audit log path cleanly for both standalone and Hermes hosts."""
    if audit_path:
        return pathlib.Path(audit_path).expanduser()
    env_audit = os.environ.get("SKILL_PROOF_AUDIT_PATH", "").strip()
    if env_audit:
        return pathlib.Path(env_audit).expanduser()
    env_data = os.environ.get("SKILL_PROOF_DATA_DIR", "").strip()
    if env_data:
        return pathlib.Path(env_data).expanduser() / "audit.jsonl"
    hermes_audit = _catalog.default_hermes_home() / "plugin-data" / "skill-proof" / "audit.jsonl"
    if hermes_audit.is_file():
        return hermes_audit
    return pathlib.Path.home() / ".skill-proof" / "audit.jsonl"


def _make_check(check_id: str, status: str, detail: str, fix_hint: str = "") -> dict[str, str]:
    return {
        "id": check_id,
        "status": status,
        "detail": detail,
        "fix_hint": fix_hint,
    }


def run_doctor(
    roots: Optional[Mapping[str, os.PathLike[str] | str]] = None,
    vault_dir: Optional[pathlib.Path | str] = None,
    audit_path: Optional[pathlib.Path | str] = None,
    vector_db: Optional[pathlib.Path | str] = None,
) -> dict[str, Any]:
    """Run all Skill Proof integrity, stub-corruption, drift, and index checks."""
    resolved_vault = pathlib.Path(vault_dir).expanduser().resolve() if vault_dir else vault.get_vault_root().resolve()
    resolved_roots = dict(roots) if roots is not None else _catalog.detect_agent_roots()
    vstat = vault.status(vault_dir=resolved_vault, roots=resolved_roots)
    catalog = _catalog.scan_catalog(resolved_roots)

    checks: list[dict[str, str]] = []

    # 1. vault_integrity
    vault_broken_children: list[str] = []
    if resolved_vault.is_dir():
        try:
            for item in resolved_vault.iterdir():
                if vault._is_reparse(item) and not item.exists():
                    vault_broken_children.append(str(item))
        except OSError:
            pass
    client_broken = sum(int(c.get("broken_links_count", 0)) for c in vstat.get("clients", {}).values())
    broken_total = len(vault_broken_children) + client_broken
    if not vstat["vault_exists"]:
        checks.append(_make_check(
            "vault_integrity",
            "warn",
            f"Canonical vault directory does not exist: {resolved_vault}",
            "Run: skill-proof vault sync",
        ))
    elif broken_total > 0:
        checks.append(_make_check(
            "vault_integrity",
            "warn",
            f"Detected {broken_total} broken skill junction(s)/symlink(s) across vault or client roots",
            "Run: skill-proof vault reconcile",
        ))
    else:
        checks.append(_make_check(
            "vault_integrity",
            "ok",
            f"Canonical vault ready at {resolved_vault} ({vstat['vault_skills_count']} skill(s))",
        ))

    # 2. stub_corruption
    stub_paths: set[str] = set()
    for hit in vstat.get("vault_stub_corrupted", []):
        if hit.get("path"):
            stub_paths.add(str(hit["path"]))
    for cinfo in vstat.get("clients", {}).values():
        for hit in cinfo.get("stub_corrupted", []):
            if hit.get("path"):
                stub_paths.add(str(hit["path"]))
    for diag in catalog.diagnostics:
        if diag.code == "stub_corrupted":
            stub_paths.add(diag.relative_path)
    stub_count = len(stub_paths)
    if stub_count > 0:
        sample = ", ".join(sorted(stub_paths)[:3])
        checks.append(_make_check(
            "stub_corruption",
            "fail",
            f"Detected {stub_count} SKILL.md file(s) with persisted Trimtok stub markers ({sample})",
            "Restore corrupted SKILL.md file(s) from clean source before running skill-proof vault reconcile",
        ))
    else:
        checks.append(_make_check(
            "stub_corruption",
            "ok",
            f"0 stub-corrupted SKILL.md files across {len(catalog.skills)} scanned skill(s)",
        ))

    # 3. cross_root_drift
    drifted_count = sum(int(c.get("drifted_count", 0)) for c in vstat.get("clients", {}).values())
    shadowed_count = sum(1 for d in catalog.diagnostics if d.code == "shadowed_by_root")
    if drifted_count > 0 or shadowed_count > 0:
        checks.append(_make_check(
            "cross_root_drift",
            "warn",
            f"{drifted_count} drifted client copy(s) vs vault, {shadowed_count} shadowed across roots",
            "Run: skill-proof vault reconcile --dry-run",
        ))
    else:
        checks.append(_make_check(
            "cross_root_drift",
            "ok",
            f"No cross-root drift or shadowing detected across {len(resolved_roots)} root(s)",
        ))

    # 4. audit_chain
    resolved_audit = resolve_audit_path(audit_path)
    audit_turns = 0
    corrupt_audit_lines = 0
    if not resolved_audit.is_file():
        checks.append(_make_check(
            "audit_chain",
            "info",
            f"No audit log at {resolved_audit} yet",
        ))
    else:
        records, corrupt_audit_lines = _compliance.read_audit_records(resolved_audit)
        audit_turns = len(records)
        if corrupt_audit_lines > 0:
            checks.append(_make_check(
                "audit_chain",
                "warn",
                f"{audit_turns} valid record(s) and {corrupt_audit_lines} corrupt line(s) in {resolved_audit}",
                "Inspect malformed JSONL lines in audit.jsonl",
            ))
        else:
            checks.append(_make_check(
                "audit_chain",
                "ok",
                f"{audit_turns} valid audit record(s) at {resolved_audit}",
            ))

    # 5. vector_index
    vector_err: Optional[Exception] = None
    idx = None
    try:
        idx = _vector.vector_index_from_env(db_path=vector_db, readonly=True)
    except (sqlite3.Error, OSError) as exc:
        vector_err = exc
    if vector_err is not None:
        checks.append(_make_check(
            "vector_index",
            "warn",
            f"Vector cache unreadable ({vector_err})",
            "Check permissions or remove corrupt embeddings.db",
        ))
    elif idx is None:
        checks.append(_make_check(
            "vector_index",
            "info",
            "Vector index disabled (provider=off; deterministic lexical routing active)",
        ))
    elif getattr(idx, "_local_unavailable", False):
        checks.append(_make_check(
            "vector_index",
            "warn",
            f"Requested local model {idx.model!r} is unavailable; latched to off",
            f"Run: skill-proof prefetch --model {idx.model}",
        ))
    else:
        pstat = idx.provider_status()
        if pstat.get("active"):
            checks.append(_make_check(
                "vector_index",
                "ok",
                f"Vector provider '{pstat['provider']}' active (model={pstat['model']}, cached={pstat['cached_skills']})",
            ))
        elif pstat.get("provider") == "gateway":
            checks.append(_make_check(
                "vector_index",
                "warn",
                f"Gateway provider configured (model={pstat['model']}) but API key missing",
                f"Set {idx.gateway_key_env} or set provider to off",
            ))
        else:
            checks.append(_make_check(
                "vector_index",
                "info",
                f"Vector provider '{pstat.get('provider', 'off')}' inactive",
            ))

    # 6. hermes_integration (strictly optional on standalone installs)
    hermes_home = _catalog.default_hermes_home()
    hermes_plugin = hermes_home / "plugins" / "skill-proof"
    hermes_cfg = hermes_home / "config.yaml"
    if hermes_plugin.is_dir():
        checks.append(_make_check(
            "hermes_integration",
            "ok",
            f"Optional Hermes plugin detected at {hermes_plugin}",
        ))
    elif hermes_cfg.is_file():
        checks.append(_make_check(
            "hermes_integration",
            "info",
            f"Hermes home found at {hermes_home} without skill-proof plugin (standalone mode unaffected)",
        ))
    else:
        checks.append(_make_check(
            "hermes_integration",
            "info",
            "Hermes not installed (running standalone)",
        ))

    statuses = {c["status"] for c in checks}
    overall = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "ok")

    return {
        "tool": "skill-proof",
        "version": _package_version(),
        "status": overall,
        "checks": checks,
        "metrics": {
            "vault_skills_count": int(vstat.get("vault_skills_count", 0)),
            "catalog_skills_count": len(catalog.skills),
            "roots_count": len(resolved_roots),
            "drifted_count": drifted_count,
            "shadowed_count": shadowed_count,
            "stub_corrupted_count": stub_count,
            "broken_links_count": broken_total,
            "audit_turns": audit_turns,
            "corrupt_audit_lines": corrupt_audit_lines,
        },
    }
