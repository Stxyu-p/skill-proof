"""Hermes-independent core for Skill Proof.

Discovery treats skill documents as untrusted data.  This module deliberately
implements a small, documented YAML-like frontmatter subset instead of claiming
to parse arbitrary YAML without a dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from math import log
from typing import Any, Mapping, Optional, Sequence


__version__ = "0.8.0"


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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


def normalize_identifier(value: str) -> str:
    """Normalize a human identifier for equality, never for display."""
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


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


@dataclass(frozen=True)
class Catalog:
    skills: tuple[SkillRecord, ...]
    diagnostics: tuple[Diagnostic, ...]
    catalog_hash: str

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
        elif stripped_key in {"tags", "aliases"}:
            parsed_list, skip_until = _parse_list_value(lines, index, closing, value)
            if stripped_key == "tags":
                tags = parsed_list
            else:
                aliases = parsed_list
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


def _bundle_sha256(folder: pathlib.Path, files: Sequence[str]) -> Optional[str]:
    """Hub-compatible bundle hash: sha256 over sorted ``name\\0content`` pairs.

    Mirrors the Hermes hub's skills-guard bundle hash (verified against live
    lock entries) so local bytes can be compared with recorded provenance.
    """
    digest = hashlib.sha256()
    total = 0
    for name in sorted(str(item) for item in files):
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
    return digest.hexdigest()


def detect_agent_roots(
    home: Optional[os.PathLike[str] | str] = None,
    cwd: Optional[os.PathLike[str] | str] = None,
    hermes_home: Optional[os.PathLike[str] | str] = None,
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
        ("agents", base_home / ".agents" / "skills"),
    ]
    if hermes_home is not None:  # explicit override; empty string disables detection
        if str(hermes_home).strip():
            candidates.append(("hermes", pathlib.Path(hermes_home).expanduser() / "skills"))
    else:
        configured_home = os.environ.get("HERMES_HOME", "").strip()
        if configured_home:
            candidates.append(("hermes", pathlib.Path(configured_home).expanduser() / "skills"))
        else:
            local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
            if os.name == "nt" and local_app_data:
                candidates.append(("hermes", pathlib.Path(local_app_data) / "hermes" / "skills"))
            candidates.append(("hermes-home", base_home / ".hermes" / "skills"))
    candidates.extend(
        [
            ("claude", base_home / ".claude" / "skills"),
            ("gemini", base_home / ".gemini" / "skills"),
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
            if path.is_dir():
                detected[root_id] = path
        except OSError:
            continue
    return detected


def suggest_roots(
    roots: Mapping[str, os.PathLike[str] | str], *, limit: int = 3
) -> list[dict[str, Any]]:
    """Recommend canonical root directories behind reparse-point facades.

    Read-only walk over the configured roots: junction/symlink directories that
    directly own a SKILL.md are resolved and their target parents grouped.
    """
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
                if dirname in _EXCLUDED_DIRS or dirname.startswith(".") or dirname.startswith("_"):
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


def scan_catalog(
    roots: Mapping[str, os.PathLike[str] | str],
    *,
    max_skill_bytes: int = 256 * 1024,
    cache: Optional[dict] = None,
    metrics: Optional[dict] = None,
) -> Catalog:
    """Discover safe `SKILL.md` files with deterministic ordering."""
    if isinstance(max_skill_bytes, bool) or max_skill_bytes < 1:
        raise ValueError("max_skill_bytes must be a positive integer")
    found: list[SkillRecord] = []
    diagnostics: list[Diagnostic] = []
    live_cache_keys = set()
    if metrics is not None:
        metrics.update(cache_hits=0, cache_misses=0)

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
                if dirname in _EXCLUDED_DIRS or dirname.startswith(".") or dirname.startswith("_"):
                    continue
                if child.is_symlink():
                    diagnostics.append(Diagnostic("unsafe_symlink", root_id, relative))
                    continue
                if _is_reparse_point(child):
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
            del cache[key]
    found = _apply_cross_root_precedence(found, roots, diagnostics)
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
    return Catalog(tuple(found), tuple(diagnostics), catalog_hash)


def extract_explicit_skill_names(query: str, *, known_names=None) -> tuple[str, ...]:
    """Extract only unambiguous, explicit skill syntax from a user query."""
    text = unicodedata.normalize("NFKC", str(query or ""))
    known = None if known_names is None else {normalize_identifier(name) for name in known_names}
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


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = {
    "a", "an", "and", "for", "in", "of", "on", "please", "the", "this", "to", "use", "using", "with",
    "skill", "skills", "ช่วย", "ด้วย", "ทำ", "นี้", "สกิล", "ใช้", "ให้",
}


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return tuple(
        token for token in _TOKEN_RE.findall(normalized)
        if len(token) > 1 and token not in _STOPWORDS
    )


@lru_cache(maxsize=2048)
def _identifier_pattern(normalized_identifier: str) -> "re.Pattern[str]":
    # Hyphen is a boundary too: "frontend-design" must not match inside
    # "frontend-design-pro". The negation patterns already refuse it.
    return re.compile(rf"(?<![\w-]){re.escape(normalized_identifier)}(?![\w-])")


def _identifier_in_query(identifier: str, query: str) -> bool:
    return bool(_identifier_pattern(normalize_identifier(identifier)).search(normalize_identifier(query)))


_NEGATION_WORDS = (
    # Thai vetoes keep the loose window: spaceless text needs back-tracking
    # inside a non-space run ("ไม่ต้องใช้python-tdd").
    "ไม่ต้อง",
    "ไม่ใช้",
    "ไม่เอา",
    "ไม่เลือก",
    "อย่าใช้",
    "ยกเว้น",
    "ห้าม",
    "อย่า",
    "ไม่",
)
_ENGLISH_NEGATION_WORDS = (
    "do\\s+not",
    "don't",
    "doesn't",
    "never",
    "without",
    "except",
    "not",
    "no",
)

# Closed vocabulary for English veto clauses ("don't use the skill X").  Only
# these words may sit between a negation and the skill name, so a request like
# "do not forget to use X" is never misread as a veto.
_NEGATION_FILLERS = (
    "ever", "really", "actually", "just", "again", "simply", "even",
    "want", "wants", "wanted", "need", "needs", "needed", "to",
    "use", "uses", "using", "load", "loads", "loading", "select",
    "selecting", "choose", "choosing", "pick", "picking", "invoke",
    "call", "run", "running",
    "the", "a", "an", "any", "that", "this", "those", "these", "my", "its",
    "skill", "skills", "สกิล",
)


@lru_cache(maxsize=1024)
def _negation_patterns(normalized_name: str) -> "tuple[re.Pattern[str], re.Pattern[str]]":
    """Compiled veto patterns per skill name (compiling per call cost ~1 ms x N skills
    and overflowed the re module cache on large catalogs)."""
    tail = r"[`\"']?\$?" + re.escape(normalized_name) + r"(?![a-z0-9_-])"
    loose = re.compile(r"(?<!\w)(?:" + "|".join(_NEGATION_WORDS) + r")(?:\s*\S+){0,2}\s*" + tail)
    negations = "|".join(_NEGATION_WORDS + _ENGLISH_NEGATION_WORDS)
    structured = re.compile(
        r"(?<!\w)(?:" + negations + r")"
        r"(?:\s+(?:" + "|".join(_NEGATION_FILLERS) + r"))*"
        r"\s*" + tail
    )
    return loose, structured


def _skill_is_negated(name: str, query: str) -> bool:
    """True when the query vetoes *name* ("don't use X", "ไม่เอา X").

    Two shapes are accepted: a loose window for Thai vetoes (spaceless text
    needs back-tracking inside one non-space run) and a filler-vocabulary
    clause for English vetoes ("do not use the skill X") that still refuses
    non-veto phrasings such as "do not forget to use X".
    """
    normalized_name = normalize_identifier(name)
    if not normalized_name:
        return False
    normalized_query = normalize_identifier(query)
    loose, structured = _negation_patterns(normalized_name)
    return bool(loose.search(normalized_query)) or bool(structured.search(normalized_query))


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
    return any(start <= code <= end for start, end in _SPACELESS_RANGES)


def _contains_spaceless_script(value: str) -> bool:
    return any(_is_spaceless_character(character) for character in str(value or ""))


_NGRAM_SIZE = 2
_NGRAM_MINIMUM_DICE = 0.25
_NGRAM_WEIGHT = 0.55


@lru_cache(maxsize=4096)
def _char_ngrams(value: str, size: int = _NGRAM_SIZE) -> "frozenset[str]":
    """Character n-grams of the spaceless-script part of *value*.

    Spaceless scripts have no word boundaries to tokenize on, so token overlap
    cannot see that "ช่วยเขียน เอกสาร ภาษาไทย" and "สร้างและตรวจ เอกสาร
    ภาษาไทย" are about the same thing.  Character n-grams are a dictionary-free
    way to measure that overlap, and they never fire for spaced scripts.
    """
    text = "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if _is_spaceless_character(character)
    )
    if len(text) < size:
        return frozenset()
    return frozenset(text[index : index + size] for index in range(len(text) - size + 1))


def _ngram_dice(left: "frozenset[str]", right: "frozenset[str]") -> float:
    """Symmetric overlap of two n-gram sets (0.0 when either side is empty)."""
    if not left or not right:
        return 0.0
    shared = len(left & right)
    if not shared:
        return 0.0
    return (2.0 * shared) / (len(left) + len(right))


def _phrase_present(shorter: str, longer: str) -> bool:
    """True when *shorter* occurs in *longer* as a complete token span.

    Spaceless scripts (Thai, CJK, Hangul, ...) keep raw substring matching:
    there are no word boundaries to split on. Anything else must match on
    token boundaries so "ui" never fires inside "build".
    """
    if not shorter:
        return False
    if " " not in shorter and " " not in longer:
        if _contains_spaceless_script(shorter + longer):
            return shorter in longer
        return bool(re.search(rf"(?<!\w){re.escape(shorter)}(?!\w)", longer))
    return bool(re.search(rf"(?<!\w){re.escape(shorter)}(?!\w)", longer))


# Closed vocabularies for requests that point at the *ongoing* skill instead of
# describing a task.  Only these phrasings are accepted: an ordinary sentence
# that happens to contain "again" or "เดิม" must keep routing lexically.
_DIALOGUE_RELEASE_PHRASES = (
    "stop using",
    "stop focusing",
    "stop with",
    "unfocus",
    "no longer use",
    "forget the skill",
    "เลิกใช้",
    "หยุดใช้",
    "เลิกโฟกัส",
    "เลิกทำ",
)
_DIALOGUE_FOCUS_PHRASES = (
    "keep using",
    "keep working with",
    "keep going with",
    "stick with",
    "stay on",
    "from now on use",
    "continue with",
    "carry on with",
    "ใช้ต่อ",
    "ใช้ตัวนี้ต่อ",
    "ทำต่อด้วย",
    "ใช้ต่อไป",
)
_DIALOGUE_REPEAT_PHRASES = (
    "same skill",
    "the same one",
    "same one",
    "same as before",
    "like before",
    "that skill again",
    "that one again",
    "do the same",
    "same as last time",
    "ใช้ตัวเดิม",
    "ตัวเดิม",
    "อันเดิม",
    "เหมือนเดิม",
    "แบบเดิม",
    "อย่างเมื่อกี้",
    "เมื่อกี้",
)
_DIALOGUE_PREVIOUS_PHRASES = (
    "previous skill",
    "the one before",
    "skill before",
    "ครั้งก่อน",
    "ก่อนหน้า",
    "อันที่แล้ว",
    "ตัวที่แล้ว",
    "อันก่อน",
)
_DIALOGUE_FAMILIES = tuple(
    # Phrases are normalized the same way queries are: NFKC rewrites Thai SARA AM
    # (\u0e33) into NIKHANIT + SARA AA, so an unnormalized phrase silently never matches.
    (
        kind,
        tuple(unicodedata.normalize("NFKC", phrase).casefold() for phrase in phrases),
    )
    for kind, phrases in (
        ("release", _DIALOGUE_RELEASE_PHRASES),
        ("focus", _DIALOGUE_FOCUS_PHRASES),
        ("repeat", _DIALOGUE_REPEAT_PHRASES),
        ("previous", _DIALOGUE_PREVIOUS_PHRASES),
    )
)


def _named_skill_in_query(query: str, known_names: Sequence[str]) -> str:
    """Return the *known* skill the request names, or "".

    Only catalog names count: an unknown token after "skill" ("stop using the
    skill now") must not drive focus or release.
    """
    known = tuple(str(name) for name in known_names if name)
    known_ids = {normalize_identifier(name) for name in known}
    for candidate in extract_explicit_skill_names(query, known_names=known_ids):
        if normalize_identifier(candidate) in known_ids:
            return str(candidate)
    for name in known:
        if _identifier_in_query(name, query):
            return name
    return ""


# Lead-in words that may sit between "keep using" and the skill name.
_DIALOGUE_FILLER = frozenset({
    "the", "a", "an", "skill", "skills", "my", "this", "that", "current",
    "สกิล", "ตัว", "อัน", "ตัวนี้", "อันนี้", "ของ",
})
_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _skill_named_after(query: str, phrase: str) -> str:
    """First identifier-shaped token after *phrase* ("keep using X").

    The token may be a skill that is not in the catalog; reporting it as
    unknown is more truthful than silently falling back to session history.
    """
    at = query.casefold().find(phrase)
    if at < 0:
        return ""
    tail = query[at + len(phrase) :].lstrip(" \t,.;:!?—–-")
    for _ in range(4):
        match = _IDENTIFIER_TOKEN.match(tail)
        if not match:
            return ""
        token = match.group(0).rstrip(".,;:!?)]}")
        if token and token.casefold() not in _DIALOGUE_FILLER:
            return token
        tail = tail[match.end() :].lstrip(" \t,.;:!?—–-")
    return ""


@dataclass(frozen=True)
class DialogueReference:
    """A request that refers to the session's skill history instead of a task."""

    kind: str
    phrase: str = ""
    name: str = ""


def parse_dialogue_reference(
    query: str,
    *,
    known_names: Sequence[str] = (),
) -> Optional[DialogueReference]:
    """Detect "use the same skill", "keep using X", "stop using X", "อันที่แล้ว"..."""
    text = unicodedata.normalize("NFKC", str(query or "")).casefold().strip()
    if not text:
        return None
    for kind, phrases in _DIALOGUE_FAMILIES:
        for phrase in phrases:
            if _phrase_present(phrase, text):
                name = _named_skill_in_query(text, known_names)
                if not name and kind in ("focus", "release"):
                    name = _skill_named_after(text, phrase)
                return DialogueReference(kind, phrase, name)
    return None


def _release_remainder(query: str, reference: DialogueReference, released: str) -> "Optional[str]":
    """Text that follows the release clause, or ``None`` when nothing was cut.

    "stop using python-tdd and design a landing page" must still route to the
    second half: the release only cancels the focus, not the rest of the turn.
    An empty string means the clause was cut and nothing else was asked, so the
    full query (which still contains the released name) must not be ranked.
    """
    text = str(query or "")
    if not text.strip():
        return ""
    # Work on a normalized copy: NFKC can change length (Thai SARA AM), so
    # positions are only safe when both the haystack and the anchor are folded.
    folded = unicodedata.normalize("NFKC", text).casefold()
    anchor = unicodedata.normalize("NFKC", str(released or "")).casefold().strip()
    index = folded.find(anchor) if anchor else -1
    if index >= 0:
        rest = folded[index + len(anchor) :]
    else:
        phrase = reference.phrase or ""
        at = folded.find(phrase)
        if at < 0:
            return None
        rest = folded[at + len(phrase) :]
    return rest.lstrip(" \t,.;:!?—–-")


@dataclass(frozen=True)
class RankedCandidate:
    skill: SkillRecord
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RankRow:
    """One row of the full lexical ranking, kept for ``/skill-proof why``."""

    name: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    status: str
    reason: str
    selected: Optional[RankedCandidate]
    candidates: tuple[RankedCandidate, ...]
    explicit: bool
    required: bool
    # Every skill that scored above zero this turn (bounded), so an "why not X"
    # question can be answered exactly without ever storing the prompt.
    rank_table: tuple[RankRow, ...] = ()


_RANK_TABLE_LIMIT = 20


def _rank_table(ranked: Sequence[RankedCandidate]) -> tuple[RankRow, ...]:
    return tuple(
        RankRow(item.skill.name, item.score, item.reasons)
        for item in ranked[:_RANK_TABLE_LIMIT]
    )


def _rank(
    catalog: Catalog,
    query: str,
    synonyms: Optional[Mapping[str, Sequence[str]]] = None,
    *,
    exclude: Sequence[str] = (),
    preferred: Optional[str] = None,
    preferred_bonus: float = 0.0,
) -> list[RankedCandidate]:
    synonyms = synonyms or {}
    excluded = {normalize_identifier(name) for name in exclude if normalize_identifier(name)}
    preferred_normalized = normalize_identifier(preferred) if preferred else ""
    bonus = max(0.0, float(preferred_bonus))
    query_tokens = set(_tokens(query))
    query_ngrams = _char_ngrams(query)
    normalized_query = normalize_identifier(query)
    name_counts = Counter(skill.normalized_name for skill in catalog.skills)
    duplicate_names = {name for name, count in name_counts.items() if count > 1}
    document_tokens = [
        set(_tokens(" ".join((skill.name, skill.description, " ".join(skill.tags)))))
        for skill in catalog.skills
    ]
    document_frequency: dict[str, int] = {}
    for tokens in document_tokens:
        for token in tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    total_documents = max(1, len(catalog.skills))

    ranked: list[RankedCandidate] = []
    for skill in catalog.skills:
        if skill.normalized_name in duplicate_names or skill.normalized_name in excluded:
            continue
        if _skill_is_negated(skill.name, query):
            continue
        name_tokens = set(_tokens(skill.name.replace("-", " ").replace("_", " ")))
        tag_tokens = set(_tokens(" ".join(skill.tags)))
        description_tokens = set(_tokens(skill.description))
        alias_terms = tuple(dict.fromkeys(tuple(skill.aliases) + tuple(synonyms.get(skill.normalized_name, ()))))
        reasons: list[str] = []
        score = 0.0

        # Only computed when the query itself contains a spaceless script, so
        # spaced-language turns pay nothing for this path.
        if query_ngrams:
            field_ngrams = _char_ngrams(
                " ".join((skill.description, " ".join(skill.tags), " ".join(alias_terms)))
            )
            dice = _ngram_dice(query_ngrams, field_ngrams)
            if dice >= _NGRAM_MINIMUM_DICE:
                score += _NGRAM_WEIGHT * dice
                reasons.append("spaceless_script_overlap")

        if _identifier_in_query(skill.name, query):
            score += 0.92
            reasons.append("exact_name_in_query")

        normalized_description = normalize_identifier(skill.description)
        if _phrase_present(normalized_description, normalized_query) or _phrase_present(
            normalized_query, normalized_description
        ):
            score += 0.76
            reasons.append("description_phrase")

        for alias in alias_terms:
            normalized_alias = normalize_identifier(alias)
            if normalized_alias and (
                _phrase_present(normalized_alias, normalized_query)
                or _phrase_present(normalized_query, normalized_alias)
            ):
                score += 0.74
                reasons.append("alias_phrase")
                break

        if query_tokens:
            query_weight = sum(
                log((total_documents + 1) / (document_frequency.get(token, 0) + 1)) + 1
                for token in query_tokens
            )
            if query_weight:
                def coverage(field_tokens: set[str]) -> float:
                    return sum(
                        log((total_documents + 1) / (document_frequency.get(token, 0) + 1)) + 1
                        for token in query_tokens & field_tokens
                    ) / query_weight

                name_coverage = coverage(name_tokens)
                tag_coverage = coverage(tag_tokens)
                description_coverage = coverage(description_tokens)
                alias_coverage = coverage(set(_tokens(" ".join(alias_terms)))) if alias_terms else 0.0
                if name_coverage:
                    score += 0.58 * name_coverage
                    reasons.append("name_terms")
                if tag_coverage:
                    score += 0.22 * tag_coverage
                    reasons.append("tag_terms")
                if alias_coverage:
                    score += 0.22 * alias_coverage
                    reasons.append("alias_terms")
                if description_coverage:
                    score += 0.34 * description_coverage
                    reasons.append("description_terms")

        # A focused skill only wins when it is already a plausible candidate:
        # focus nudges the ranking, it never hijacks an unrelated turn.
        if bonus and score > 0 and skill.normalized_name == preferred_normalized:
            score += bonus
            reasons.append("dialogue_focus")

        if score > 0:
            ranked.append(RankedCandidate(skill, round(min(1.0, score), 6), tuple(reasons)))
    ranked.sort(key=lambda item: (-item.score, item.skill.skill_id, item.skill.relative_path))
    return ranked


def select_skill(
    catalog: Catalog,
    query: str,
    *,
    explicit_names: Sequence[str] = (),
    min_score: float = 0.28,
    min_margin: float = 0.05,
    limit: int = 3,
    synonyms: Optional[Mapping[str, Sequence[str]]] = None,
    exclude: Sequence[str] = (),
    preferred: Optional[str] = None,
    preferred_bonus: float = 0.0,
) -> Selection:
    """Choose one skill conservatively or return a reason for not choosing."""
    if not 0 <= float(min_score) <= 1:
        raise ValueError("min_score must be between 0 and 1")
    if not 0 <= float(min_margin) <= 1:
        raise ValueError("min_margin must be between 0 and 1")
    if isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    unique_explicit: list[str] = []
    seen_explicit: set[str] = set()
    for name in explicit_names:
        normalized = normalize_identifier(name)
        if normalized and normalized not in seen_explicit:
            seen_explicit.add(normalized)
            unique_explicit.append(str(name))
    # A vetoed explicit name ("don't use $X") is not a request: drop it and
    # fall through to lexical ranking instead of selecting it.
    requested = [name for name in unique_explicit if not _skill_is_negated(name, query)]
    if len(requested) > 1:
        return Selection("blocked", "multiple_explicit_skills", None, (), True, True)
    if requested:
        matches = catalog.by_name(requested[0])
        if not matches:
            return Selection("blocked", "unknown_explicit_skill", None, (), True, True)
        if len(matches) > 1:
            return Selection("blocked", "ambiguous_explicit_skill", None, (), True, True)
        candidate = RankedCandidate(matches[0], 1.0, ("explicit_name",))
        return Selection("selected", "explicit_skill", candidate, (candidate,), True, True)
    if unique_explicit and not requested:
        # Every explicit name was vetoed: lexical ranking (which also skips
        # vetoed skills) decides; an explicit-only veto must not select.
        ranked_vetoed = _rank(
            catalog, query, synonyms, exclude=exclude, preferred=preferred, preferred_bonus=preferred_bonus
        )
        return Selection(
            "no_match",
            "negated_skill",
            None,
            tuple(ranked_vetoed[:limit]),
            False,
            False,
            _rank_table(ranked_vetoed),
        )

    ranked = _rank(
        catalog, query, synonyms, exclude=exclude, preferred=preferred, preferred_bonus=preferred_bonus
    )
    table = _rank_table(ranked)
    if not ranked or ranked[0].score < float(min_score):
        if any(_skill_is_negated(skill.name, query) for skill in catalog.skills):
            return Selection("no_match", "negated_skill", None, tuple(ranked[:limit]), False, False, table)
        return Selection("no_match", "below_threshold", None, tuple(ranked[:limit]), False, False, table)
    if len(ranked) > 1 and ranked[0].score - ranked[1].score < float(min_margin):
        return Selection("ambiguous", "insufficient_margin", None, tuple(ranked[:limit]), False, False, table)
    return Selection("selected", "lexical_match", ranked[0], tuple(ranked[:limit]), False, False, table)


@dataclass(frozen=True)
class TurnStart:
    selection: Selection
    context: str
    errors: tuple[str, ...]


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str
    message: str = ""


@dataclass
class _TurnState:
    turn_id: str
    session_id: str
    task_id: str
    mode: str
    query_sha256: str
    catalog: Catalog
    selection: Selection
    context: str
    errors: list[str] = field(default_factory=list)
    hermes_loaded_event: bool = False
    loaded_provenance: Optional[str] = None
    loaded_use_count: Optional[int] = None
    loaded_reused: Optional[bool] = None
    loaded_reuse_after_patch: Optional[bool] = None
    source_sha256: Optional[str] = None
    source_bytes: Optional[int] = None
    source_error: Optional[str] = None
    tool_result_sha256_pre_transform: Optional[str] = None
    tool_result_bytes_pre_transform: Optional[int] = None
    active: bool = False
    active_evidence: Optional[str] = None
    observed_tools: list[str] = field(default_factory=list)
    complete: bool = False
    availability: str = "local_only"
    local_skill_count: int = 0
    host_name_count: Optional[int] = None
    host_unindexed: tuple[str, ...] = ()
    hub: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)
    vetoed: tuple[str, ...] = ()
    # Normalized names of the *unfiltered* local catalog, kept so "why" can
    # tell "not indexed" apart from "excluded by the host listing".
    indexed_names: frozenset = frozenset()
    performance: dict = field(default_factory=dict)
    source_path_match: str = "unknown"
    compliance: str = "unassessed"
    compliance_reasons: list[str] = field(default_factory=list)


