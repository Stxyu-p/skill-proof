from __future__ import annotations

"""Catalog layer: catalog types, SKILL.md scanning, frontmatter parsing,
filesystem and text predicates (v0.16 module split)."""

import hashlib
import json
import os
import pathlib
import re
import stat
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional, Sequence


_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._: -]{0,126}[A-Za-z0-9._:-])?$")


_EXCLUDED_DIRS = {
    ".archive",
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}


# ponytail: lru_cache on normalize_identifier cuts repeated NFKC normalization
@lru_cache(maxsize=8192)
def normalize_identifier(value: str) -> str:
    """Normalize a human identifier for equality, never for display."""
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def normalize_identifiers(names: Iterable[str]) -> frozenset[str]:
    """Return a frozenset of non-empty normalized identifiers from an iterable."""
    return frozenset(norm for name in names if (norm := normalize_identifier(name)))


@dataclass(frozen=True)
class Diagnostic:
    code: str
    root_id: str
    relative_path: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SkillInvariants:
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    ordered_tools: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.required_tools and not self.forbidden_tools and not self.ordered_tools


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    normalized_name: str
    description: str
    tags: tuple[str, ...]
    root_id: str
    relative_path: str
    source_sha256: str
    source_bytes: int
    root_path: pathlib.Path
    source_path: pathlib.Path
    invariants: SkillInvariants = SkillInvariants()
    aliases: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalog:
    skills: tuple[SkillRecord, ...]
    diagnostics: tuple[Diagnostic, ...]
    catalog_hash: str
    # Derived lookup tables for candidate pruning; built once on construction
    # and excluded from equality/hash comparisons.
    index: Optional["CatalogIndex"] = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.index is None:
            from _ranking import _build_catalog_index  # local import: type<->index cycle
            object.__setattr__(self, "index", _build_catalog_index(self.skills))

    def by_name(self, name: str) -> tuple[SkillRecord, ...]:
        wanted = normalize_identifier(name)
        return tuple(skill for skill in self.skills if skill.normalized_name == wanted)


class _FrontmatterError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _parse_scalar(raw: str, field: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] == '"':
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise _FrontmatterError("invalid_frontmatter", f"invalid quoted {field}") from exc
        if not isinstance(parsed, str):
            raise _FrontmatterError("invalid_frontmatter", f"{field} must be a scalar")
        return parsed.strip()
    if value[0] == "'":
        if len(value) < 2 or value[-1] != "'":
            raise _FrontmatterError("invalid_frontmatter", f"invalid quoted {field}")
        return value[1:-1].replace("''", "'").strip()
    if value[0] in "[{|>" or value.startswith("&") or value.startswith("*"):
        raise _FrontmatterError("invalid_frontmatter", f"unsupported {field} scalar")
    return value.strip()


def _parse_list_value(
    lines: list[str], index: int, closing: int, value: str
) -> tuple[tuple[str, ...], int]:
    """Parse an inline list or a YAML block list ("tags:" then "  - ui" lines)."""
    if value.strip():
        return _parse_inline_tags(value), index + 1
    cursor = index + 1
    collected: list[str] = []
    while cursor < closing:
        subline = lines[cursor]
        if subline.strip() and not subline[0].isspace():
            break
        stripped = subline.strip()
        if stripped.startswith("-"):
            collected.append(stripped[1:].strip())
        cursor += 1
    if not collected:
        return (), cursor
    return _parse_inline_tags("[" + ", ".join(collected) + "]"), cursor


def _parse_inline_tags(raw: str) -> tuple[str, ...]:
    value = raw.strip()
    if not value:
        return ()
    if value.startswith("[") and value.endswith("]"):
        pieces = value[1:-1].split(",")
    else:
        pieces = [value]
    tags = []
    for piece in pieces:
        tag = _parse_scalar(piece, "tag")
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags[:32])


