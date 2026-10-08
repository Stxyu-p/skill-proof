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

import json
import pathlib
import sys
from collections import Counter

import core

SERVER_INFO = {"name": "skill-proof", "version": core.__version__}
FALLBACK_PROTOCOL = "2024-11-05"

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
            "Deterministically select one SKILL.md for a query and return the "
            "evidence: decision status and reason, selected skill, ranked candidates. "
            "No network and no model call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "roots": _ROOTS_SCHEMA,
                "min_score": {"type": "number"},
                "min_margin": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
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


def _tool_roots(parameters):
    return {"roots": {root_id: str(path) for root_id, path in core.detect_agent_roots().items()}}


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
    catalog = core.scan_catalog(roots)
    explicit = core.extract_explicit_skill_names(
        query, known_names=(skill.name for skill in catalog.skills)
    )
    selection = core.select_skill(
        catalog,
        query,
        explicit_names=explicit,
        min_score=float(parameters.get("min_score", 0.28)),
        min_margin=float(parameters.get("min_margin", 0.05)),
        limit=int(parameters.get("limit", 3)),
    )
    selected = selection.selected
    return {
        "decision": {
            "status": selection.status,
            "reason": selection.reason,
            "explicit": selection.explicit,
        },
        "selected": (
            None
            if selected is None
            else {
                "name": selected.skill.name,
                "score": selected.score,
                "reasons": list(selected.reasons),
                "root_id": selected.skill.root_id,
                "relative_path": selected.skill.relative_path,
            }
        ),
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


_TOOLS = {"skill_roots": _tool_roots, "skill_scan": _tool_scan, "skill_select": _tool_select}


def handle(request):
    method = request.get("method")
    parameters = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method == "initialize":
        requested = parameters.get("protocolVersion")
        protocol = requested if isinstance(requested, str) and requested else FALLBACK_PROTOCOL
        return {"protocolVersion": protocol, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
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