@dataclass
class _SessionMemory:
    """What this session was just using, for dialogue references and focus."""

    stack: list[str] = field(default_factory=list)
    focus: Optional[str] = None
    focus_remaining: int = 0


class SkillProofEngine:
    """Thread-safe turn state and evidence ledger, independent of Hermes internals."""

    _MODES = frozenset({"observe", "nudge", "enforce-tools"})
    _MODE_ALIASES = {"enforce": "enforce-tools"}
    _SESSION_LIMIT = 64
    _STACK_LIMIT = 10
    # Focus is a bounded, decaying boost that only applies to a skill that
    # already has lexical signal: it decides close calls, never unrelated turns.
    _FOCUS_BONUS = 0.20
    _FOCUS_BONUS_STEP = 0.04
    _FOCUS_BONUS_FLOOR = 0.04
    _SKILL_TOOLS = frozenset({"skill_view", "skills_list", "skill_search", "skill_manage"})

    def __init__(
        self,
        roots: Mapping[str, os.PathLike[str] | str],
        *,
        mode: str = "nudge",
        min_score: float = 0.28,
        min_margin: float = 0.05,
        max_candidates: int = 3,
        max_skill_bytes: int = 256 * 1024,
        context_budget_bytes: int = 1200,
        observed_tool_limit: int = 16,
        retained_turn_limit: int = 100,
        invariants: Optional[Mapping[str, Mapping[str, Any]]] = None,
        hub_lock_path: Optional[str | os.PathLike[str]] = None,
        synonyms: Optional[Mapping[str, Any]] = None,
        session_memory: bool = True,
        focus_turns: int = 5,
    ) -> None:
        normalized_mode = normalize_identifier(mode)
        normalized_mode = self._MODE_ALIASES.get(normalized_mode, normalized_mode)
        if normalized_mode not in self._MODES:
            raise ValueError("mode must be observe, nudge, or enforce-tools")
        if not 0 <= float(min_score) <= 1:
            raise ValueError("min_score must be between 0 and 1")
        if not 0 <= float(min_margin) <= 1:
            raise ValueError("min_margin must be between 0 and 1")
        for value, label in (
            (max_candidates, "max_candidates"),
            (max_skill_bytes, "max_skill_bytes"),
            (context_budget_bytes, "context_budget_bytes"),
            (observed_tool_limit, "observed_tool_limit"),
            (retained_turn_limit, "retained_turn_limit"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")

        self._roots = {str(key): pathlib.Path(value) for key, value in roots.items()}
        self.mode = normalized_mode
        self.min_score = float(min_score)
        self.min_margin = float(min_margin)
        self.max_candidates = max_candidates
        self.max_skill_bytes = max_skill_bytes
        self.context_budget_bytes = context_budget_bytes
        self.observed_tool_limit = observed_tool_limit
        self.retained_turn_limit = retained_turn_limit
        self.invariants: dict[str, SkillInvariants] = {}
        if isinstance(invariants, Mapping):
            for sname, srules in invariants.items():
                if isinstance(srules, Mapping):
                    sname_norm = normalize_identifier(sname)
                    req = tuple(normalize_identifier(x) for x in srules.get("required_tools", ()) if normalize_identifier(x))
                    forb = tuple(normalize_identifier(x) for x in srules.get("forbidden_tools", ()) if normalize_identifier(x))
                    order = tuple(normalize_identifier(x) for x in srules.get("ordered_tools", ()) if normalize_identifier(x))
                    self.invariants[sname_norm] = SkillInvariants(
                        required_tools=req,
                        forbidden_tools=forb,
                        ordered_tools=order,
                    )
        self.hub_lock_path: Optional[pathlib.Path] = (
            pathlib.Path(hub_lock_path).expanduser() if hub_lock_path else None
        )
        self._hub_cache: Optional[dict[str, Mapping[str, Any]]] = None
        self._hub_cache_mtime: Optional[int] = None
        self.synonyms: dict[str, tuple[str, ...]] = {}
        if isinstance(synonyms, Mapping):
            for sname, terms in synonyms.items():
                if isinstance(terms, str):
                    terms = (terms,)
                if not isinstance(terms, (list, tuple)):
                    continue
                normalized_terms = tuple(
                    dict.fromkeys(str(term).strip() for term in terms if str(term).strip())
                )[:32]
                if normalized_terms:
                    self.synonyms[normalize_identifier(sname)] = normalized_terms
        if not isinstance(session_memory, bool):
            raise ValueError("session_memory must be a boolean")
        if isinstance(focus_turns, bool) or not isinstance(focus_turns, int) or not 0 <= focus_turns <= 50:
            raise ValueError("focus_turns must be an integer between 0 and 50")
        self.session_memory = session_memory
        self.focus_turns = focus_turns
        self._sessions: dict[str, _SessionMemory] = {}
        self._catalog_cache: dict = {}
        self._turns: dict[str, _TurnState] = {}
        self._latest_by_task: dict[tuple[str, str], str] = {}
        self._latest_by_session: dict[str, str] = {}
        self._latest_turn_id: Optional[str] = None
        self._lock = threading.RLock()

    @staticmethod
    def _text_id(value: object) -> str:
        return str(value or "").strip()

    def _state(
        self,
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Optional[_TurnState]:
        if turn_id:
            state = self._turns.get(self._text_id(turn_id))
            if state is not None:
                if session_id and state.session_id != self._text_id(session_id):
                    return None
                if task_id and state.task_id != self._text_id(task_id):
                    return None
                return state
            return None
        session = self._text_id(session_id)
        task = self._text_id(task_id)
        if session or task:
            resolved_turn = self._latest_by_task.get((session, task))
            if resolved_turn:
                return self._turns.get(resolved_turn)
            if task:
                return None
        if session:
            resolved_turn = self._latest_by_session.get(session)
            if resolved_turn:
                return self._turns.get(resolved_turn)
        return None

    def _focus_fallback(
        self,
        selection: Selection,
        catalog: Catalog,
        focus: Optional[str],
        query: str,
    ) -> Selection:
        """Use the focused skill only when nothing else matched.

        "keep using X" is an explicit instruction, so an unrelated turn that has
        no competitor falls back to X instead of silently dropping it. A veto,
        an ambiguity, or a competing match always wins: focus never decides a
        close call, and it never survives its own release.
        """
        if not focus or selection.status != "no_match" or selection.reason != "below_threshold":
            return selection
        matches = catalog.by_name(focus)
        if len(matches) != 1 or _skill_is_negated(matches[0].name, query):
            return selection
        candidate = RankedCandidate(matches[0], 0.0, ("dialogue_focus_fallback",))
        return Selection(
            "selected",
            "focus_fallback",
            candidate,
            (candidate,),
            False,
            False,
            selection.rank_table,
        )

    def explain(
        self,
        skill_name: str = "",
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Answer "why (not) this skill?" from stored derived numbers.

        The prompt is never read or returned: the report is built from the rank
        table, the veto list, and the thresholds recorded for this turn.
        """
        wanted = normalize_identifier(skill_name)
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return {"available": False, "reason": "no_turn_observed"}
            selection = state.selection
            report: dict[str, Any] = {
                "available": True,
                "turn_id": state.turn_id,
                "decision": {
                    "status": selection.status,
                    "reason": selection.reason,
                    "explicit": selection.explicit,
                    "required": selection.required,
                },
                "thresholds": {
                    "min_score": self.min_score,
                    "min_margin": self.min_margin,
                    "max_candidates": self.max_candidates,
                },
                "selected": (
                    None
                    if selection.selected is None
                    else {
                        "name": selection.selected.skill.name,
                        "score": selection.selected.score,
                        "reasons": list(selection.selected.reasons),
                    }
                ),
                "candidates": [
                    {"name": item.skill.name, "score": item.score, "reasons": list(item.reasons)}
                    for item in selection.candidates
                ],
                "ranked_count": len(selection.rank_table),
                "rank_table": [
                    {"name": row.name, "score": row.score, "reasons": list(row.reasons)}
                    for row in selection.rank_table
                ],
                "vetoed": list(state.vetoed),
                "session": dict(state.session),
                "catalog_size": len(state.catalog.skills),
                "query_sha256": state.query_sha256,
            }
            if wanted:
                report["skill"] = self._explain_skill(state, wanted)
            return report

    def _explain_skill(self, state: _TurnState, wanted: str) -> dict[str, Any]:
        selection = state.selection
        base: dict[str, Any] = {
            "in_catalog": bool(state.catalog.by_name(wanted)),
            "threshold": self.min_score,
        }
        if wanted in state.indexed_names:
            base["indexed"] = True
        elif state.indexed_names:
            base["indexed"] = False
        if selection.selected is not None and selection.selected.skill.normalized_name == wanted:
            base.update(
                verdict="selected",
                name=selection.selected.skill.name,
                score=selection.selected.score,
                reasons=list(selection.selected.reasons),
                threshold_met=True,
            )
            return base
        if wanted in {normalize_identifier(name) for name in state.vetoed}:
            base.update(
                verdict="vetoed",
                name=None,
                score=None,
                reasons=["negated_in_query"],
                threshold_met=None,
                note=(
                    "The turn vetoed this skill, so it never entered the ranking. "
                    "Skill Proof does not store prompts, so the wording is not available here."
                ),
            )
            return base
        # Only a turn that skipped lexical ranking has no score to give; a turn
        # that ranked but scored nothing falls through to no_signal.
        if not selection.rank_table and (
            selection.explicit or selection.reason == "hermes_catalog_unavailable"
        ):
            base.update(
                verdict="not_ranked",
                name=None,
                score=None,
                reasons=[selection.reason],
                threshold_met=None,
                note=(
                    "This turn was decided without lexical ranking "
                    f"({selection.reason}), so no score exists for this skill."
                ),
            )
            return base
        for index, row in enumerate(selection.rank_table):
            if normalize_identifier(row.name) == wanted:
                base.update(
                    verdict="below_threshold" if row.score < self.min_score else "ranked",
                    name=row.name,
                    score=row.score,
                    rank=index + 1,
                    reasons=list(row.reasons),
                    threshold_met=row.score >= self.min_score,
                    gap_to_top=round(max(0.0, selection.rank_table[0].score - row.score), 6),
                )
                return base
        if not base["in_catalog"] and base.get("indexed"):
            base.update(
                verdict="not_listed_by_host",
                name=None,
                score=None,
                reasons=[],
                threshold_met=False,
                note=(
                    "A SKILL.md for this name exists under the configured roots, but the host's "
                    "skill listing for this turn did not include it, so it was excluded from "
                    "ranking. Not indexed is a different failure."
                ),
            )
            return base
        if not base["in_catalog"]:
            base.update(
                verdict="unknown_skill",
                name=None,
                score=None,
                reasons=[],
                threshold_met=False,
                note="No indexed SKILL.md under the configured roots has this name.",
            )
            return base
        last = selection.rank_table[-1].score
        capped = len(selection.rank_table) >= _RANK_TABLE_LIMIT
        base.update(
            verdict="ranked_below_cap" if capped else "no_signal",
            name=None,
            score=round(last, 6) if capped else 0.0,
            score_is_ceiling=capped,
            reasons=[],
            threshold_met=False,
            note=(
                f"Scored at or below {last} this turn."
                if capped
                else "No term in this turn overlapped this skill's name, description, tags, "
                "aliases, or spaceless-script characters."
            ),
        )
        return base

    @staticmethod
    def _session_evidence(memory: _SessionMemory, dialogue: Mapping[str, Any]) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        if memory.focus:
            evidence["focus"] = memory.focus
            evidence["focus_remaining"] = memory.focus_remaining
        if memory.stack:
            evidence["history"] = list(memory.stack[-5:])
        if dialogue:
            evidence["reference"] = dict(dialogue)
        return evidence

    def _session(self, session_id: str) -> Optional[_SessionMemory]:
        """Session-scoped memory, bounded to the most recent sessions."""
        if not self.session_memory or not session_id:
            return None
        memory = self._sessions.get(session_id)
        if memory is None:
            memory = _SessionMemory()
            self._sessions[session_id] = memory
            while len(self._sessions) > self._SESSION_LIMIT:
                self._sessions.pop(next(iter(self._sessions)))
        return memory

    def _touch_stack(self, memory: _SessionMemory, name: str) -> None:
        normalized = normalize_identifier(name)
        if not normalized:
            return
        if memory.stack and memory.stack[-1] == normalized:
            return
        memory.stack.append(normalized)
        del memory.stack[: max(0, len(memory.stack) - self._STACK_LIMIT)]

    def _focus_bonus(self, memory: _SessionMemory) -> float:
        """Focus decays each turn so it nudges the ranking without becoming sticky forever."""
        if not memory.focus:
            return 0.0
        if self.focus_turns <= 0:
            return self._FOCUS_BONUS
        used = max(0, self.focus_turns - memory.focus_remaining)
        bonus = self._FOCUS_BONUS - self._FOCUS_BONUS_STEP * used
        return round(max(self._FOCUS_BONUS_FLOOR, bonus), 4)

    def _dialogue_selection(
        self,
        memory: _SessionMemory,
        catalog: Catalog,
        reference: DialogueReference,
    ) -> Optional[Selection]:
        """Resolve a reference to session history; ``None`` means "fall through"."""
        if reference.kind == "focus":
            if reference.name:
                matches = catalog.by_name(reference.name)
                if not matches:
                    return Selection(
                        "no_match", "dialogue_reference_unknown", None, (), False, False
                    )
            elif memory.stack:
                matches = catalog.by_name(memory.stack[-1])
                if not matches:
                    return Selection(
                        "no_match", "dialogue_reference_unavailable", None, (), False, False
                    )
            else:
                return Selection("no_match", "no_previous_selection", None, (), False, False)
            memory.focus = matches[0].normalized_name
            memory.focus_remaining = self.focus_turns
            candidate = RankedCandidate(matches[0], 1.0, ("dialogue_focus_request",))
            return Selection("selected", "focus_requested", candidate, (candidate,), False, False)
        if reference.kind in ("repeat", "previous"):
            depth = 1 if reference.kind == "repeat" else 2
            if len(memory.stack) < depth:
                return Selection("no_match", "no_previous_selection", None, (), False, False)
            matches = catalog.by_name(memory.stack[-depth])
            if len(matches) != 1:
                return Selection("no_match", "dialogue_reference_unavailable", None, (), False, False)
            reason = "dialogue_reference" if depth == 1 else "dialogue_reference_previous"
            candidate = RankedCandidate(matches[0], 1.0, (reason,))
            return Selection("selected", reason, candidate, (candidate,), False, False)
        return None

    def forget_session(self, session_id: str) -> bool:
        """Drop session memory (skill history and focus) without touching audit turns."""
        clean = self._text_id(session_id)
        if not clean:
            return False
        with self._lock:
            return self._sessions.pop(clean, None) is not None

    def session_state(self, session_id: str) -> dict[str, Any]:
        """Read-only view of what this session is holding on to."""
        with self._lock:
            memory = self._sessions.get(self._text_id(session_id))
            if memory is None:
                return {"enabled": self.session_memory, "history": [], "focus": "", "focus_remaining": 0}
            return {
                "enabled": self.session_memory,
                "history": list(memory.stack),
                "focus": memory.focus or "",
                "focus_remaining": memory.focus_remaining if memory.focus else 0,
            }

    def _selection_context(self, selection: Selection) -> str:
        if selection.status == "selected" and selection.selected is not None:
            name = selection.selected.skill.name
            if selection.reason in ("dialogue_reference", "dialogue_reference_previous"):
                return (
                    f'Skill Proof selected "{name}" because the request refers to a skill already '
                    f'used in this session. Call skill_view with name="{name}". The selection did not '
                    "come from the current wording, so do not present it as a fresh match."
                )
            if selection.reason == "focus_requested":
                return (
                    f'Skill Proof selected "{name}" and kept it as the session focus for the next '
                    f'turns. Call skill_view with name="{name}" before operational tools.'
                )
            if selection.reason == "focus_fallback":
                return (
                    f'Skill Proof selected "{name}" only because the session focus is active and '
                    f'nothing else matched; its score is 0. If {name} does not fit this task, '
                    f'answer directly without it. Otherwise call skill_view with name="{name}".'
                )
            if self.mode == "observe":
                return (
                    f'Skill Proof candidate: "{name}". If you use it, call skill_view with '
                    f'name="{name}". A load event proves loading only, not compliance or verification.'
                )
            return (
                f'Skill Proof selected "{name}". Before operational tools, call skill_view with '
                f'name="{name}". A Hermes loaded event proves loading only; it does not prove '
                "skill compliance or task verification."
            )
        if selection.status == "ambiguous" and selection.candidates:
            names = ", ".join(item.skill.name for item in selection.candidates)
            return (
                f"Skill Proof: multiple skills matched ({names}). Ask which skill to load; "
                "do not claim one was selected."
            )
        if selection.status == "blocked" and selection.explicit:
            if selection.reason == "listed_but_unindexed":
                return (
                    "Skill Proof: Hermes lists the requested skill, but no local SKILL.md under "
                    "the configured roots could be indexed for it. Do not claim that it was loaded."
                )
            return (
                f"Skill Proof could not resolve the explicitly requested skill "
                f"({selection.reason}). Do not claim that it was loaded."
            )
        if selection.status == "no_match" and selection.reason == "no_previous_selection":
            return (
                "Skill Proof: the request refers to a previously used skill, but this session has no "
                "skill history yet. Ask which skill to load; do not claim one was selected."
            )
        if selection.status == "no_match" and selection.reason == "dialogue_reference_unavailable":
            return (
                "Skill Proof: the previously used skill in this session is no longer available in the "
                "current catalog. Do not claim that it was loaded."
            )
        if selection.status == "no_match" and selection.reason == "dialogue_reference_unknown":
            return (
                "Skill Proof: the request names a skill that is not in the current catalog. Ask the "
                "user which skill they mean; do not claim one was selected."
            )
        if selection.status == "no_match" and selection.reason == "focus_released":
            return (
                "Skill Proof: session focus cleared; no skill was selected for this instruction."
            )
        return ""

    def begin_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        task_id: str,
        query: str,
        available_names: Optional[set[str]] = None,
        availability_error: bool = False,
    ) -> TurnStart:
        core_started = time.perf_counter()
        clean_turn_id = self._text_id(turn_id)
        if not clean_turn_id:
            raise ValueError("turn_id must not be empty")
        clean_session_id = self._text_id(session_id)
        clean_task_id = self._text_id(task_id)
        query_text = str(query or "")
        metrics = {}
        with self._lock:
            catalog = scan_catalog(self._roots, max_skill_bytes=self.max_skill_bytes, cache=self._catalog_cache, metrics=metrics)
        metrics['scan_ms'] = round((time.perf_counter() - core_started) * 1000, 3)
        local_count = len(catalog.skills)
        indexed_names = frozenset(skill.normalized_name for skill in catalog.skills)
        host_unindexed: tuple[str, ...] = ()
        selection_started = time.perf_counter()
        if available_names is not None:
            names = {normalize_identifier(name) for name in available_names}
            local_indexed = {skill.normalized_name for skill in catalog.skills}
            host_unindexed = tuple(
                sorted(str(name) for name in available_names if normalize_identifier(name) not in local_indexed)
            )
            eligible = tuple(skill for skill in catalog.skills if skill.normalized_name in names)
            filtered_hash = hashlib.sha256(json.dumps(
                [(s.skill_id, s.source_sha256) for s in eligible], separators=(",", ":")
            ).encode()).hexdigest()
            exclusions = tuple(Diagnostic('not_in_hermes_list', skill.root_id, skill.relative_path)
                               for skill in catalog.skills if skill.normalized_name not in names)
            catalog = Catalog(eligible, catalog.diagnostics + exclusions, filtered_hash)
        known_names = tuple(skill.name for skill in catalog.skills)
        explicit_names = extract_explicit_skill_names(query_text, known_names=known_names)
        memory = self._session(clean_session_id)
        dialogue: dict[str, Any] = {}
        selection: Optional[Selection] = None
        if memory is not None and not explicit_names:
            if memory.focus and self.focus_turns > 0 and memory.focus_remaining <= 0:
                memory.focus = None
            reference = parse_dialogue_reference(query_text, known_names=known_names)
            if reference is not None:
                dialogue = {"kind": reference.kind, "phrase": reference.phrase}
                if reference.name:
                    dialogue["name"] = reference.name
                selection = self._dialogue_selection(memory, catalog, reference)
                if selection is None:
                    # A release has no skill of its own: clear the focus and let
                    # lexical ranking decide what (if anything) comes next, but
                    # never re-select the skill the user just released.
                    named = normalize_identifier(dialogue.get("name") or "")
                    # A name that is not in the catalog must not redirect the
                    # release: the current focus is what gets dropped.
                    released = (
                        named if named and len(catalog.by_name(named)) == 1
                        else normalize_identifier(memory.focus or "")
                    )
                    if released and normalize_identifier(memory.focus or "") == released:
                        memory.focus = None
                    memory.focus_remaining = 0
                    # Rank what the user asked for *after* the release clause,
                    # not the whole sentence that contains it.
                    remainder = _release_remainder(
                        query_text, reference, named or released
                    )
                    selection = select_skill(
                        catalog,
                        query_text if remainder is None else remainder,
                        explicit_names=explicit_names,
                        min_score=self.min_score,
                        min_margin=self.min_margin,
                        limit=self.max_candidates,
                        synonyms=self.synonyms,
                        exclude=(released,) if released else (),
                    )
                    if selection.status == "no_match":
                        dialogue["released"] = released or named
                        selection = Selection(
                            "no_match",
                            "focus_released",
                            None,
                            selection.candidates,
                            False,
                            False,
                            selection.rank_table,
                        )
        if selection is None:
            focus = memory.focus if memory is not None else None
            if focus and len(catalog.by_name(focus)) != 1:
                memory.focus = None
                focus = None
            selection = select_skill(
                catalog,
                query_text,
                explicit_names=explicit_names,
                min_score=self.min_score,
                min_margin=self.min_margin,
                limit=self.max_candidates,
                synonyms=self.synonyms,
                preferred=focus,
                preferred_bonus=self._focus_bonus(memory) if (memory and focus) else 0.0,
            )
            selection = self._focus_fallback(selection, catalog, focus, query_text)
        session_evidence: dict[str, Any] = {}
        if memory is not None:
            if selection.selected is not None:
                self._touch_stack(memory, selection.selected.skill.normalized_name)
            session_evidence = self._session_evidence(memory, dialogue)
        if availability_error:
            selection = Selection("blocked", "hermes_catalog_unavailable", None, (), bool(explicit_names), True)
        elif (
            selection.status == "blocked"
            and selection.reason == "unknown_explicit_skill"
            and host_unindexed
            and any(normalize_identifier(name) in names for name in explicit_names)
        ):
            # Hermes lists the requested skill, but no local SKILL.md under the
            # configured roots could be indexed for it: say that instead of
            # claiming the skill is unknown.
            selection = Selection("blocked", "listed_but_unindexed", None, (), True, True)
        metrics['selection_ms'] = round((time.perf_counter() - selection_started) * 1000, 3)
        metrics['core_ms'] = round((time.perf_counter() - core_started) * 1000, 3)
        context = self._selection_context(selection)
        errors: list[str] = ["hermes_catalog_unavailable"] if availability_error else []
        if availability_error:
            context = "Skill Proof cannot read Hermes skills_list. Skill availability is unknown; report this limitation."
        if len(context.encode("utf-8")) > self.context_budget_bytes:
            context = ""
            errors.append("context_budget_exceeded")
        state = _TurnState(
            turn_id=clean_turn_id,
            session_id=clean_session_id,
            task_id=clean_task_id,
            mode=self.mode,
            query_sha256=hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
            catalog=catalog,
            selection=selection,
            context=context,
            errors=errors,
            availability="unavailable" if availability_error else "hermes_list" if available_names is not None else "local_only",
            local_skill_count=local_count,
            host_unindexed=host_unindexed,
            host_name_count=None if available_names is None or availability_error else len(available_names),
            session=session_evidence,
            indexed_names=indexed_names,
            # Recorded so "why not X" can say vetoed instead of guessing; the
            # same scan runs inside _rank for every turn (about 0.5 ms).
            vetoed=tuple(
                skill.name for skill in catalog.skills if _skill_is_negated(skill.name, query_text)
            ),
            performance=metrics,
        )
        with self._lock:
            self._turns[clean_turn_id] = state
            self._latest_by_task[(clean_session_id, clean_task_id)] = clean_turn_id
            if clean_session_id:
                self._latest_by_session[clean_session_id] = clean_turn_id
            self._latest_turn_id = clean_turn_id
        return TurnStart(selection, context, tuple(errors))

    @staticmethod
    def _has_symlink_component(root: pathlib.Path, relative_path: str) -> bool:
        current = root
        if current.is_symlink():
            return True
        for part in pathlib.PurePosixPath(relative_path).parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _prove_selected_source(self, selected: SkillRecord) -> tuple[Optional[str], Optional[int], Optional[str]]:
        root = selected.root_path
        source = root.joinpath(*pathlib.PurePosixPath(selected.relative_path).parts)
        try:
            if self._has_symlink_component(root, selected.relative_path):
                return None, None, "unsafe_symlink"
            resolved_root = root.resolve(strict=True)
            resolved_source = source.resolve(strict=True)
            if not resolved_root.is_dir() or not resolved_source.is_file():
                return None, None, "source_unavailable"
            if not _is_within(resolved_source, resolved_root):
                return None, None, "unsafe_path"
            with resolved_source.open("rb") as handle:
                raw = handle.read(self.max_skill_bytes + 1)
        except (OSError, RuntimeError):
            return None, None, "source_unavailable"
        if len(raw) > self.max_skill_bytes:
            return None, None, "skill_too_large"
        try:
            reparsed = _parse_skill(
                raw,
                root_id=selected.root_id,
                relative_path=selected.relative_path,
                root_path=resolved_root,
                source_path=source,
            )
        except _FrontmatterError:
            return None, None, "skill_changed"
        digest = hashlib.sha256(raw).hexdigest()
        if (
            reparsed.normalized_name != selected.normalized_name
            or digest != selected.source_sha256
            or len(raw) != selected.source_bytes
        ):
            return None, None, "skill_changed"
        return digest, len(raw), None

    def observe_skill_loaded(
        self,
        *,
        session_id: str,
        task_id: str,
        skill_name: str,
        provenance: Optional[str] = None,
        use_count: Optional[int] = None,
        reused: Optional[bool] = None,
        reuse_after_patch: Optional[bool] = None,
    ) -> bool:
        with self._lock:
            state = self._state(session_id=session_id, task_id=task_id)
            if state is None or state.selection.selected is None:
                return False
            selected = state.selection.selected.skill
            if normalize_identifier(skill_name) != selected.normalized_name:
                return False
            state.hermes_loaded_event = True
            state.loaded_provenance = None if provenance is None else str(provenance)
            state.loaded_use_count = use_count if isinstance(use_count, int) and not isinstance(use_count, bool) else None
            state.loaded_reused = reused if isinstance(reused, bool) else None
            state.loaded_reuse_after_patch = reuse_after_patch if isinstance(reuse_after_patch, bool) else None
            digest, size, error = self._prove_selected_source(selected)
            state.source_sha256 = digest
            state.source_bytes = size
            state.source_error = error
            if error and error not in state.errors:
                state.errors.append(error)
            state.hub = self._hub_provenance(selected)
            return True

    def _remember_tool(self, state: _TurnState, tool_name: str) -> None:
        clean_name = str(tool_name or "").strip()
        if not clean_name:
            return
        state.observed_tools.append(clean_name)
        if len(state.observed_tools) > self.observed_tool_limit:
            del state.observed_tools[: len(state.observed_tools) - self.observed_tool_limit]

    def _resolve_invariants(self, skill: SkillRecord) -> SkillInvariants:
        # ponytail: tuple concatenation with deduplication; add schema checks if users define custom predicates
        cfg = self.invariants.get(skill.normalized_name, SkillInvariants())
        front = skill.invariants
        req = tuple(dict.fromkeys(front.required_tools + cfg.required_tools))
        forb = tuple(dict.fromkeys(front.forbidden_tools + cfg.forbidden_tools))
        order = tuple(dict.fromkeys(front.ordered_tools + cfg.ordered_tools))
        return SkillInvariants(required_tools=req, forbidden_tools=forb, ordered_tools=order)

    def guard_tool(
        self,
        *,
        turn_id: Optional[str],
        session_id: str,
        task_id: str,
        tool_name: str,
    ) -> GuardResult:
        normalized_tool = normalize_identifier(tool_name)
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return GuardResult(True, "no_turn_state")
            self._remember_tool(state, tool_name)
            if normalized_tool in self._SKILL_TOOLS:
                return GuardResult(True, "skill_tool")
            selection = state.selection
            if self.mode == "enforce-tools" and selection.status == "blocked" and selection.required:
                return GuardResult(
                    False,
                    selection.reason,
                    f"Skill Proof blocked this tool: {selection.reason}.",
                )
            if selection.selected is None:
                return GuardResult(True, "no_required_skill")
            if self.mode == "enforce-tools" and not state.hermes_loaded_event:
                name = selection.selected.skill.name
                return GuardResult(
                    False,
                    "selected_skill_not_loaded",
                    f'Call skill_view for "{name}" before operational tools.',
                )
            if state.hermes_loaded_event:
                state.active = True
                state.active_evidence = "post_load_tool_call"

            inv = self._resolve_invariants(selection.selected.skill)
            if not inv.is_empty:
                if normalized_tool in inv.forbidden_tools:
                    state.compliance = "failed"
                    reason = f"forbidden_tool:{normalized_tool}"
                    if reason not in state.compliance_reasons:
                        state.compliance_reasons.append(reason)
                    if self.mode == "enforce-tools":
                        return GuardResult(
                            False,
                            "forbidden_tool",
                            f'Skill Proof blocked this tool: skill "{selection.selected.skill.name}" forbids tool "{tool_name}".',
                        )
                if inv.ordered_tools and normalized_tool in inv.ordered_tools:
                    idx = inv.ordered_tools.index(normalized_tool)
                    prior = inv.ordered_tools[:idx]
                    normalized_prior = [normalize_identifier(t) for t in state.observed_tools[:-1]]
                    missing = [p for p in prior if p not in normalized_prior]
                    if missing:
                        state.compliance = "failed"
                        reason = f"order_violation:{normalized_tool}_before_{missing[0]}"
                        if reason not in state.compliance_reasons:
                            state.compliance_reasons.append(reason)
                        if self.mode == "enforce-tools":
                            return GuardResult(
                                False,
                                "tool_order_violated",
                                f'Skill Proof blocked this tool: skill "{selection.selected.skill.name}" requires "{missing[0]}" before "{tool_name}".',
                            )

            if state.hermes_loaded_event:
                return GuardResult(True, "selected_skill_loaded")
            return GuardResult(True, "mode_not_enforcing")

    def observe_tool_result(
        self,
        *,
        turn_id: Optional[str],
        session_id: str,
        task_id: str,
        tool_name: str,
        args: Optional[Mapping[str, Any]],
        result: Any,
        status: Optional[str] = None,
    ) -> bool:
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return False
            self._remember_tool(state, tool_name)
            if normalize_identifier(tool_name) != "skill_view" or state.selection.selected is None:
                return False
            supplied_name = ""
            if isinstance(args, Mapping):
                supplied_name = str(args.get("name") or args.get("skill_name") or "")
            if normalize_identifier(supplied_name) != state.selection.selected.skill.normalized_name:
                return False
            if args and args.get('file_path'):
                return False  # Supporting-file output is not the main skill's proof.
            if status is not None and normalize_identifier(status) not in {"success", "ok", "completed"}:
                return False
            if isinstance(result, bytes):
                raw_result = result
            elif isinstance(result, str):
                raw_result = result.encode("utf-8")
            else:
                raw_result = json.dumps(
                    result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")
            state.tool_result_sha256_pre_transform = hashlib.sha256(raw_result).hexdigest()
            state.tool_result_bytes_pre_transform = len(raw_result)
            try:
                parsed_result = json.loads(raw_result)
                reported_path = parsed_result.get('_source_path') if isinstance(parsed_result, dict) and parsed_result.get('success') is True else None
                if isinstance(reported_path, str) and pathlib.Path(reported_path).is_absolute():
                    actual = os.path.normcase(str(pathlib.Path(reported_path).resolve()))
                    expected = os.path.normcase(str(state.selection.selected.skill.source_path.resolve()))
                    state.source_path_match = 'match' if actual == expected else 'mismatch'
                    if actual != expected and 'source_path_mismatch' not in state.errors:
                        state.errors.append('source_path_mismatch')
            except (ValueError, OSError):
                pass
            return True

    @staticmethod
    def _candidate_payload(candidate: RankedCandidate) -> dict[str, Any]:
        skill = candidate.skill
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "root_id": skill.root_id,
            "relative_path": skill.relative_path,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
        }

    def _evaluate_compliance(self, state: _TurnState) -> None:
        if state.selection.selected is None:
            state.compliance = "unassessed"
            return
        skill = state.selection.selected.skill
        inv = self._resolve_invariants(skill)
        if inv.is_empty:
            state.compliance = "unassessed"
            return
        if state.compliance == "failed":
            return
        if not state.hermes_loaded_event:
            state.compliance = "failed"
            if "skill_not_loaded" not in state.compliance_reasons:
                state.compliance_reasons.append("skill_not_loaded")
            return
        observed = {normalize_identifier(t) for t in state.observed_tools}
        missing = [t for t in inv.required_tools if t not in observed]
        if missing:
            state.compliance = "failed"
            for m in missing:
                reason = f"missing_required_tool:{m}"
                if reason not in state.compliance_reasons:
                    state.compliance_reasons.append(reason)
            return
        state.compliance = "verified"

    def _receipt_payload(self, state: _TurnState) -> dict[str, Any]:
        self._evaluate_compliance(state)
        diagnostics = Counter(item.code for item in state.catalog.diagnostics)
        selected = state.selection.selected
        return {
            "schema_version": "skill-proof.receipt.v1",
            "turn_id": state.turn_id,
            "mode": state.mode,
            "query_sha256": state.query_sha256,
            "catalog": {
                "skill_count": len(state.catalog.skills),
                "catalog_hash": state.catalog.catalog_hash,
                "diagnostic_counts": dict(sorted(diagnostics.items())),
                "availability": state.availability,
                "local_skill_count": state.local_skill_count,
                "host_name_count": state.host_name_count,
                "host_unindexed_count": len(state.host_unindexed),
                "host_unindexed_sample": list(state.host_unindexed[:10]),
                "host_unindexed_omitted": max(0, len(state.host_unindexed) - 10),
                "diagnostics": [{'code': d.code, 'root_id': d.root_id, 'relative_path': d.relative_path[:240]} for d in state.catalog.diagnostics[:20]],
                "diagnostics_omitted": max(0, len(state.catalog.diagnostics) - 20),
            },
            "decision": {
                "status": state.selection.status,
                "reason": state.selection.reason,
                "explicit": state.selection.explicit,
                "required": state.selection.required,
            },
            "selected": None if selected is None else self._candidate_payload(selected),
            "candidates": [self._candidate_payload(item) for item in state.selection.candidates],
            "evidence": {
                "hermes_loaded_event": state.hermes_loaded_event,
                "provenance": state.loaded_provenance,
                "use_count": state.loaded_use_count,
                "reused": state.loaded_reused,
                "reuse_after_patch": state.loaded_reuse_after_patch,
                "source_sha256": state.source_sha256,
                "source_bytes": state.source_bytes,
                "source_error": state.source_error,
                "source_path_match": state.source_path_match,
                "hub": dict(state.hub),
                "session": dict(state.session),
                "tool_result_sha256_pre_transform": state.tool_result_sha256_pre_transform,
                "tool_result_bytes_pre_transform": state.tool_result_bytes_pre_transform,
            },
            "runtime": {
                "active": state.active,
                "active_evidence": state.active_evidence,
                "observed_tools": list(state.observed_tools),
            },
            "compliance": state.compliance,
            "compliance_reasons": list(state.compliance_reasons),
            "verification": "unverified",
            "errors": list(state.errors),
            "complete": state.complete,
            "performance": dict(state.performance),
        }

    def record_timing(self, turn_id: str, *, listing_ms: float, total_ms: float) -> None:
        with self._lock:
            state = self._state(turn_id=turn_id)
            if state is not None:
                state.performance.update(listing_ms=round(listing_ms, 3), total_ms=round(total_ms, 3))

    def receipt(
        self,
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return {}
            return self._receipt_payload(state)

    def finish_turn(self, *, turn_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._state(turn_id=turn_id)
            if state is None:
                return {}
            state.complete = True
            # Focus lasts a bounded number of turns, counted including the turn
            # that requested it.
            memory = self._sessions.get(state.session_id)
            if memory is not None and memory.focus and memory.focus_remaining > 0:
                memory.focus_remaining -= 1
            payload = self._receipt_payload(state)
            completed = [key for key, value in self._turns.items() if value.complete]
            for key in completed[:-self.retained_turn_limit]:
                del self._turns[key]
            self._latest_by_task = {k: v for k, v in self._latest_by_task.items() if v in self._turns}
            self._latest_by_session = {k: v for k, v in self._latest_by_session.items() if v in self._turns}
            return payload

    def refresh_catalog(self) -> None:
        """Force a content reread next turn; existing turn evidence stays unchanged."""
        with self._lock:
            self._catalog_cache.clear()

    def _hub_entries(self) -> Mapping[str, Mapping[str, Any]]:
        """Read-only hub lock entries keyed by normalized skill name (mtime-cached)."""
        if self.hub_lock_path is None:
            return {}
        try:
            info = self.hub_lock_path.stat()
        except OSError:
            return {}
        if info.st_size > _HUB_LOCK_MAX_BYTES:
            return {}
        if self._hub_cache is not None and self._hub_cache_mtime == info.st_mtime_ns:
            return self._hub_cache
        entries: dict[str, Mapping[str, Any]] = {}
        try:
            payload = json.loads(self.hub_lock_path.read_text(encoding="utf-8"))
            installed = payload.get("installed") if isinstance(payload, Mapping) else None
            if isinstance(installed, Mapping):
                for name, entry in installed.items():
                    if isinstance(entry, Mapping):
                        entries[normalize_identifier(str(name))] = entry
        except (OSError, ValueError):
            entries = {}
        self._hub_cache = entries
        self._hub_cache_mtime = info.st_mtime_ns
        return entries

    def _hub_provenance(self, selected: SkillRecord) -> dict[str, Any]:
        """Cross-check a loaded skill against the hub lock; never trust it blindly."""
        entry = self._hub_entries().get(selected.normalized_name)
        if entry is None:
            return {}
        scan = entry.get("scan_provenance")
        scan = scan if isinstance(scan, Mapping) else {}
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        recorded = str(scan.get("bundle_hash") or entry.get("content_hash") or "")
        recorded_hex = recorded.split(":", 1)[-1] if recorded else ""
        files = entry.get("files") if isinstance(entry.get("files"), (list, tuple)) else ()
        local_hash = (
            _bundle_sha256(selected.source_path.parent, [str(name) for name in files]) if files else None
        )
        if local_hash is not None and recorded_hex:
            bundle = "match" if local_hash == recorded_hex else "modified"
        else:
            bundle = "unknown"
        return {
            "available": True,
            "trust_level": str(entry.get("trust_level") or ""),
            "scan_verdict": str(entry.get("scan_verdict") or ""),
            "source": str(entry.get("source") or ""),
            "source_revision": str(metadata.get("source_revision") or ""),
            "bundle": bundle,
        }

    def hub_status(self) -> dict[str, Any]:
        if self.hub_lock_path is None:
            return {"enabled": False}
        entries = self._hub_entries()
        return {"enabled": True, "path": str(self.hub_lock_path), "entries": len(entries)}

    def root_suggestions(self, limit: int = 3) -> list[dict[str, Any]]:
        with self._lock:
            return suggest_roots(self._roots, limit=limit)

    def summary(
        self,
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> str:
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return "Skill Proof: no turn evidence"
            self._evaluate_compliance(state)
            selected = state.selection.selected
            name = selected.skill.name if selected is not None else "none"
            loaded = "yes" if state.hermes_loaded_event else "no"
            active = "yes" if state.active else "no"
            return (
                f"Skill Proof: selected={name} loaded={loaded} active={active} "
                f"compliance={state.compliance} verification=unverified"
            )

    @property
    def latest_turn_id(self) -> Optional[str]:
        with self._lock:
            return self._latest_turn_id