def _parse_skill(
    raw: bytes,
    *,
    root_id: str,
    relative_path: str,
    root_path: pathlib.Path,
    source_path: pathlib.Path,
) -> SkillRecord:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _FrontmatterError("invalid_utf8", "SKILL.md is not valid UTF-8") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise _FrontmatterError("invalid_frontmatter", "frontmatter must start at byte zero")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise _FrontmatterError("invalid_frontmatter", "missing closing frontmatter delimiter") from exc
    body = "\n".join(lines[closing + 1 :]).strip()
    if not body:
        raise _FrontmatterError("invalid_frontmatter", "instruction body is empty")

    fields: dict[str, str] = {}
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    invariants_data: dict[str, tuple[str, ...]] = {}
    skip_until = 0
    for index, line in enumerate(lines[1:closing], start=1):
        if index < skip_until:
            continue
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        stripped_key = key.strip()
        is_top_level = len(key) == len(key.lstrip())
        if is_top_level and stripped_key in {"name", "description"}:
            if stripped_key in fields:
                raise _FrontmatterError("invalid_frontmatter", f"duplicate {stripped_key}")
            if stripped_key == "description" and re.fullmatch(r"[>|][+-]?(?:\s+#.*)?", value.strip()):
                parts = []
                cursor = index + 1
                while cursor < closing:
                    continuation = lines[cursor]
                    if continuation.strip() and not continuation.startswith(' '):
                        break
                    parts.append(continuation.strip())
                    cursor += 1
                skip_until = cursor
                # Description is search metadata: normalize block whitespace rather
                # than promising full YAML folding/chomping semantics.
                fields[stripped_key] = ' '.join(' '.join(parts).split())
            else:
                fields[stripped_key] = _parse_scalar(value, stripped_key)
        elif stripped_key in {"tags", "aliases", "related_skills", "related"}:
            parsed_list, skip_until = _parse_list_value(lines, index, closing, value)
            if stripped_key == "tags":
                tags = parsed_list
            elif stripped_key == "aliases":
                aliases = parsed_list
            else:
                related_skills = parsed_list
        elif is_top_level and stripped_key == "invariants":
            cursor = index + 1
            inv_lines = []
            while cursor < closing:
                subline = lines[cursor]
                if subline.strip() and len(subline) == len(subline.lstrip()):
                    break
                if subline.strip() and not subline.lstrip().startswith("#"):
                    inv_lines.append(subline)
                cursor += 1
            skip_until = cursor
            for inv_line in inv_lines:
                if ":" in inv_line:
                    ik, iv = inv_line.split(":", 1)
                    ik_norm = normalize_identifier(ik)
                    if ik_norm in {"required_tools", "forbidden_tools", "ordered_tools"}:
                        invariants_data[ik_norm] = tuple(
                            normalize_identifier(x) for x in _parse_inline_tags(iv) if normalize_identifier(x)
                        )

    name = fields.get("name", "")
    description = fields.get("description", "")
    if not name or not description:
        raise _FrontmatterError("invalid_frontmatter", "name and description are required")
    if not _NAME_RE.fullmatch(name):
        raise _FrontmatterError("invalid_frontmatter", "name contains unsupported characters")

    stable_ref = f"{root_id}:{relative_path}:{normalize_identifier(name)}"
    skill_id = "skl-" + hashlib.sha256(stable_ref.encode("utf-8")).hexdigest()[:20]
    inv_obj = SkillInvariants(
        required_tools=invariants_data.get("required_tools", ()),
        forbidden_tools=invariants_data.get("forbidden_tools", ()),
        ordered_tools=invariants_data.get("ordered_tools", ()),
    )
    return SkillRecord(
        skill_id=skill_id,
        name=name,
        normalized_name=normalize_identifier(name),
        description=description,
        tags=tags,
        root_id=root_id,
        relative_path=relative_path,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_bytes=len(raw),
        root_path=root_path,
        source_path=source_path,
        invariants=inv_obj,
        aliases=aliases,
        related_skills=related_skills,
    )


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except (OSError, ValueError):
        return False


def _is_reparse_point(path: pathlib.Path) -> bool:
    """True for symlinks, Windows junctions, and other reparse points.

    ``Path.is_symlink`` misses junctions on Windows (Python 3.11), so junction
    directories were silently walked into and only rejected later as the
    generic ``unsafe_path``.  Classify them explicitly instead.
    """
    try:
        if path.is_symlink():
            return True
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes and attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    return bool(getattr(info, "st_reparse_tag", 0))


