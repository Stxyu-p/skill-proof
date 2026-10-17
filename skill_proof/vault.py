"""
Skill Proof Global Vault Manager.

Provides a unified canonical skill repository at ~/.skill-proof/skills/
and manages synchronization and pinning across agent ecosystems
(Codex, Claude Code, Gemini CLI, Antigravity, Cursor, Hermes).
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, Mapping, Optional

DEFAULT_VAULT_ROOT = (
    pathlib.Path(os.environ["SKILL_PROOF_VAULT"]).expanduser()
    if os.environ.get("SKILL_PROOF_VAULT")
    else pathlib.Path.home() / ".skill-proof" / "skills"
)


def init_vault(vault_dir: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Ensure the global vault directory exists and is initialized."""
    target = (vault_dir or DEFAULT_VAULT_ROOT).resolve()
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


def list_vault_skills(vault_dir: Optional[pathlib.Path] = None) -> list[dict[str, Any]]:
    """List all skills in the global vault."""
    vdir = (vault_dir or DEFAULT_VAULT_ROOT).resolve()
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


def create_junction_or_link(source: pathlib.Path, target: pathlib.Path, force: bool = False) -> bool:
    """Create an NTFS junction on Windows or a symlink on Unix."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or _is_reparse(target):
        if target.is_dir() and _is_reparse(target):
            try:
                target.rmdir()
            except Exception:
                pass
        elif target.is_file() or target.is_symlink():
            target.unlink()
        elif force and target.is_dir():
            shutil.rmtree(target)
        else:
            return False

    if os.name == "nt":
        # Prefer mklink /J on Windows for unprivileged junction creation
        try:
            cmd = f'cmd /c mklink /J "{str(target)}" "{str(source)}"'
            res = subprocess.run(
                cmd, capture_output=True, text=True, check=False, shell=True
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
        # Fallback to os.symlink
        try:
            target.symlink_to(source, target_is_directory=True)
            return True
        except Exception:
            return False
    else:
        try:
            target.symlink_to(source, target_is_directory=True)
            return True
        except Exception:
            return False


def migrate_from_roots(
    roots: Mapping[str, os.PathLike[str] | str],
    vault_dir: Optional[pathlib.Path] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate physical skills from dispersed agent roots into the global vault."""
    vdir = init_vault(vault_dir) if not dry_run else (vault_dir or DEFAULT_VAULT_ROOT)
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

        # Collect both top-level skills and nested category skills (e.g. hermes/skills/<category>/<skill>/SKILL.md)
        candidate_dirs: list[pathlib.Path] = []
        for child in sorted(rp.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if (child / "SKILL.md").is_file():
                candidate_dirs.append(child)
            elif not _is_reparse(child):
                for sub_md in sorted(child.rglob("SKILL.md")):
                    if any(part.startswith(".") for part in sub_md.relative_to(rp).parts):
                        continue
                    candidate_dirs.append(sub_md.parent)

        for child in candidate_dirs:
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

            dest = vdir / child.name
            if dest.exists():
                dest_md = dest / "SKILL.md"
                try:
                    src_bytes = skill_md.read_bytes()
                    dst_bytes = dest_md.read_bytes() if dest_md.is_file() else b""
                except OSError:
                    src_bytes, dst_bytes = b"", b""
                # Upgrade vault copy if source has richer content
                if len(src_bytes) > len(dst_bytes):
                    if not dry_run:
                        try:
                            shutil.rmtree(dest)
                            shutil.copytree(child, dest, symlinks=False)
                            migrated.append({"skill": child.name, "source": str(child), "upgraded": True})
                        except Exception as e:
                            skipped.append({"skill": child.name, "source": str(child), "reason": str(e)})
                    else:
                        migrated.append({"skill": child.name, "source": str(child), "upgraded": True})
                else:
                    skipped.append(
                        {
                            "skill": child.name,
                            "source": str(child),
                            "reason": "already_in_vault",
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
    vdir = (vault_dir or DEFAULT_VAULT_ROOT).resolve()
    source = vdir / skill_name
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        return {
            "success": False,
            "error": f"skill '{skill_name}' not found in vault {vdir}",
        }

    dest = target_root.resolve() / skill_name
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
    dest = target_root.resolve() / skill_name
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
    vdir = (vault_dir or DEFAULT_VAULT_ROOT).resolve()
    vault_exists = vdir.is_dir()
    skills = list_vault_skills(vdir) if vault_exists else []

    client_stats: dict[str, Any] = {}
    if roots:
        for root_id, root_path in sorted(roots.items()):
            rp = pathlib.Path(root_path).expanduser()
            if not rp.is_dir():
                continue
            total = 0
            pinned_to_vault = 0
            local_physical = 0
            for child in rp.iterdir():
                if not child.is_dir():
                    continue
                if not (child / "SKILL.md").is_file():
                    continue
                total += 1
                if _is_reparse(child):
                    try:
                        tgt = child.resolve()
                        if tgt.is_relative_to(vdir):
                            pinned_to_vault += 1
                        else:
                            local_physical += 1
                    except Exception:
                        local_physical += 1
                else:
                    local_physical += 1
            client_stats[root_id] = {
                "path": str(rp),
                "total_skills": total,
                "pinned_to_vault": pinned_to_vault,
                "local_physical": local_physical,
            }

    return {
        "vault_dir": str(vdir),
        "vault_exists": vault_exists,
        "vault_skills_count": len(skills),
        "skills": [s["name"] for s in skills],
        "clients": client_stats,
    }


def sync(
    vault_dir: Optional[pathlib.Path] = None,
    roots: Optional[Mapping[str, os.PathLike[str] | str]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Full synchronization: migrate physical skills to vault and ensure links are healthy."""
    vdir = init_vault(vault_dir) if not dry_run else (vault_dir or DEFAULT_VAULT_ROOT)
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
    vdir = (vault_dir or DEFAULT_VAULT_ROOT).resolve()
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

