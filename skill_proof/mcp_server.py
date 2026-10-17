#!/usr/bin/env python3
"""Minimal stdio MCP server exposing Skill Proof to any MCP-capable agent host.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout.  Tools:

  skill_roots  — detected skill roots for common agent ecosystems
  skill_scan   — catalog summary, diagnostics, and canonical root suggestions
  skill_select — deterministic selection for a query (offline, stdlib only)

Register it in Claude Code, Codex, Antigravity, Cursor, or any MCP client with
a command like:  python /path/to/skill-proof/mcp_server.py
"""

from __future__ import annotations

from collections import Counter
import json
import pathlib
import sys
try:
    from . import core
except (ImportError, ValueError):
    import core

SERVER_INFO = {"name": "skill-proof", "version": "1.0.0"}
FALLBACK_PROTOCOL = "2024-11-05"
SERVER_INSTRUCTIONS = (
    "Skill Proof — Deterministic Skill Router, Cryptographic Provenance & Global Skill Vault.\n\n"
    "## Mandatory Agent Habit (When to Use Skill Proof)\n"
    "1. **Route Before Acting (`skill_select`):** Before starting a specialized engineering, research, design, or domain workflow, "
    "call `skill_select` with `query` set to the user's intent (pass `include_content: true` to inline the winning `SKILL.md` in one round-trip).\n"
    "2. **Read or Search When Needed (`skill_read` / `skill_search`):** Call `skill_read` with the selected skill `name` to load its full instructions, "
    "or `skill_search` to discover top-K matching skills across the Global Skill Vault (`~/.skill-proof/skills`)."
)

_ENGINE_CACHE: dict[tuple, core.SkillProofEngine] = {}
_TURN_SEQ = 0

_ROOTS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Skill root directories; defaults to detected agent roots",
}