def _apply_cross_root_precedence(
    found: list[SkillRecord], roots: Mapping[str, os.PathLike[str] | str], diagnostics: list[Diagnostic]
) -> list[SkillRecord]:
    """Resolve duplicate skill names across roots by configured root order.

    The first configured root that indexes a name wins.  Cross-root twins that
    are byte-identical collapse as ``alias_skipped`` (a junction facade plus its
    physical store); divergent copies of the same name are reported as
    ``shadowed_by_root`` so the winner stays auditable.  Same-name records
    INSIDE one root remain ambiguous ``duplicate_name`` (fail closed).
    """
    priority = {str(root_id): index for index, root_id in enumerate(roots)}
    same_root_twins: set[str] = set()
    seen_per_root: dict[str, set[str]] = {}
    for skill in found:
        names_in_root = seen_per_root.setdefault(skill.root_id, set())
        if skill.normalized_name in names_in_root:
            same_root_twins.add(skill.normalized_name)
        names_in_root.add(skill.normalized_name)
    ordered = sorted(
        found,
        key=lambda skill: (
            priority.get(skill.root_id, len(priority)),
            skill.normalized_name,
            skill.root_id,
            skill.relative_path,
        ),
    )
    winners: dict[str, SkillRecord] = {}
    survivors: list[SkillRecord] = []
    for skill in ordered:
        if skill.normalized_name in same_root_twins:
            survivors.append(skill)
            continue
        current = winners.get(skill.normalized_name)
        if current is None:
            winners[skill.normalized_name] = skill
            survivors.append(skill)
            continue
        identical = (
            skill.source_sha256 == current.source_sha256 and skill.source_bytes == current.source_bytes
        )
        diagnostics.append(
            Diagnostic(
                "alias_skipped" if identical else "shadowed_by_root",
                skill.root_id,
                skill.relative_path,
                skill.name,
            )
        )
    return survivors


_HUB_LOCK_MAX_BYTES = 8 * 1024 * 1024


_HUB_BUNDLE_FILE_MAX_BYTES = 4 * 1024 * 1024


_HUB_BUNDLE_TOTAL_MAX_BYTES = 16 * 1024 * 1024


_BUNDLE_SHA_CACHE: dict[tuple[str, tuple[str, ...], int], Optional[str]] = {}


def _bundle_mtime_signature(folder: pathlib.Path, files: Sequence[str]) -> Optional[int]:
    """Newest mtime (ns) across the bundle files; None when any stat fails."""
    newest = 0
    try:
        for name in files:
            clean = str(name).replace("\\", "/")
            parts = pathlib.PurePosixPath(clean).parts
            if not clean or clean.startswith("/") or ".." in parts:
                return None
            candidate = folder.joinpath(*parts)
            if candidate.is_symlink() or not candidate.is_file():
                return None
            newest = max(newest, candidate.stat().st_mtime_ns)
    except OSError:
        return None
    return newest


def _bundle_sha256(folder: pathlib.Path, files: Sequence[str]) -> Optional[str]:
    """Hub-compatible bundle hash: sha256 over sorted ``name\\0content`` pairs.

    Mirrors the Hermes hub's skills-guard bundle hash (verified against live
    lock entries) so local bytes can be compared with recorded provenance.
    An unchanged newest-mtime signature reuses the previous digest (plan 3.7).
    """
    names = tuple(sorted(str(item) for item in files))
    signature = _bundle_mtime_signature(folder, names) if names else None
    cache_key = (str(folder), names, signature if signature is not None else -1)
    if signature is not None and cache_key in _BUNDLE_SHA_CACHE:
        return _BUNDLE_SHA_CACHE[cache_key]
    digest = hashlib.sha256()
    total = 0
    for name in names:
        clean = name.replace("\\", "/")
        parts = pathlib.PurePosixPath(clean).parts
        if not clean or clean.startswith("/") or ".." in parts:
            return None
        candidate = folder.joinpath(*parts)
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return None
            size = candidate.stat().st_size
            if size > _HUB_BUNDLE_FILE_MAX_BYTES:
                return None
            raw = candidate.read_bytes()
        except OSError:
            return None
        total += len(raw)
        if total > _HUB_BUNDLE_TOTAL_MAX_BYTES:
            return None
        digest.update(clean.encode("utf-8") + b"\0" + raw)
    result = digest.hexdigest()
    if signature is not None:
        _BUNDLE_SHA_CACHE[cache_key] = result
    return result


