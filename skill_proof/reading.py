"""Progressive, deterministic reads for Markdown skill documents."""
from __future__ import annotations

import pathlib
import re

from . import core

_HEADING = re.compile(r"^#{1,6}\s+\S")
_LIST_ITEM = re.compile(r"^(?:\d+[.)]|[-*+])\s+\S")


def markdown_outline(text: str, *, max_line_chars: int = 160) -> list[str]:
    """Return Markdown headings and top-level list items, excluding prose/code."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() in ("---", "..."):
                lines = lines[index + 1:]
                break

    outline: list[str] = []
    fence: str | None = None
    fallback = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if fence:
            if line.startswith(fence):
                fence = None
            continue
        if line.startswith("```") or line.startswith("~~~"):
            fence = line[:3]
            continue
        if not fallback:
            fallback = line
        if _HEADING.match(line) or _LIST_ITEM.match(line):
            if len(line) > max_line_chars:
                line = line[:max_line_chars - 3].rstrip() + "..."
            outline.append(line)
    return outline or ([fallback[:max_line_chars]] if fallback else [])


def read_skill(name: str, roots: dict[str, pathlib.Path], *, level: str = "full") -> dict:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if level not in ("full", "outline"):
        raise ValueError("level must be 'full' or 'outline'")

    matches = list(core.scan_catalog(roots).by_name(name.strip()))
    if not matches:
        raise ValueError(f"Skill '{name}' not found in catalog")
    if len(matches) > 1:
        raise ValueError(f"Skill '{name}' is ambiguous in catalog")

    skill = matches[0]
    content = skill.source_path.read_text(encoding="utf-8")
    result = {"name": skill.name, "source_path": str(skill.source_path), "content": content}
    if level == "outline":
        result["content"] = "\n".join(markdown_outline(content))
        result["level"] = "outline"
    return result
