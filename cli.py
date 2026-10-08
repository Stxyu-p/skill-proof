#!/usr/bin/env python3
"""Skill Proof command line: deterministic, offline skill selection for any host.

Usage:
  python cli.py roots  [--json]
  python cli.py scan   [--root DIR ...] [--json]
  python cli.py select --query "..." [--root DIR ...] [--json] [--min-score N] [--min-margin N]

Without --root, every existing skill directory from common agent ecosystems is
used (Codex, Claude Code, Gemini/Antigravity CLI, Cursor, OpenCode, Cline,
Hermes, ... see `roots`).  Plain text by default, JSON with --json.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

import core


def _collect_roots(paths):
    if paths:
        return {
            f"root-{index}": pathlib.Path(value).expanduser()
            for index, value in enumerate(paths, start=1)
        }
    return core.detect_agent_roots()


def _root_map(roots):
    return {root_id: str(path) for root_id, path in roots.items()}


def _scan_payload(roots):
    catalog = core.scan_catalog(roots)
    diagnostics = Counter(item.code for item in catalog.diagnostics)
    return {
        "version": core.__version__,
        "roots": _root_map(roots),
        "skill_count": len(catalog.skills),
        "catalog_hash": catalog.catalog_hash,
        "diagnostic_counts": dict(sorted(diagnostics.items())),
        "diagnostics": [
            {"code": item.code, "root_id": item.root_id, "relative_path": item.relative_path}
            for item in catalog.diagnostics[:20]
        ],
        "diagnostics_omitted": max(0, len(catalog.diagnostics) - 20),
        "root_suggestions": core.suggest_roots(roots) if roots else [],
    }


def _select_payload(roots, query, min_score, min_margin, limit):
    catalog = core.scan_catalog(roots)
    explicit = core.extract_explicit_skill_names(
        query, known_names=(skill.name for skill in catalog.skills)
    )
    selection = core.select_skill(
        catalog,
        query,
        explicit_names=explicit,
        min_score=min_score,
        min_margin=min_margin,
        limit=limit,
    )
    selected = selection.selected

    def _candidate(item):
        return {
            "name": item.skill.name,
            "score": item.score,
            "reasons": list(item.reasons),
            "root_id": item.skill.root_id,
            "relative_path": item.skill.relative_path,
        }

    return {
        "version": core.__version__,
        "roots": _root_map(roots),
        "skill_count": len(catalog.skills),
        "explicit_names": list(explicit),
        "decision": {
            "status": selection.status,
            "reason": selection.reason,
            "explicit": selection.explicit,
        },
        "selected": None if selected is None else _candidate(selected),
        "candidates": [_candidate(item) for item in selection.candidates],
    }


def _format_scan(payload):
    lines = [
        f"Skill Proof {payload['version']}: {payload['skill_count']} skills from "
        f"{len(payload['roots'])} root(s)"
    ]
    counts = ", ".join(f"{code}={count}" for code, count in payload["diagnostic_counts"].items())
    lines.append(f"diagnostics: {counts or 'none'}")
    for suggestion in payload["root_suggestions"]:
        lines.append(
            f"root suggestion: {suggestion['path']} ({suggestion['skills']} skills behind reparse points)"
        )
    return "\n".join(lines)


def _format_select(payload):
    decision = payload["decision"]
    lines = [f"decision: {decision['status']} ({decision['reason']})"]
    selected = payload["selected"]
    if selected is not None:
        lines.append(
            f"selected: {selected['name']} score={selected['score']} "
            f"reasons={','.join(selected['reasons'])}"
        )
    for candidate in payload["candidates"]:
        lines.append(
            f"candidate: {candidate['name']} score={candidate['score']} "
            f"reasons={','.join(candidate['reasons'])}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="skill-proof",
        description="Deterministic, offline SKILL.md selection and evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root", action="append", default=[], metavar="DIR",
        help="skill root directory (repeatable; defaults to detected agent roots)",
    )
    common.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers.add_parser("roots", parents=[common], help="list detected skill roots")
    subparsers.add_parser("scan", parents=[common], help="scan roots and report catalog state")
    select_parser = subparsers.add_parser("select", parents=[common], help="choose one skill for a query")
    select_parser.add_argument("--query", required=True, help="the user request to route")
    select_parser.add_argument("--min-score", type=float, default=0.28)
    select_parser.add_argument("--min-margin", type=float, default=0.05)
    select_parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    roots = _collect_roots(args.root)
    if args.command == "roots":
        payload = {"version": core.__version__, "roots": _root_map(roots)}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif payload["roots"]:
            print("\n".join(f"{root_id}\t{path}" for root_id, path in payload["roots"].items()))
        else:
            print("No skill roots detected.")
        return 0
    if args.command == "scan":
        payload = _scan_payload(roots)
        print(
            json.dumps(payload, indent=2, ensure_ascii=False)
            if args.json
            else _format_scan(payload)
        )
        return 0
    payload = _select_payload(roots, args.query, args.min_score, args.min_margin, args.limit)
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.json
        else _format_select(payload)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
