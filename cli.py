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
import os
import pathlib
import subprocess
import sys
import tempfile
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
    """Route one query through the shared engine seam (plan Part 1.2 item 2).

    The engine resolves the env-configured vector index, syncs it, and applies
    the same selection pipeline as the Hermes plugin, so CLI/MCP selections
    match the plugin on the same machine.
    """
    engine = core.SkillProofEngine.from_config(
        roots,
        min_score=min_score,
        min_margin=min_margin,
        max_candidates=limit,
    )
    turn = engine.begin_turn(turn_id="cli", session_id="cli", task_id="cli", query=query)
    catalog = engine.latest_catalog()
    if catalog is None:  # pragma: no cover - begin_turn always scans
        catalog = core.Catalog((), (), "")
    explicit = core.extract_explicit_skill_names(
        query, known_names=(skill.name for skill in catalog.skills)
    )
    return catalog, explicit, turn.selection


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


def _yaml_indent(line):
    return len(line) - len(line.lstrip(" "))


def _section_end(lines, start, indent):
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line.lstrip().startswith("#") and _yaml_indent(line) <= indent:
            return index
    return len(lines)


def _find_section(lines, parent_start, parent_end, parent_indent, name):
    expected_indent = parent_indent + 2
    for index in range(parent_start + 1, parent_end):
        line = lines[index]
        stripped = line.strip()
        key_prefix = f"{name}:"
        if (
            _yaml_indent(line) == expected_indent
            and stripped.startswith(key_prefix)
            and (len(stripped) == len(key_prefix) or stripped[len(key_prefix)].isspace())
        ):
            return index, _section_end(lines, index, expected_indent), expected_indent
    return None


def _ensure_section(lines, parent_start, parent_end, parent_indent, name):
    found = _find_section(lines, parent_start, parent_end, parent_indent, name)
    if found:
        sec_start, sec_end, sec_indent = found
        head = lines[sec_start].strip()[len(name) + 1:].strip()
        if head in ("", "{}"):
            lines[sec_start] = " " * sec_indent + f"{name}:"
        return found
    index = parent_end
    lines.insert(index, " " * (parent_indent + 2) + f"{name}:")
    return index, index + 1, parent_indent + 2


def _write_plugin_settings(config_path, settings):
    """Update only Skill Proof settings, preserving unrelated YAML text."""
    path = pathlib.Path(config_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    plugins = next(
        (index for index, line in enumerate(lines) if _yaml_indent(line) == 0 and line.strip() == "plugins:"),
        None,
    )
    if plugins is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("plugins:")
        plugins = len(lines) - 1
    plugins_end = _section_end(lines, plugins, 0)
    entries, entries_end, entries_indent = _ensure_section(lines, plugins, plugins_end, 0, "entries")
    plugin, plugin_end, plugin_indent = _ensure_section(
        lines, entries, entries_end, entries_indent, "skill-proof"
    )
    settings_start, settings_end, settings_indent = _ensure_section(
        lines, plugin, plugin_end, plugin_indent, "settings"
    )
    setting_indent = settings_indent + 2
    serialized = {key: json.dumps(value, ensure_ascii=False) for key, value in settings.items()}
    for key, value in serialized.items():
        found_key = _find_section(lines, settings_start, settings_end, settings_indent, key)
        replacement = " " * setting_indent + f"{key}: {value}"
        if found_key:
            k_start, k_end, _ = found_key
            lines[k_start:k_end] = [replacement]
            settings_end -= (k_end - k_start - 1)
        else:
            lines.insert(settings_end, replacement)
            settings_end += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=path.name + ".", suffix=".tmp", delete=False,
    ) as handle:
        temp_path = pathlib.Path(handle.name)
        handle.write(content)
    temp_path.replace(path)


