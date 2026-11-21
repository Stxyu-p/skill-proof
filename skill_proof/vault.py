"""
Skill Proof Global Vault Manager.

Provides a unified canonical skill repository at ~/.agents/skills/
and manages synchronization and pinning across agent ecosystems
(Codex, Claude Code, Gemini CLI, Antigravity, Cursor, Hermes).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Mapping, Optional

from . import _catalog


def get_vault_root() -> pathlib.Path:
    """Resolve the active global skill vault root dynamically."""
    return _catalog._canonical_vault_root()


DEFAULT_VAULT_ROOT = get_vault_root()


def init_vault(vault_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Ensure the global vault directory exists and is initialized."""
    target = (vault_dir or get_vault_root()).resolve()
    target.mkdir(parents=True, exist_ok=True)
    manifest = target.parent / "vault.json"
    if not manifest.exists():
        manifest.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "managed_by": "skill-proof",
                    "vault_dir": str(target),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return target


def _skill_content_info(skill_dir: pathlib.Path) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Return (sha256_hex, stub_hits) for ``skill_dir/SKILL.md``."""
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return None, []
    try:
        raw = md.read_bytes()
    except OSError:
        return None, []
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    return sha, _catalog.find_stub_markers(text)


def _collect_client_skills(root: pathlib.Path) -> tuple[list[tuple[str, pathlib.Path, bool]], list[pathlib.Path]]:
    """Collect (skill_name, skill_dir, is_nested) entries and broken links under *root*."""
    entries: list[tuple[str, pathlib.Path, bool]] = []
    broken_links: list[pathlib.Path] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return entries, broken_links
    for child in children:
        if child.name.startswith("."):
            continue
        is_rep = _is_reparse(child)
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        if not is_dir:
            if is_rep:
                broken_links.append(child)
            continue
        try:
            has_skill = (child / "SKILL.md").is_file()
        except OSError:
            has_skill = False
        if has_skill:
            entries.append((child.name, child, False))
        elif not is_rep:
            try:
                sub_mds = sorted(child.rglob("SKILL.md"))
            except OSError:
                continue
            for sub_md in sub_mds:
                try:
                    rel_parts = sub_md.relative_to(root).parts
                except OSError:
                    continue
                if any(part.startswith(".") for part in rel_parts):
                    continue
                entries.append((sub_md.parent.name, sub_md.parent, True))
    return entries, broken_links


def list_vault_skills(vault_dir: Optional[pathlib.Path] = None) -> list[dict[str, Any]]:
    """List all skills in the global vault."""
    vdir = (vault_dir or get_vault_root()).resolve()
    if not vdir.is_dir():
        return []
    results = []
    for item in sorted(vdir.iterdir()):
        if not item.is_dir():
            continue
        skill_file = item / "SKILL.md"
        if not skill_file.is_file():
            continue
        # Extract title or basic summary
        title = item.name
        description = ""
        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines()[:20]:
                if line.startswith("name:"):
                    title = line.split(":", 1)[1].strip().strip('"\'')
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip('"\'')
        except Exception:
            pass
        results.append(
            {
                "name": item.name,
                "title": title,
                "description": description,
                "path": str(item),
                "is_link": item.is_symlink() or _is_reparse(item),
            }
        )
    return results


def _is_reparse(path: pathlib.Path) -> bool:
    try:
        if path.is_symlink():
            return True
        info = path.lstat()
        attrs = getattr(info, "st_file_attributes", 0)
        return bool(attrs and attrs & 0x0400)
    except Exception:
        return False


def _is_self_or_cycle_target(source: pathlib.Path, target: pathlib.Path) -> bool:
    """Return True if creating a link at *target* pointing to *source* would self-loop or delete *source*."""
    try:
        src_abs = pathlib.Path(os.path.abspath(os.fspath(source)))
        tgt_abs = pathlib.Path(os.path.abspath(os.fspath(target)))
        if os.path.normcase(str(src_abs)) == os.path.normcase(str(tgt_abs)):
            return True
        src_res = source.resolve(strict=False)
        if target.exists() and not _is_reparse(target):
            if target.resolve(strict=False) == src_res:
                return True
        if target.parent.resolve(strict=False) == src_res:
            return True
        try:
            if src_res.is_relative_to(tgt_abs):
                return True
        except ValueError:
            pass
    except OSError:
        return True
    return False


def create_junction_or_link(source: pathlib.Path, target: pathlib.Path, force: bool = False) -> bool:
    """Create an NTFS junction on Windows or a symlink on Unix (handles dangling junctions)."""
    if _is_self_or_cycle_target(source, target):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    is_rep = _is_reparse(target)
    if is_rep:
        # Dangling or live junction/symlink dir on Windows must be removed via rmdir/unlink first
        try:
            target.rmdir()
        except OSError:
            try:
                target.unlink()
            except OSError:
                return False
    elif target.exists():
        if target.is_file():
            target.unlink()
        elif force and target.is_dir():
            shutil.rmtree(target)
        else:
            return False

    if os.name == "nt":
        # 1. Prefer native standard library _winapi.CreateJunction (zero subprocess overhead)
        try:
            import _winapi
            _winapi.CreateJunction(str(source.resolve()), str(target))
            return True
        except Exception:
            pass
        # 2. Fallback to cmd mklink with shell=False
        try:
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(target), str(source.resolve())],
                capture_output=True, text=True, check=False, shell=False,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
    try:
        target.symlink_to(source.resolve(), target_is_directory=True)
        return True
    except Exception:
        return False


def migrate_from_roots(
    roots: Mapping[str, os.PathLike[str] | str],
    vault_dir: Optional[pathlib.Path] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate physical skills from dispersed agent roots into the global vault."""
    vdir = init_vault(vault_dir) if not dry_run else (vault_dir or get_vault_root()).resolve()
    migrated = []
    skipped = []
    already_vault = []

    for root_id, root_path in sorted(roots.items()):
        rp = pathlib.Path(root_path).expanduser()
        if not rp.is_dir():
            continue
        try:
            resolved_root = rp.resolve(strict=True)
        except Exception:
            continue
        if resolved_root == vdir.resolve():
            continue

        candidate_entries, _ = _collect_client_skills(rp)
        for _, child, _ in candidate_entries:
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            if _is_reparse(child):
                try:
                    target = child.resolve()
                    if target.is_relative_to(vdir) or target == vdir / child.name:
                        already_vault.append(child.name)
                        continue
                except Exception:
                    pass

            src_sha, src_stubs = _skill_content_info(child)
            if src_stubs:
                skipped.append(
                    {
                        "skill": child.name,
                        "source": str(child),
                        "reason": "stub_corrupted",
                        "stubs": src_stubs,
                    }
                )
                continue

            dest = vdir / child.name
            if dest.exists():
                dest_md = dest / "SKILL.md"
                dst_sha, dst_stubs = _skill_content_info(dest)
                if src_sha and dst_sha and src_sha == dst_sha:
                    skipped.append(
                        {
                            "skill": child.name,
                            "source": str(child),
                            "reason": "already_in_vault",
                        }
                    )
                    continue
                if is_skill_immutable(dest):
                    skipped.append(
                        {
                            "skill": child.name,
                            "source": str(child),
                            "reason": "immutable_vault_skill",
                            "src_sha256": src_sha,
                            "dst_sha256": dst_sha,
                        }
                    )
                    continue

                should_upgrade = bool(dst_stubs) or not dest_md.is_file()
                if not should_upgrade:
                    try:
                        should_upgrade = skill_md.stat().st_mtime > dest_md.stat().st_mtime
                    except OSError:
                        should_upgrade = False

                if should_upgrade:
                    if not dry_run:
                        try:
                            shutil.rmtree(dest)
                            shutil.copytree(child, dest, symlinks=False)
                            migrated.append(
                                {
                                    "skill": child.name,
                                    "source": str(child),
                                    "upgraded": True,
                                    "repaired_stub_corruption": bool(dst_stubs),
                                    "src_sha256": src_sha,
                                    "dst_sha256": dst_sha,
                                }
                            )
                        except Exception as e:
                            skipped.append({"skill": child.name, "source": str(child), "reason": str(e)})
                    else:
                        migrated.append(
                            {
                                "skill": child.name,
                                "source": str(child),
                                "upgraded": True,
                                "repaired_stub_corruption": bool(dst_stubs),
                                "src_sha256": src_sha,
                                "dst_sha256": dst_sha,
                            }
                        )
                else:
                    skipped.append(
                        {
                            "skill": child.name,
                            "source": str(child),
                            "reason": "divergent_conflict",
                            "src_sha256": src_sha,
                            "dst_sha256": dst_sha,
                        }
                    )
                continue

            if not dry_run:
                try:
                    shutil.copytree(child, dest, symlinks=False)
                    migrated.append({"skill": child.name, "source": str(child)})
                except Exception as e:
                    skipped.append(
                        {"skill": child.name, "source": str(child), "reason": str(e)}
                    )
            else:
                migrated.append({"skill": child.name, "source": str(child)})

    return {
        "vault": str(vdir),
        "migrated_count": len(migrated),
        "skipped_count": len(skipped),
        "already_linked_count": len(already_vault),
        "migrated": migrated,
        "skipped": skipped,
    }