TOOLS = [
    {
        "name": "skill_roots",
        "description": (
            "List detected skill roots on this machine "
            "(Codex, Claude Code, Gemini/Antigravity CLI, Cursor, OpenCode, Cline, Hermes)."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "skill_scan",
        "description": (
            "Scan skill roots and report catalog size, diagnostics, and canonical "
            "root suggestions. Read-only and offline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"roots": _ROOTS_SCHEMA},
            "additionalProperties": False,
        },
    },
    {
        "name": "skill_select",
        "description": (
            "Select one SKILL.md for a query and return the evidence: decision status "
            "and reason, selected skill, ranked candidates. Pass include_content=true to "
            "inline the winning SKILL.md body in a single call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "roots": _ROOTS_SCHEMA,
                "min_score": {"type": "number"},
                "min_margin": {"type": "number"},
                "limit": {"type": "integer"},
                "include_content": {
                    "type": "boolean",
                    "description": "Inline the full SKILL.md content of the selected skill in one round-trip (default: false)",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "skill_search",
        "description": (
            "Search skills using hybrid ranking (Anthropic Tool Search style). "
            "Returns top matching skill names, descriptions, and file paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "Max skills to return, default 5"},
                "roots": _ROOTS_SCHEMA,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "skill_read",
        "description": "Read the full markdown content of a SKILL.md by skill name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Canonical name of the skill"},
                "roots": _ROOTS_SCHEMA,
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


class MethodNotFound(Exception):
    pass


def _roots(parameters):
    given = parameters.get("roots") or []
    if given:
        return {
            f"root-{index}": pathlib.Path(str(value)).expanduser()
            for index, value in enumerate(given, start=1)
        }
    return core.detect_agent_roots()


def _get_engine(roots, *, min_score: float = 0.28, min_margin: float = 0.05, max_candidates: int = 3):
    key = (
        tuple((k, str(v)) for k, v in sorted(roots.items())),
        round(float(min_score), 4),
        round(float(min_margin), 4),
        int(max_candidates),
    )
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        engine = core.SkillProofEngine.from_config(
            roots,
            min_score=min_score,
            min_margin=min_margin,
            max_candidates=max_candidates,
        )
        if len(_ENGINE_CACHE) >= 16:
            _ENGINE_CACHE.clear()
        _ENGINE_CACHE[key] = engine
    return engine


def _next_turn_id(prefix: str = "mcp") -> str:
    global _TURN_SEQ
    _TURN_SEQ += 1
    return f"{prefix}-{_TURN_SEQ}"


def _tool_roots(parameters):
    return {"roots": {root_id: str(path) for root_id, path in core.detect_agent_roots().items()}}


def _vector_index():
    """Read the shared, explicit vector settings for this standalone process."""
    return core.vector_index_from_env()


def _tool_scan(parameters):
    roots = _roots(parameters)
    catalog = core.scan_catalog(roots)
    return {
        "roots": {root_id: str(path) for root_id, path in roots.items()},
        "skill_count": len(catalog.skills),
        "catalog_hash": catalog.catalog_hash,
        "diagnostic_counts": dict(sorted(Counter(item.code for item in catalog.diagnostics).items())),
        "root_suggestions": core.suggest_roots(roots) if roots else [],
    }


def _tool_select(parameters):
    query = parameters.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    roots = _roots(parameters)
    engine = _get_engine(
        roots,
        min_score=float(parameters.get("min_score", 0.28)),
        min_margin=float(parameters.get("min_margin", 0.05)),
        max_candidates=int(parameters.get("limit", 3)),
    )
    selection = engine.begin_turn(
        turn_id=_next_turn_id("mcp"), session_id="mcp", task_id="mcp", query=query
    ).selection
    catalog = engine.latest_catalog()
    if catalog is None:  # pragma: no cover - begin_turn always scans
        catalog = core.Catalog((), (), "")
    selected = selection.selected
    selected_payload = None
    if selected is not None:
        selected_payload = {
            "name": selected.skill.name,
            "score": selected.score,
            "reasons": list(selected.reasons),
            "root_id": selected.skill.root_id,
            "relative_path": selected.skill.relative_path,
        }
        if parameters.get("include_content"):
            selected_payload["source_path"] = str(selected.skill.source_path)
            try:
                selected_payload["content"] = selected.skill.source_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                selected_payload["content"] = ""
    return {
        "decision": {
            "status": selection.status,
            "reason": selection.reason,
            "explicit": selection.explicit,
        },
        "selected": selected_payload,
        "candidates": [
            {
                "name": item.skill.name,
                "score": item.score,
                "reasons": list(item.reasons),
            }
            for item in selection.candidates
        ],
        "skill_count": len(catalog.skills),
    }


def _tool_search(parameters):
    query = parameters.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    limit = int(parameters.get("limit", 5))
    roots = _roots(parameters)
    engine = _get_engine(roots, max_candidates=max(limit, 5))
    selection = engine.begin_turn(
        turn_id=_next_turn_id("mcp-search"), session_id="mcp", task_id="mcp-search", query=query
    ).selection
    catalog = engine.latest_catalog()
    if catalog is None:  # pragma: no cover - begin_turn always scans
        catalog = core.Catalog((), (), "")
    skills_found = []
    seen = set()
    if selection.selected and selection.selected.skill.name not in seen:
        s = selection.selected.skill
        seen.add(s.name)
        skills_found.append({
            "name": s.name,
            "description": s.description,
            "source_path": str(s.source_path),
            "score": selection.selected.score,
            "reasons": list(selection.selected.reasons),
        })
    for cand in selection.candidates:
        s = cand.skill
        if s.name not in seen:
            seen.add(s.name)
            skills_found.append({
                "name": s.name,
                "description": s.description,
                "source_path": str(s.source_path),
                "score": cand.score,
                "reasons": list(cand.reasons),
            })
        if len(skills_found) >= limit:
            break
    return {
        "query": query,
        "status": selection.status,
        "results": skills_found,
    }


def _tool_read(parameters):
    name = parameters.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    roots = _roots(parameters)
    catalog = core.scan_catalog(roots)
    matches = catalog.by_name(name.strip())
    if not matches:
        raise ValueError(f"Skill '{name}' not found in catalog")
    if len(matches) > 1:
        raise ValueError(f"Skill '{name}' is ambiguous in catalog")
    skill = matches[0]
    content = skill.source_path.read_text(encoding="utf-8")
    return {
        "name": skill.name,
        "source_path": str(skill.source_path),
        "content": content,
    }


_TOOLS = {
    "skill_roots": _tool_roots,
    "skill_scan": _tool_scan,
    "skill_select": _tool_select,
    "skill_search": _tool_search,
    "skill_read": _tool_read,
}


def handle(request):
    method = request.get("method")
    parameters = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "initialize":
        requested = parameters.get("protocolVersion")
        protocol = requested if isinstance(requested, str) and requested else FALLBACK_PROTOCOL
        return {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": SERVER_INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = str(parameters.get("name"))
        handler = _TOOLS.get(name)
        if handler is None:
            raise MethodNotFound(f"unknown tool: {name}")
        arguments = parameters.get("arguments") if isinstance(parameters.get("arguments"), dict) else {}
        try:
            result = handler(arguments)
        except ValueError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {
            "content": [
                {"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}
            ],
            "isError": False,
        }
    raise MethodNotFound(str(method))


def _write(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(request, dict):
            continue
        request_id = request.get("id")
        try:
            result = handle(request)
        except MethodNotFound as exc:
            if request_id is not None:
                _write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": str(exc)}})
            continue
        except Exception as exc:  # never crash the host loop
            if request_id is not None:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": f"internal error: {exc}"},
                    }
                )
            continue
        if request_id is not None:
            _write({"jsonrpc": "2.0", "id": request_id, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