def _hermes_runtime_python():
    configured = os.environ.get("HERMES_PYTHON", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            return None
        return pathlib.Path(local_app_data) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    hermes_home = pathlib.Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return hermes_home / "hermes-agent" / "venv" / "bin" / "python"


def _missing_local_runtime_dependencies():
    """Return missing optional imports in Hermes' Python, or None if uncheckable."""
    python_path = _hermes_runtime_python()
    if not python_path or not python_path.is_file():
        return None
    code = (
        "import importlib.util, json; "
        "names = ('onnxruntime', 'tokenizers', 'numpy'); "
        "print(json.dumps([name for name in names if importlib.util.find_spec(name) is None]))"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", code], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None
        missing = json.loads(result.stdout.strip())
        return missing if isinstance(missing, list) else None
    except (OSError, json.JSONDecodeError):
        return None


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
    models_parser = subparsers.add_parser("models", parents=[common], help="list available embedding models and installation status")
    setup_parser = subparsers.add_parser("setup", parents=[common], help="configure an embedding provider")
    setup_parser.add_argument("--provider", choices=("off", "local", "gateway"))
    setup_parser.add_argument("--model", help="registered model key or 'none'")
    setup_parser.add_argument("--gateway-url")
    setup_parser.add_argument("--gateway-key-env")
    setup_parser.add_argument("--prefetch", action="store_true", help="download and verify the local model now")
    prefetch_parser = subparsers.add_parser("prefetch", parents=[common], help="download and verify an embedding model")
    prefetch_parser.add_argument("--model", default="Xenova/multilingual-e5-small", help="model key or alias to prefetch")
    remove_parser = subparsers.add_parser("remove", parents=[common], help="remove an installed embedding model")
    remove_parser.add_argument("--model", default="Xenova/multilingual-e5-small", help="model key or alias to remove")
    status_parser = subparsers.add_parser("status", parents=[common], help="report Skill Proof system, catalog, and provider status")
    calibrate_parser = subparsers.add_parser(
        "calibrate", parents=[common], help="suggest threshold/synonym tuning from the audit log"
    )
    calibrate_parser.add_argument("--audit-path", default="", help="audit.jsonl path (default: hermes plugin-data)")
    calibrate_parser.add_argument("--min-score", type=float, default=0.28)
    calibrate_parser.add_argument("--min-margin", type=float, default=0.05)
    bridge_parser = subparsers.add_parser("bridge", parents=[common], help="connect or inspect host agent workspaces")
    bridge_parser.add_argument("bridge_action", nargs="?", default="status", choices=("status", "init", "install", "remove", "ai-setup"))
    bridge_parser.add_argument("--host", default="auto")
    bridge_parser.add_argument("--target", default=".")
    bridge_parser.add_argument("--dry-run", action="store_true")
    bridge_parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "setup":
        provider = args.provider or input("Embedding provider [off/local/gateway] (off): ").strip().lower() or "off"
        if provider not in {"off", "local", "gateway"}:
            print("error: provider must be off, local, or gateway", file=sys.stderr)
            return 2
        model = args.model or "Xenova/multilingual-e5-small"
        gateway_url = args.gateway_url or "http://localhost:20128/v1/embeddings"
        gateway_key_env = args.gateway_key_env or "HERMES_CUSTOM_LOCALHOST_20128_API_KEY"
        if provider == "local" and not args.model:
            model = input(f"Model key or 'none' [{model}]: ").strip() or model
        if model.casefold() == "none":
            model = "Xenova/multilingual-e5-small"
            if provider == "local":
                provider = "off"
        if provider == "gateway":
            gateway_url = args.gateway_url or input(f"Gateway URL [{gateway_url}]: ").strip() or gateway_url
            gateway_key_env = args.gateway_key_env or input(f"Environment variable holding the key [{gateway_key_env}]: ").strip() or gateway_key_env
        if provider == "local":
            import models
            if not models.get_model_spec(model):
                print(f"error: unknown model '{model}'", file=sys.stderr)
                return 2
            missing = _missing_local_runtime_dependencies()
            if missing:
                print(
                    "error: Hermes runtime is missing local inference dependencies: "
                    + ", ".join(missing),
                    file=sys.stderr,
                )
                print(
                    "Install them in the Hermes venv with requirements-local.txt, then retry setup.",
                    file=sys.stderr,
                )
                runtime_python = _hermes_runtime_python()
                requirements = pathlib.Path(__file__).resolve().parent / "requirements-local.txt"
                if runtime_python:
                    if os.name == "nt":
                        command = f'& "{runtime_python}" -m pip install -r "{requirements}"'
                    else:
                        command = f'"{runtime_python}" -m pip install -r "{requirements}"'
                    print(f"Run: {command}", file=sys.stderr)
                return 2
            if missing is None:
                print(
                    "warning: could not inspect the Hermes Python environment; "
                    "verify requirements-local.txt is installed there.",
                    file=sys.stderr,
                )
        prefetch = args.prefetch
        if provider == "local" and not prefetch:
            answer = input("Download and verify the local model now? [y/N]: ").strip().lower()
            prefetch = answer in {"y", "yes"}
        if provider == "local" and prefetch:
            if not models.download_model(model):
                print("error: model download or verification failed; provider remains off", file=sys.stderr)
                provider = "off"
        settings = {
            "embedding_provider": provider,
            "embedding_model": model,
            "gateway_url": gateway_url,
            "gateway_key_env": gateway_key_env,
        }
        config_path = core.default_hermes_home() / "config.yaml"
        _write_plugin_settings(config_path, settings)
        print(f"Updated {config_path} for skill-proof ({provider}).")
        if provider == "local" and not prefetch:
            print("Install model weights separately with: python cli.py prefetch --model " + model)
        return 0

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
    if args.command == "models":
        import models
        res = []
        for key, spec in models.MODEL_REGISTRY.items():
            status = models.check_model_status(key)
            res.append({
                "model": key,
                "name": spec["name"],
                "license": spec["license"],
                "credit": spec["credit"],
                "source_url": spec["source_url"],
                "dim": spec["dim"],
                "installed": status["installed"],
                "valid": status["valid"],
                "size_mb": round(sum(f["size"] for f in spec["files"].values()) / (1024 * 1024), 1),
            })
        if args.json:
            print(json.dumps({"models": res}, indent=2, ensure_ascii=False))
        else:
            print(f"{'Model':<40} {'Status':<12} {'Size':<10} {'License':<10} {'Dim'}")
            print("-" * 80)
            for m in res:
                st = "installed" if m["installed"] else "missing"
                print(f"{m['model']:<40} {st:<12} {m['size_mb']} MB   {m['license']:<10} {m['dim']}")
        return 0

    if args.command == "prefetch":
        import models
        model_key = args.model
        print(f"Prefetching embedding model '{model_key}'...")
        spec = models.get_model_spec(model_key)
        if not spec:
            print(f"error: unknown model '{model_key}'", file=sys.stderr)
            return 2

        def _cb(filename, curr, tot):
            if curr == tot or curr % (10 * 1024 * 1024) < 65536:
                print(f"  {filename}: {curr / (1024 * 1024):.1f}/{tot / (1024 * 1024):.1f} MB")

        ok = models.download_model(model_key, callback=_cb)
        if ok:
            print(f"Model '{model_key}' successfully downloaded and verified.")
            return 0
        else:
            print(f"error: failed to download/verify model '{model_key}'", file=sys.stderr)
            return 1

    if args.command == "remove":
        import models
        ok = models.remove_model(args.model)
        if ok:
            print(f"Removed model '{args.model}'.")
            return 0
        else:
            print(f"error: failed to remove model '{args.model}'", file=sys.stderr)
            return 1

    if args.command == "status":
        import models
        catalog = core.scan_catalog(roots)
        installed_models = []
        for key in models.MODEL_REGISTRY:
            st = models.check_model_status(key)
            if st["installed"]:
                installed_models.append(key)
        vdb_path = core.default_hermes_home() / "plugin-data" / "skill-proof" / "embeddings.db"
        vec_idx = core.vector_index_from_env(db_path=vdb_path)
        st_data = {
            "version": core.__version__,
            "catalog_skills": len(catalog.skills),
            "installed_models": installed_models,
            "embeddings_db_exists": vdb_path.exists(),
            "vector_index_status": vec_idx.provider_status() if vec_idx else {"provider": "off", "active": False},
        }
        if args.json:
            print(json.dumps(st_data, indent=2, ensure_ascii=False))
        else:
            print(f"Skill Proof Status v{st_data['version']}")
            print(f"  Catalog Skills:     {st_data['catalog_skills']}")
            print(f"  Installed Models:   {', '.join(installed_models) or 'none'}")
            print(f"  Embeddings DB:      {'found' if st_data['embeddings_db_exists'] else 'none'}")
            print(f"  Vector Provider:    {st_data['vector_index_status']['provider']}")
            print(f"  Vector Active:      {st_data['vector_index_status']['active']}")
        return 0

    if args.command == "calibrate":
        audit_path = (
            pathlib.Path(args.audit_path).expanduser()
            if args.audit_path
            else core.default_hermes_home() / "plugin-data" / "skill-proof" / "audit.jsonl"
        )
        records, corrupt = core.read_audit_records(audit_path)
        if not records:
            print(f"No audit rows to calibrate on yet ({audit_path}).", file=sys.stderr)
            return 1
        payload = core.calibrate_audit(
            records,
            min_score=float(args.min_score),
            min_margin=float(args.min_margin),
            path=str(audit_path),
            corrupt=corrupt,
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(core.format_calibrate(payload))
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
