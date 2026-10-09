#!/usr/bin/env python3
"""Skill Proof command line: deterministic, offline skill selection for any host.

Usage:
  python cli.py roots  [--json]
  python cli.py scan   [--root DIR ...] [--json]
  python cli.py select --query "..." [--root DIR ...] [--json] [--min-score N] [--min-margin N]
  python cli.py overlap [--root DIR ...] [--json] [--min-similarity N] [--limit N]

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


def _collect_roots(paths, profile=None):
    if paths:
        return {
            f"root-{index}": pathlib.Path(value).expanduser()
            for index, value in enumerate(paths, start=1)
        }
    return core.detect_agent_roots(profile=profile)


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


def _candidate_dict(item):
    return {
        "name": item.skill.name,
        "score": item.score,
        "reasons": list(item.reasons),
        "root_id": item.skill.root_id,
        "relative_path": item.skill.relative_path,
        "companions": list(item.companions),
    }


def _resolve_selection(roots, query, min_score, min_margin, limit):
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
    return catalog, explicit, selection


def _select_payload(roots, query, min_score, min_margin, limit):
    catalog, explicit, selection = _resolve_selection(roots, query, min_score, min_margin, limit)
    selected = selection.selected

    return {
        "version": core.__version__,
        "roots": _root_map(roots),
        "skill_count": len(catalog.skills),
        "explicit_names": list(explicit),
        "decision": {
            "status": selection.status,
            "reason": selection.reason,
            "explicit": selection.explicit,
            "suggested_agent": selection.suggested_agent,
        },
        "selected": None if selected is None else _candidate_dict(selected),
        "candidates": [_candidate_dict(item) for item in selection.candidates],
    }


def _eval_payload(roots, query, min_score=0.28, min_margin=0.05, limit=5, profile=None):
    catalog, explicit, selection = _resolve_selection(roots, query, min_score, min_margin, limit)
    selected = selection.selected
    tokens = core._tokens(query)

    return {
        "version": core.__version__,
        "roots": _root_map(roots),
        "query": query,
        "query_breakdown": {
            "tokens": list(tokens),
            "ngrams_count": len(core._char_ngrams(query)),
            "explicit_detected": list(explicit),
        },
        "decision": {
            "status": selection.status,
            "reason": selection.reason,
            "selected_skill": selected.skill.name if selected else None,
            "score": selected.score if selected else None,
            "companions": list(selected.companions) if selected else [],
            "suggested_agent": selection.suggested_agent,
        },
        "candidates": [_candidate_dict(item) for item in selection.candidates],
    }


def _overlap_payload(roots, min_similarity, limit):
    catalog = core.scan_catalog(roots)
    report = core.overlap_report(catalog, min_similarity=min_similarity, limit=limit)
    return {"version": core.__version__, "roots": _root_map(roots), **report}


def _format_overlap(payload):
    lines = [
        f"Skill Proof {payload['version']}: {payload['skills']} skills, "
        f"{payload['pairs_checked']} pairs checked at similarity >= {payload['min_similarity']}",
        (
            f"exact copies already collapsed: {payload['exact_copies']}  "
            f"divergent copies shadowed: {payload['shadowed_copies']}  "
            f"near-duplicates: {payload['total_found']}"
        ),
    ]
    for row in payload["pairs"]:
        lines.append(
            f"{row['similarity']:.3f}  {row['a']} ({row['root_a']})  <->  "
            f"{row['b']} ({row['root_b']})"
        )
        lines.append(f"         shares: {', '.join(row['shared'])}")
        lines.append(f"         -> {row['recommendation']}")
    if payload["truncated"]:
        lines.append(
            f"... {payload['total_found'] - len(payload['pairs'])} more pair(s) hidden by --limit"
        )
    if not payload["pairs"]:
        lines.append("No near-duplicates above the threshold.")
    return "\n".join(lines)


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
    lines = [
        f"decision: {decision['status']} ({decision['reason']})",
        f"suggested fleet agent: {decision.get('suggested_agent', 'mika')}",
    ]
    selected = payload["selected"]
    if selected is not None:
        comp_str = f" companions={','.join(selected['companions'])}" if selected.get("companions") else ""
        lines.append(
            f"selected: {selected['name']} score={selected['score']} "
            f"reasons={','.join(selected['reasons'])}{comp_str}"
        )
    for candidate in payload["candidates"]:
        lines.append(
            f"candidate: {candidate['name']} score={candidate['score']} "
            f"reasons={','.join(candidate['reasons'])}"
        )
    return "\n".join(lines)


def _format_eval(payload):
    decision = payload["decision"]
    breakdown = payload["query_breakdown"]
    lines = [
        f"Skill Proof {payload['version']} Diagnostics for: {payload['query']!r}",
        f"tokens: {', '.join(breakdown['tokens']) or 'none'} (ngrams={breakdown['ngrams_count']}, explicit={breakdown['explicit_detected'] or 'none'})",
        f"decision: {decision['status']} ({decision['reason']})",
        f"suggested fleet agent: {decision['suggested_agent']}",
    ]
    if decision["selected_skill"]:
        lines.append(f"selected: {decision['selected_skill']} (score={decision['score']})")
        if decision["companions"]:
            lines.append(f"companions: {', '.join(decision['companions'])}")
    lines.append("candidates:")
    for idx, cand in enumerate(payload["candidates"], start=1):
        lines.append(
            f"  #{idx} {cand['name']} score={cand['score']} reasons={','.join(cand['reasons'])} root={cand['root_id']}"
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
    common.add_argument(
        "--profile", default=None, metavar="NAME",
        help="Hermes agent profile name to prioritize (e.g. altima, sora, nua, milim)",
    )
    common.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers.add_parser("roots", parents=[common], help="list detected skill roots")
    subparsers.add_parser("scan", parents=[common], help="scan roots and report catalog state")
    select_parser = subparsers.add_parser("select", parents=[common], help="choose one skill for a query")
    select_parser.add_argument("pos_query", nargs="?", default="", metavar="QUERY", help="the user request to route")
    select_parser.add_argument("--query", dest="flag_query", default="", help="the user request to route")
    select_parser.add_argument("--min-score", type=float, default=0.28)
    select_parser.add_argument("--min-margin", type=float, default=0.05)
    select_parser.add_argument("--limit", type=int, default=3)
    eval_parser = subparsers.add_parser("eval", parents=[common], help="interactive query diagnostics and ranking matrix")
    eval_parser.add_argument("pos_query", nargs="?", default="", metavar="QUERY", help="user query to diagnose")
    eval_parser.add_argument("--query", dest="flag_query", help="user query to diagnose")
    eval_parser.add_argument("--min-score", type=float, default=0.28)
    eval_parser.add_argument("--min-margin", type=float, default=0.05)
    eval_parser.add_argument("--limit", type=int, default=5)
    overlap_parser = subparsers.add_parser(
        "overlap", parents=[common], help="near-duplicate skills worth pruning"
    )
    overlap_parser.add_argument("--min-similarity", type=float, default=0.4)
    overlap_parser.add_argument("--limit", type=int, default=20)
    bridge_parser = subparsers.add_parser("bridge", parents=[common], help="connect or inspect host agent workspaces")
    bridge_parser.add_argument("bridge_action", nargs="?", default="status", choices=("status", "init", "install", "remove", "ai-setup"))
    bridge_parser.add_argument("--host", default="auto")
    bridge_parser.add_argument("--target", default=".")
    bridge_parser.add_argument("--dry-run", action="store_true")
    bridge_parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "bridge":
        import bridge
        target_path = pathlib.Path(args.target).expanduser()
        if args.bridge_action == "status":
            st = bridge.status(target_path)
            print(json.dumps(st, indent=2, ensure_ascii=False))
            return 0
        if args.bridge_action in ("init", "install"):
            for line in bridge.init_host(args.host, target_path, dry_run=args.dry_run, force=args.force):
                print(line)
            return 0
        if args.bridge_action == "remove":
            for line in bridge.remove_host(args.host, target_path):
                print(line)
            return 0
        if args.bridge_action == "ai-setup":
            cli_path = pathlib.Path(__file__).resolve()
            bridge_path = cli_path.parent / "bridge.py"
            print("=== Skill Proof AI Agent Setup Instructions ===")
            print(f"1. Check skill roots: python {cli_path} roots")
            print(f"2. Select skill for task: python {cli_path} select --query \"<user request>\" --json")
            print(f"3. Auto-install workspace bridge: python {bridge_path} install")
            return 0

    roots = _collect_roots(args.root, profile=args.profile)
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
    if args.command == "overlap":
        try:
            payload = _overlap_payload(roots, args.min_similarity, args.limit)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(
            json.dumps(payload, indent=2, ensure_ascii=False)
            if args.json
            else _format_overlap(payload)
        )
        return 0
    if args.command == "eval":
        target_query = args.flag_query or args.pos_query
        if not target_query:
            print("error: query is required for eval", file=sys.stderr)
            return 2
        payload = _eval_payload(
            roots, target_query, min_score=args.min_score, min_margin=args.min_margin, limit=args.limit, profile=args.profile
        )
        print(
            json.dumps(payload, indent=2, ensure_ascii=False)
            if args.json
            else _format_eval(payload)
        )
        return 0
    target_query = args.flag_query or args.pos_query
    if not target_query:
        print("error: query is required for select", file=sys.stderr)
        return 2
    payload = _select_payload(roots, target_query, args.min_score, args.min_margin, args.limit)
    print(
        json.dumps(payload, indent=2, ensure_ascii=False)
        if args.json
        else _format_select(payload)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