def pin(
    skill_name: str,
    target_root: pathlib.Path,
    vault_dir: Optional[pathlib.Path] = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Pin a skill from the global vault to a target agent directory via junction/symlink."""
    safe = _safe_skill_name(skill_name)
    if safe is None:
        return {"success": False, "error": f"invalid skill name: {skill_name!r}"}
    vdir = (vault_dir or get_vault_root()).resolve()
    source = vdir / safe
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        return {
            "success": False,
            "error": f"skill '{safe}' not found in vault {vdir}",
        }

    target_resolved = pathlib.Path(target_root).expanduser().resolve()
    if target_resolved == vdir:
        return {
            "success": True,
            "already_in_vault": True,
            "dry_run": dry_run,
            "source": str(source),
            "target": str(source),
        }

    dest = target_resolved / safe
    if _is_self_or_cycle_target(source, dest):
        return {
            "success": True,
            "already_in_vault": True,
            "dry_run": dry_run,
            "source": str(source),
            "target": str(dest),
        }
    if dest.exists() and dest.is_dir() and not _is_reparse(dest) and not force:
        return {
            "success": False,
            "dry_run": dry_run,
            "reason": "target_is_physical_dir",
            "error": f"'{dest}' is a physical directory; pass --force to replace with a vault junction",
            "source": str(source),
            "target": str(dest),
        }
    if dry_run:
        return {"success": True, "dry_run": True, "source": str(source), "target": str(dest)}

    ok = create_junction_or_link(source, dest, force=force)
    return {"success": ok, "source": str(source), "target": str(dest)}


def unpin(skill_name: str, target_root: pathlib.Path, dry_run: bool = False) -> dict[str, Any]:
    """Unpin (remove link) a skill from a target agent directory."""
    safe = _safe_skill_name(skill_name)
    if safe is None:
        return {"success": False, "error": f"invalid skill name: {skill_name!r}"}
    dest = target_root.resolve() / safe
    if not (dest.exists() or _is_reparse(dest)):
        return {"success": False, "error": f"skill link '{dest}' does not exist"}

    if not _is_reparse(dest):
        return {
            "success": False,
            "error": f"'{dest}' is a physical directory, not a link. Refusing to unpin to prevent data loss.",
        }

    if dry_run:
        return {"success": True, "dry_run": True, "target": str(dest)}

    try:
        if dest.is_dir():
            dest.rmdir()
        else:
            dest.unlink()
        return {"success": True, "target": str(dest)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def status(
    vault_dir: Optional[pathlib.Path] = None,
    roots: Optional[Mapping[str, os.PathLike[str] | str]] = None,
) -> dict[str, Any]:
    """Inspect the status of the global skill vault and client linkages."""
    vdir = (vault_dir or get_vault_root()).resolve()
    vault_exists = vdir.is_dir()
    skills = list_vault_skills(vdir) if vault_exists else []
    vault_hashes: dict[str, Optional[str]] = {}
    vault_stub_corrupted: list[dict[str, Any]] = []
    for item in skills:
        sdir = pathlib.Path(item["path"])
        sha, stubs = _skill_content_info(sdir)
        vault_hashes[item["name"]] = sha
        if stubs:
            vault_stub_corrupted.append(
                {
                    "skill": item["name"],
                    "path": str(sdir / "SKILL.md"),
                    "stubs": stubs,
                }
            )

    client_stats: dict[str, Any] = {}
    if roots:
        for root_id, root_path in sorted(roots.items()):
            rp = pathlib.Path(root_path).expanduser()
            if not rp.is_dir():
                continue
            try:
                if rp.resolve() == vdir:
                    continue
            except OSError:
                continue

            entries, broken_links = _collect_client_skills(rp)
            total = len(entries)
            pinned_to_vault = 0
            local_physical = 0
            drifted_skills: list[dict[str, Any]] = []
            stub_corrupted: list[dict[str, Any]] = []
            broken_paths = [str(bp) for bp in broken_links]

            for sname, sdir, is_nested in entries:
                is_rep = _is_reparse(sdir)
                if is_rep:
                    try:
                        tgt = sdir.resolve(strict=True)
                        if tgt.is_relative_to(vdir):
                            pinned_to_vault += 1
                            continue
                    except Exception:
                        broken_paths.append(str(sdir))
                        continue
                local_physical += 1
                sha, stubs = _skill_content_info(sdir)
                if stubs:
                    stub_corrupted.append(
                        {
                            "skill": sname,
                            "path": str(sdir / "SKILL.md"),
                            "stubs": stubs,
                        }
                    )
                vsha = vault_hashes.get(sname)
                if vsha and sha and sha != vsha:
                    drifted_skills.append(
                        {
                            "skill": sname,
                            "path": str(sdir),
                            "nested": is_nested,
                            "vault_sha256": vsha,
                            "client_sha256": sha,
                        }
                    )

            client_stats[root_id] = {
                "path": str(rp),
                "total_skills": total,
                "pinned_to_vault": pinned_to_vault,
                "local_physical": local_physical,
                "drifted_count": len(drifted_skills),
                "drifted": drifted_skills,
                "stub_corrupted_count": len(stub_corrupted),
                "stub_corrupted": stub_corrupted,
                "broken_links_count": len(broken_paths),
                "broken_links": broken_paths,
            }

    return {
        "vault_dir": str(vdir),
        "vault_exists": vault_exists,
        "vault_skills_count": len(skills),
        "skills": [s["name"] for s in skills],
        "vault_stub_corrupted_count": len(vault_stub_corrupted),
        "vault_stub_corrupted": vault_stub_corrupted,
        "clients": client_stats,
    }


def sync(
    vault_dir: Optional[pathlib.Path] = None,
    roots: Optional[Mapping[str, os.PathLike[str] | str]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Full synchronization: migrate physical skills to vault and ensure links are healthy."""
    vdir = init_vault(vault_dir) if not dry_run else (vault_dir or get_vault_root()).resolve()
    migration_report = {}
    if roots:
        migration_report = migrate_from_roots(roots, vault_dir=vdir, dry_run=dry_run)
    st = status(vault_dir=vdir, roots=roots)
    return {
        "vault": str(vdir),
        "migration": migration_report,
        "status": st,
    }


def link_all(
    target_root: pathlib.Path,
    vault_dir: Optional[pathlib.Path] = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Pin all skills from the global vault to a target agent directory via junctions."""
    vdir = (vault_dir or get_vault_root()).resolve()
    target = pathlib.Path(target_root).resolve()
    target.mkdir(parents=True, exist_ok=True)
    skills = list_vault_skills(vdir)
    results = []
    success_count = 0
    for s in skills:
        name = s["name"]
        res = pin(name, target, vault_dir=vdir, dry_run=dry_run, force=force)
        if res.get("success"):
            success_count += 1
        results.append({"skill": name, **res})
    return {
        "vault": str(vdir),
        "target": str(target),
        "total": len(skills),
        "linked": success_count,
        "dry_run": dry_run,
        "results": results,
    }


def reconcile_all_clients(
    vault_dir: Optional[pathlib.Path] = None,
    roots: Optional[Mapping[str, os.PathLike[str] | str]] = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Auto-heal and reconcile all detected agent client roots via NTFS junctions."""
    vdir = init_vault(vault_dir) if not dry_run else (vault_dir or get_vault_root()).resolve()
    if roots is not None:
        detected_roots = dict(roots)
    else:
        detected_roots = dict(_catalog.detect_agent_roots())
        base_home = pathlib.Path.home()
        hermes_home = _catalog.default_hermes_home()
        known_clients = [
            ("antigravity-cli", base_home / ".gemini" / "antigravity-cli" / "skills"),
            ("antigravity", base_home / ".gemini" / "antigravity" / "skills"),
            ("hermes", hermes_home / "skills"),
            ("claude", base_home / ".claude" / "skills"),
            ("cursor", base_home / ".cursor" / "skills"),
        ]
        for cid, cpath in known_clients:
            if cid not in detected_roots and (cpath.parent.exists() or cpath.exists()):
                detected_roots[cid] = cpath

    skills = list_vault_skills(vdir)
    vault_hashes = {s["name"]: _skill_content_info(pathlib.Path(s["path"]))[0] for s in skills}
    reconciled: dict[str, Any] = {}

    for root_id, root_str in detected_roots.items():
        rp = pathlib.Path(root_str).expanduser()
        try:
            rp_resolved = rp.resolve()
        except OSError:
            rp_resolved = pathlib.Path(os.path.abspath(str(rp)))
        if rp_resolved == vdir.resolve():
            continue

        # If it already resolves directly to the vault root and is a reparse point, it is healthy!
        if _is_reparse(rp):
            try:
                if rp.resolve() == vdir.resolve():
                    reconciled[root_id] = {"path": str(rp), "status": "healthy_junction", "target": str(vdir)}
                    continue
            except Exception:
                pass
            if not dry_run:
                rp.parent.mkdir(parents=True, exist_ok=True)
                ok = create_junction_or_link(vdir, rp, force=force)
                reconciled[root_id] = {"path": str(rp), "status": "repaired_junction" if ok else "failed", "target": str(vdir)}
            else:
                reconciled[root_id] = {"path": str(rp), "status": "dry_run_reconcile", "target": str(vdir)}
            continue

        # Hybrid or per-skill pinning (e.g. Hermes global with nested category dirs)
        if root_id == "hermes":
            entries, broken = _collect_client_skills(rp) if rp.is_dir() else ([], [])
            nested_by_name = {sname: sdir for sname, sdir, is_nested in entries if is_nested}
            pinned_count = 0
            nested_present = 0
            drifted_nested = 0
            stub_corrupted_count = 0
            failed_count = 0

            for s in skills:
                name = s["name"]
                target_skill = rp / name
                is_rep = _is_reparse(target_skill)
                if is_rep:
                    try:
                        tgt = target_skill.resolve(strict=True)
                        if tgt == (vdir / name).resolve():
                            pinned_count += 1
                            continue
                    except Exception:
                        pass
                if not target_skill.exists() and name in nested_by_name and not force:
                    nested_dir = nested_by_name[name]
                    sha, stubs = _skill_content_info(nested_dir)
                    nested_present += 1
                    if stubs:
                        stub_corrupted_count += 1
                    elif vault_hashes.get(name) and sha and sha != vault_hashes.get(name):
                        drifted_nested += 1
                    continue
                if not dry_run:
                    ok = pin(name, rp, vault_dir=vdir, force=force)
                    if ok.get("success"):
                        pinned_count += 1
                    else:
                        failed_count += 1
                elif target_skill.exists() and _is_reparse(target_skill):
                    pinned_count += 1

            total_skills = len(skills)
            if dry_run:
                hermes_status = "dry_run_preview"
            elif stub_corrupted_count > 0 or drifted_nested > 0:
                hermes_status = "drifted"
            elif failed_count > 0 or (pinned_count + nested_present) < total_skills:
                hermes_status = "incomplete"
            elif pinned_count == total_skills:
                hermes_status = "healthy_junctions"
            else:
                hermes_status = "healthy_mixed"

            reconciled[root_id] = {
                "path": str(rp),
                "status": hermes_status,
                "pinned_count": pinned_count,
                "nested_present_count": nested_present,
                "drifted_nested_count": drifted_nested,
                "stub_corrupted_count": stub_corrupted_count,
                "broken_links_count": len(broken),
                "failed_count": failed_count,
                "total_vault_skills": total_skills,
            }
            continue

        reconciled[root_id] = {"path": str(rp), "status": "unchanged"}

    # Project-level check
    cwd = pathlib.Path.cwd()
    project_hermes = cwd / ".hermes" / "skills"
    project_agents = cwd / ".agents" / "skills"
    if project_agents.is_dir() and project_agents.resolve() != vdir.resolve():
        is_rep = _is_reparse(project_hermes)
        if not is_rep or not project_hermes.exists():
            if not dry_run:
                project_hermes.parent.mkdir(parents=True, exist_ok=True)
                create_junction_or_link(project_agents, project_hermes, force=force)
                reconciled["project-hermes"] = {"path": str(project_hermes), "status": "linked_to_project_agents"}
        else:
            reconciled["project-hermes"] = {"path": str(project_hermes), "status": "healthy_junction"}

    return {
        "vault_dir": str(vdir),
        "total_canonical_skills": len(skills),
        "clients": reconciled,
    }


_SKILL_NAME_SAFE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


def _safe_skill_name(skill_name: str) -> Optional[str]:
    """Return sanitized skill name, or None if it escapes the allowed shape."""
    if not isinstance(skill_name, str) or not skill_name:
        return None
    stripped = skill_name.strip()
    if stripped in (".", "..") or ".." in stripped.split("/") or "\\" in stripped or "/" in stripped:
        return None
    if not _SKILL_NAME_SAFE_RE.fullmatch(stripped):
        return None
    return stripped


def get_patches_dir(vault_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    vdir = (vault_dir or get_vault_root()).resolve()
    pdir = vdir.parent / "patches"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def export_patch(skill_name: str, patch_content: str, vault_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    safe = _safe_skill_name(skill_name)
    if safe is None:
        raise ValueError(f"invalid skill name for patch export: {skill_name!r}")
    pdir = get_patches_dir(vault_dir)
    pfile = pdir / f"{safe}.patch"
    pfile.write_text(patch_content, encoding="utf-8")
    return pfile


def apply_patch(skill_name: str, patch_file: Optional[pathlib.Path] = None, vault_dir: Optional[pathlib.Path] = None) -> bool:
    safe = _safe_skill_name(skill_name)
    if safe is None:
        return False
    vdir = (vault_dir or get_vault_root()).resolve()
    target_skill = vdir / safe
    if not target_skill.is_dir() or not (target_skill / "SKILL.md").is_file():
        return False
    if is_skill_immutable(target_skill):
        return False
    pfile = patch_file or (get_patches_dir(vault_dir) / f"{safe}.patch")
    if not pfile.is_file():
        return False

    try:
        res = subprocess.run(
            ["git", "apply", "--ignore-whitespace", str(pfile)],
            cwd=str(target_skill),
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def rebase_patches(vault_dir: Optional[pathlib.Path] = None) -> dict[str, Any]:
    pdir = get_patches_dir(vault_dir)
    applied = []
    failed = []
    for pfile in sorted(pdir.glob("*.patch")):
        if not pfile.name.endswith(".patch"):
            continue
        safe = _safe_skill_name(pfile.stem)
        if safe is None:
            failed.append(pfile.stem)
            continue
        if apply_patch(safe, patch_file=pfile, vault_dir=vault_dir):
            applied.append(safe)
        else:
            failed.append(safe)
    return {"applied": applied, "failed": failed}


def is_skill_immutable(skill_path: pathlib.Path | str) -> bool:
    """Check if a skill is marked immutable/locked in its YAML frontmatter."""
    p = pathlib.Path(skill_path)
    md_file = p if p.is_file() else p / "SKILL.md"
    if not md_file.is_file():
        vault_root = get_vault_root()
        fallback = vault_root / skill_path / "SKILL.md"
        if fallback.is_file():
            md_file = fallback
        else:
            return False

    try:
        content = md_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return False
        for line in lines[1:50]:
            stripped = line.strip().lower()
            if stripped == "---":
                break
            if stripped.startswith("immutable:") and any(v in stripped for v in ("true", "yes", "1")):
                return True
            if stripped.startswith("governance:") and "locked" in stripped:
                return True
            if stripped.startswith("protected:") and any(v in stripped for v in ("true", "yes", "1")):
                return True
    except Exception:
        return False
    return False


def set_skill_readonly(skill_dir: pathlib.Path | str, readonly: bool = True) -> bool:
    """Apply or remove OS read-only attributes on all files in a skill directory."""
    p = pathlib.Path(skill_dir)
    target_dir = p.parent if p.is_file() else p
    if not target_dir.is_dir():
        return False

    try:
        for item in target_dir.rglob("*"):
            if item.is_file():
                try:
                    if os.name == "nt":
                        mode = stat.S_IREAD if readonly else (stat.S_IREAD | stat.S_IWRITE)
                    else:
                        current = item.stat().st_mode & 0o777
                        mode = (current & ~0o222) if readonly else (current | 0o200)
                    os.chmod(item, mode)
                except OSError:
                    pass
        return True
    except Exception:
        return False