def default_hermes_home(base_home: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Canonical path to the Hermes data directory."""
    configured = os.environ.get("HERMES_HOME", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if os.name == "nt" and local_app_data:
        return pathlib.Path(local_app_data) / "hermes"
    home = base_home if base_home is not None else pathlib.Path.home()
    return home / ".hermes"


# ponytail: mtime-based cache cuts repeated config.yaml parses from 40ms to 0.001ms
_DISABLED_SKILLS_CACHE: list[Any] = [None, None, frozenset()]


def load_disabled_skills(hermes_home: Optional[os.PathLike[str] | str] = None) -> frozenset[str]:
    """Normalized disabled skill names from Hermes config.yaml (cached by mtime)."""
    global _DISABLED_SKILLS_CACHE
    h_home = pathlib.Path(hermes_home).expanduser() if hermes_home else default_hermes_home()
    cfg_path = h_home / "config.yaml"
    if not cfg_path.is_file():
        return frozenset()
    mtime_ns = None
    try:
        mtime_ns = cfg_path.stat().st_mtime_ns
        if _DISABLED_SKILLS_CACHE[0] == str(cfg_path) and _DISABLED_SKILLS_CACHE[1] == mtime_ns:
            return _DISABLED_SKILLS_CACHE[2]
    except OSError:
        pass
    res: frozenset[str] = frozenset()
    try:
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        disabled = data.get("skills", {}).get("disabled", []) or []
        res = frozenset(normalize_identifier(x) for x in disabled if x) - {normalize_identifier("hermes-agent")}
    except Exception:
        try:
            content = cfg_path.read_text(encoding="utf-8")
            m = re.search(r"^skills:\s*\n(?:[ \t]+[^\n]+\n)*?[ \t]+disabled:\s*\n((?:[ \t]+-[^\n]+\n)+)", content, re.MULTILINE)
            if m:
                names = set()
                for line in m.group(1).splitlines():
                    line = line.strip()
                    if line.startswith("-"):
                        val = line[1:].strip().strip("\"'")
                        if val:
                            names.add(normalize_identifier(val))
                res = frozenset(names) - {normalize_identifier("hermes-agent")}
        except Exception:
            res = frozenset()
    if mtime_ns is not None:
        _DISABLED_SKILLS_CACHE = [str(cfg_path), mtime_ns, res]
    return res


def detect_agent_roots(
    home: Optional[os.PathLike[str] | str] = None,
    cwd: Optional[os.PathLike[str] | str] = None,
    hermes_home: Optional[os.PathLike[str] | str] = None,
    profile: Optional[str] = None,
) -> dict[str, pathlib.Path]:
    """Existing skill roots across common agent ecosystems (id -> path).

    The shared ``SKILL.md`` layout is used by many hosts (Codex, Claude Code,
    Gemini/Antigravity CLI, Cursor, OpenCode, Cline, ...), and the ``skills``
    CLI keeps its canonical store in ``~/.agents/skills``.  Only directories
    that exist are returned; caller order is the matching precedence order.
    """
    base_home = pathlib.Path(home).expanduser() if home else pathlib.Path.home()
    base_cwd = pathlib.Path(cwd).expanduser() if cwd else pathlib.Path.cwd()
    candidates: list[tuple[str, pathlib.Path]] = [
        ("project-agents", base_cwd / ".agents" / "skills"),
        ("project-claude", base_cwd / ".claude" / "skills"),
        ("vault", base_home / ".skill-proof" / "skills"),
        ("agents", base_home / ".agents" / "skills"),
    ]
    if profile:
        profile_candidates: list[pathlib.Path] = []
        if hermes_home is not None:
            if str(hermes_home).strip():
                profile_candidates.append(pathlib.Path(hermes_home).expanduser() / "profiles" / profile / "skills")
        else:
            profile_candidates.append(default_hermes_home(base_home) / "profiles" / profile / "skills")
            if default_hermes_home(base_home) != base_home / ".hermes":
                profile_candidates.append(base_home / ".hermes" / "profiles" / profile / "skills")
        for p_cand in profile_candidates:
            candidates.insert(0, (f"profile-{profile}", p_cand))
    if hermes_home is not None:  # explicit override; empty string disables detection
        if str(hermes_home).strip():
            candidates.append(("hermes", pathlib.Path(hermes_home).expanduser() / "skills"))
    else:
        h_home = default_hermes_home(base_home)
        candidates.append(("hermes", h_home / "skills"))
        if h_home != base_home / ".hermes":
            candidates.append(("hermes-home", base_home / ".hermes" / "skills"))
    candidates.extend(
        [
            ("claude", base_home / ".claude" / "skills"),
            ("codex", base_home / ".codex" / "skills"),
            ("gemini", base_home / ".gemini" / "skills"),
            ("gemini-config", base_home / ".gemini" / "config" / "skills"),
            ("antigravity", base_home / ".gemini" / "antigravity" / "skills"),
            ("antigravity-cli", base_home / ".gemini" / "antigravity-cli" / "skills"),
            ("opencode", base_home / ".config" / "opencode" / "skills"),
            ("copilot", base_home / ".copilot" / "skills"),
            ("cursor", base_home / ".cursor" / "skills"),
            ("windsurf", base_home / ".codeium" / "windsurf" / "skills"),
            ("kilo", base_home / ".kilo" / "skills"),
        ]
    )
    detected: dict[str, pathlib.Path] = {}
    for root_id, path in candidates:
        try:
            if path.is_dir() and not path.is_symlink():
                detected[root_id] = path
        except OSError:
            continue
    return detected


def _is_ignored_dirname(dirname: str) -> bool:
    return dirname in _EXCLUDED_DIRS or dirname.startswith((".", "_"))


def suggest_roots(
    roots: Optional[Mapping[str, os.PathLike[str] | str]] = None, *, limit: int = 3
) -> list[dict[str, Any]]:
    """Recommend canonical root directories behind reparse-point facades.

    Read-only walk over the configured roots: junction/symlink directories that
    directly own a SKILL.md are resolved and their target parents grouped.
    """
    if not roots:
        return []
    configured: set[str] = set()
    counts: Counter[str] = Counter()
    for _root_id, raw_root in sorted(roots.items(), key=lambda item: str(item[0])):
        root = pathlib.Path(raw_root).expanduser()
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        configured.add(str(resolved_root))
        for current, dirs, files in os.walk(resolved_root, topdown=True, followlinks=False):
            current_path = pathlib.Path(current)
            keep: list[str] = []
            for dirname in sorted(dirs):
                child = current_path / dirname
                if _is_ignored_dirname(dirname):
                    continue
                if child.is_symlink():
                    continue
                if _is_reparse_point(child):
                    try:
                        target = child.resolve(strict=True)
                    except (OSError, RuntimeError):
                        continue
                    if (target / "SKILL.md").is_file():
                        counts[str(target.parent)] += 1
                    continue
                keep.append(dirname)
            dirs[:] = keep
            if "SKILL.md" in files:
                dirs[:] = []
    suggestions: list[dict[str, Any]] = []
    for parent, count in counts.most_common():
        if parent in configured:
            continue
        suggestions.append({"path": parent, "skills": count})
        if len(suggestions) >= max(1, int(limit)):
            break
    return suggestions


def _tree_fingerprint(
    roots,
    max_skill_bytes,
):
    """Cheap whole-tree snapshot for the scan fast path (os.scandir stats).

    Returns None when any root is unreadable (caller runs the full secure
    scan). Otherwise (sorted root ids, root entry counts, per-skill-dir
    SKILL.md mtime+size) - enough to detect add/remove/edit/rename of
    skills without opening a single file. Directory mtimes change on child
    create/delete/rename; SKILL.md mtime+size catches content edits.
    """
    try:
        parts = []
        for raw_root_id, raw_root in sorted(roots.items(), key=lambda item: str(item[0])):
            root = pathlib.Path(raw_root).expanduser()
            try:
                resolved = root.resolve(strict=True)
            except (OSError, RuntimeError):
                return None
            if not resolved.is_dir():
                return None
            entries = []
            with os.scandir(resolved) as top:
                for entry in top:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        return None
                    entries.append((entry.name, is_dir))
            entries.sort()
            parts.append((str(raw_root_id).strip(), len(entries)))
            for name, is_dir in entries:
                if not is_dir or name.startswith(".") or name == "__pycache__":
                    continue
                if name == "node_modules":
                    continue
                skill = resolved / name / "SKILL.md"
                try:
                    st = skill.stat()
                    parts.append((name, st.st_mtime_ns, st.st_size))
                except OSError:
                    parts.append((name, None))
        return (max_skill_bytes, tuple(parts))
    except Exception:
        return None


def scan_catalog(
    roots: Mapping[str, os.PathLike[str] | str],
    *,
    max_skill_bytes: int = 256 * 1024,
    cache: Optional[dict] = None,
    metrics: Optional[dict] = None,
    disabled: Optional[Iterable[str]] = None,
) -> Catalog:
    """Discover safe `SKILL.md` files with deterministic ordering."""
    if isinstance(max_skill_bytes, bool) or max_skill_bytes < 1:
        raise ValueError("max_skill_bytes must be a positive integer")
    found: list[SkillRecord] = []
    diagnostics: list[Diagnostic] = []
    live_cache_keys = set()
    if metrics is not None:
        metrics.update(cache_hits=0, cache_misses=0)
    disabled_set = load_disabled_skills() if disabled is None else normalize_identifiers(disabled)
    # Fast path: unchanged tree (by cheap scandir fingerprint) reuses the last
    # Catalog object as-is. Security posture unchanged: any add/remove/edit
    # changes the fingerprint and runs the full secure scan below.
    if cache is not None:
        fp = _tree_fingerprint(roots, max_skill_bytes)
        # disabled_set participates: same tree + different exclusions is a miss.
        fp = (fp, tuple(sorted(disabled_set))) if fp is not None else None
        if fp is not None and cache.get("__tree_fp__") == fp and "__catalog__" in cache:
            if metrics is not None:
                metrics.update(cache_hits=len(cache.get("__live_keys__", ())), cache_misses=0, tree_cache_hit=True)
            return cache["__catalog__"]
        cache["__tree_fp__"] = fp

    all_resolved_roots: list[pathlib.Path] = []
    for raw_r in roots.values():
        try:
            rr = pathlib.Path(raw_r).expanduser().resolve(strict=True)
            if rr.is_dir():
                all_resolved_roots.append(rr)
        except (OSError, RuntimeError):
            pass

    for raw_root_id, raw_root in sorted(roots.items(), key=lambda item: str(item[0])):
        root_id = str(raw_root_id).strip()
        root = pathlib.Path(raw_root).expanduser()
        if not root_id:
            raise ValueError("root id must not be empty")
        if root.is_symlink():
            diagnostics.append(Diagnostic("unsafe_root", root_id))
            continue
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            diagnostics.append(Diagnostic("missing_root", root_id))
            continue
        if not resolved_root.is_dir():
            diagnostics.append(Diagnostic("invalid_root", root_id))
            continue

        for current, dirs, files in os.walk(resolved_root, topdown=True, followlinks=False):
            current_path = pathlib.Path(current)
            safe_dirs = []
            for dirname in sorted(dirs):
                child = current_path / dirname
                relative = child.relative_to(resolved_root).as_posix()
                if _is_ignored_dirname(dirname):
                    continue
                if child.is_symlink():
                    diagnostics.append(Diagnostic("unsafe_symlink", root_id, relative))
                    continue
                if _is_reparse_point(child):
                    try:
                        resolved_child = child.resolve(strict=True)
                        if any(_is_within(resolved_child, r) for r in all_resolved_roots):
                            continue
                    except (OSError, RuntimeError):
                        pass
                    diagnostics.append(Diagnostic("unsafe_reparse", root_id, relative))
                    continue
                safe_dirs.append(dirname)
            dirs[:] = safe_dirs

            if "SKILL.md" not in files:
                continue
            dirs[:] = []  # a skill directory owns all nested support files
            skill_path = current_path / "SKILL.md"
            relative_path = skill_path.relative_to(resolved_root).as_posix()
            if skill_path.is_symlink():
                diagnostics.append(Diagnostic("unsafe_symlink", root_id, relative_path))
                continue
            try:
                resolved_skill = skill_path.resolve(strict=True)
            except (OSError, RuntimeError):
                diagnostics.append(Diagnostic("unreadable_skill", root_id, relative_path))
                continue
            if not _is_within(resolved_skill, resolved_root) or not resolved_skill.is_file():
                diagnostics.append(Diagnostic("unsafe_path", root_id, relative_path))
                continue
            try:
                stat = resolved_skill.stat()
                cache_key = (root_id, str(resolved_skill), max_skill_bytes)
                fingerprint = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
                live_cache_keys.add(cache_key)
                cached = cache.get(cache_key) if cache is not None else None
                if cached is not None and cached[0] == fingerprint:
                    if metrics is not None:
                        metrics['cache_hits'] += 1
                    found.append(cached[1])
                    continue
                if metrics is not None:
                    metrics['cache_misses'] += 1
                with resolved_skill.open("rb") as handle:
                    raw = handle.read(max_skill_bytes + 1)
            except OSError:
                diagnostics.append(Diagnostic("unreadable_skill", root_id, relative_path))
                continue
            if len(raw) > max_skill_bytes:
                diagnostics.append(Diagnostic("skill_too_large", root_id, relative_path))
                continue
            try:
                found.append(
                    _parse_skill(
                        raw,
                        root_id=root_id,
                        relative_path=relative_path,
                        root_path=resolved_root,
                        source_path=skill_path,
                    )
                )
                if cache is not None:
                    cache[cache_key] = (fingerprint, found[-1])
            except _FrontmatterError as exc:
                diagnostics.append(Diagnostic(exc.code, root_id, relative_path, exc.detail))

    if cache is not None:
        for key in set(cache) - live_cache_keys:
            if isinstance(key, str) and key.startswith("__"):
                continue
            del cache[key]
    found = _apply_cross_root_precedence(found, roots, diagnostics)
    if disabled_set:
        found = [skill for skill in found if skill.normalized_name not in disabled_set]
    found.sort(key=lambda skill: (skill.normalized_name, skill.root_id, skill.relative_path))
    name_counts: dict[str, int] = {}
    for skill in found:
        name_counts[skill.normalized_name] = name_counts.get(skill.normalized_name, 0) + 1
    for skill in found:
        if name_counts[skill.normalized_name] > 1:
            diagnostics.append(Diagnostic("duplicate_name", skill.root_id, skill.relative_path, skill.name))
    diagnostics.sort(key=lambda item: (item.code, item.root_id, item.relative_path, item.detail))
    catalog_payload = [
        {
            "id": skill.skill_id,
            "name": skill.normalized_name,
            "description": skill.description,
            "tags": list(skill.tags),
            "aliases": list(skill.aliases),
            "root": skill.root_id,
            "path": skill.relative_path,
            "sha256": skill.source_sha256,
        }
        for skill in found
    ]
    catalog_hash = hashlib.sha256(
        json.dumps(catalog_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    catalog = Catalog(tuple(found), tuple(diagnostics), catalog_hash)
    if cache is not None:
        cache["__catalog__"] = catalog
        cache["__live_keys__"] = frozenset(live_cache_keys)
    return catalog


def extract_explicit_skill_names(query: str, *, known_names=None) -> tuple[str, ...]:
    """Extract only unambiguous, explicit skill syntax from a user query."""
    text = unicodedata.normalize("NFKC", str(query or ""))
    known = None if known_names is None else normalize_identifiers(known_names)
    identifier = r"([A-Za-z0-9][A-Za-z0-9._:-]{0,127})"
    patterns = (
        re.compile(r"\$" + identifier),
        re.compile(r"(?i)\b(?:use|using|load)\s+(?:the\s+)?skills?\s+[`\"']?" + identifier),
        re.compile(r"(?:ใช้|โหลด)\s*(?:skill|สกิล)\s*[`\"']?" + identifier, re.IGNORECASE),
        re.compile(r"(?:skill|สกิล)\s*[:=]\s*[`\"']?" + identifier, re.IGNORECASE),
    )
    seen: set[str] = set()
    names: list[str] = []
    continuation = re.compile(
        r"\s*[`\"']?\s*(?:,|\band\b|กับ|และ)\s*(?:skills?\s+|สกิล\s*)?[`\"']?" + identifier,
        re.IGNORECASE,
    )
    for index, pattern in enumerate(patterns):
        for match in pattern.finditer(text):
            # Natural-language plugin references are not requests to load a skill.
            # Strong $name / skill:name syntax remains authoritative, even unknown.
            if index in (1, 2) and re.match(r"\s+plugin\b", text[match.end():], re.IGNORECASE):
                continue
            matches = [match]
            tail = match.end()
            while index != 0:
                extra = continuation.match(text, tail)
                if extra is None:
                    break
                # In a real turn, 'and summarize it' is an action, not a second
                # skill. Unknown skills remain explicit via $name / skill:name.
                if known is not None and normalize_identifier(extra.group(1).rstrip('.,;:!?)]}')) not in known:
                    break
                matches.append(extra)
                tail = extra.end()
            for item in matches:
                name = item.group(1).rstrip(".,;:!?)]}")
                normalized = normalize_identifier(name)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    names.append(name)
    return tuple(names)


# Single source of truth (zero-spaghetti law 1): every spaceless-script
# range lives in _SPACELESS_RANGES below; these predicates only slice it.
_SEA_RANGE_COUNT = 4  # Thai, Lao, Myanmar, Khmer: first entries of the table


def _is_sea_spaceless_character(character: str) -> bool:
    code = ord(character)
    return any(lo <= code <= hi for lo, hi in _SPACELESS_RANGES[:_SEA_RANGE_COUNT])


@lru_cache(maxsize=2048)
def _identifier_pattern(normalized_identifier: str) -> "re.Pattern[str]":
    # Hyphen is a boundary too: "frontend-design" must not match inside
    # "frontend-design-pro". The negation patterns already refuse it.
    return re.compile(rf"(?<![\w-]){re.escape(normalized_identifier)}(?![\w-])")


def _identifier_in_query(
    identifier: str, query: str, norm_query: Optional[str] = None
) -> bool:
    norm_id = normalize_identifier(identifier)
    norm_q = norm_query if norm_query is not None else normalize_identifier(query)
    if not norm_id or norm_id not in norm_q:
        return False
    return bool(_identifier_pattern(norm_id).search(norm_q))


# Scripts that do not separate words with spaces: raw substring matching is the
# only workable rule for them, and it is language-agnostic (no dictionary).
_SPACELESS_RANGES = (
    (0x0E00, 0x0E7F),  # Thai
    (0x0E80, 0x0EFF),  # Lao
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0x1100, 0x11FF),  # Hangul Jamo
)


def _is_spaceless_character(character: str) -> bool:
    code = ord(character)
    return any(lo <= code <= hi for lo, hi in _SPACELESS_RANGES)


# ponytail: lru_cache on _contains_spaceless_script cuts repeated character category loops
@lru_cache(maxsize=4096)
def _contains_spaceless_script(value: str) -> bool:
    return any(_is_spaceless_character(character) for character in str(value or ""))


@lru_cache(maxsize=2048)
def _phrase_boundary_pattern(shorter: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<!\w){re.escape(shorter)}(?!\w)")


def _phrase_present(shorter: str, longer: str) -> bool:
    """True when *shorter* occurs in *longer* as a complete token span.

    Spaceless scripts (Thai, CJK, ...) keep raw substring matching:
    there are no word boundaries to split on. Anything else must match on
    token boundaries so "ui" never fires inside "build".
    """
    if not shorter or shorter not in longer:
        return False
    if " " not in shorter and " " not in longer and _contains_spaceless_script(longer):
        return True
    return bool(_phrase_boundary_pattern(shorter).search(longer))
