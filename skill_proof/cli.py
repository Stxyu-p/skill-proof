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
try:
    from . import core
except (ImportError, ValueError):
    import core


def _sync_skill_proof_antigravity_schema() -> None:
    try:
        try:
            from . import mcp_server as _mcp_mod
        except (ImportError, ValueError):
            import mcp_server as _mcp_mod
        ag_mcp = pathlib.Path.home() / ".gemini" / "antigravity-cli" / "mcp"
        if not ag_mcp.parent.is_dir():
            return
        target = ag_mcp / "skill-proof"
        target.mkdir(parents=True, exist_ok=True)
        for tool in _mcp_mod.TOOLS:
            schema = {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            }
            (target / f"{tool['name']}.json").write_text(
                json.dumps(schema, ensure_ascii=False), encoding="utf-8"
            )
        if getattr(_mcp_mod, "SERVER_INSTRUCTIONS", None):
            (target / "instructions.md").write_text(
                _mcp_mod.SERVER_INSTRUCTIONS.strip() + "\n", encoding="utf-8"
            )
    except Exception:
        pass


def _sync_skill_proof_global_skill() -> None:
    skill_md = (
        "---\n"
        "name: skill-proof\n"
        "description: >-\n"
        "  Deterministic, offline SKILL.md router, cryptographic provenance engine, and Global Skill Vault\n"
        "  manager across all agent ecosystems.\n"
        "---\n\n"
        "# Skill Proof — Deterministic Skill Router & Global Vault\n\n"
        "Use `skill_select` (with optional `include_content: true`), `skill_search`, `skill_read`, `skill_scan`, "
        "and `skill_roots` to route and inspect skills across the Global Skill Vault (`~/.agents/skills`).\n"
        "\n"
        "**Surface note:** those five are the **MCP server** tools (Codex CLI, Claude Code, Antigravity / Gemini CLI). In "
        "**Hermes**, skill-proof runs as a native plugin with no MCP server — routing is automatic: a routing hint is "
        "injected before each turn, so read that hint and load the named skill with `skill_view` instead of calling "
        "`skill_select`.\n"
    )
    try:
        try:
            from . import vault as _vault_mod
        except (ImportError, ValueError):
            import vault as _vault_mod
        vdir = _vault_mod.init_vault()
        d = vdir / "skill-proof"
        d.mkdir(parents=True, exist_ok=True)
        target = d / "SKILL.md"
        if not target.exists():
            target.write_text(skill_md, encoding="utf-8")
        for root in (
            pathlib.Path.home() / ".agents" / "skills",
            pathlib.Path.home() / ".gemini" / "config" / "skills",
        ):
            if root.is_dir():
                _vault_mod.pin("skill-proof", root, vault_dir=vdir, force=True)
    except Exception:
        pass



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
    venv_env = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv_env:
        cand = (
            pathlib.Path(venv_env) / "Scripts" / "python.exe"
            if os.name == "nt"
            else pathlib.Path(venv_env) / "bin" / "python"
        )
        if cand.is_file():
            return cand
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            legacy = pathlib.Path(local_app_data) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
            if legacy.is_file():
                return legacy
    else:
        hermes_home = pathlib.Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        legacy = hermes_home / "hermes-agent" / "venv" / "bin" / "python"
        if legacy.is_file():
            return legacy
    if sys.executable and pathlib.Path(sys.executable).is_file():
        return pathlib.Path(sys.executable)
    return None


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
    read_parser = subparsers.add_parser("read", parents=[common], help="read a skill at a progressive level")
    read_parser.add_argument("name", help="canonical skill name")
    read_parser.add_argument("--level", choices=("outline", "full"), default="full")
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

    audit_parser = subparsers.add_parser("audit", parents=[common], help="inspect audit logs, usage statistics, and prune dead skills")
    audit_parser.add_argument("audit_action", nargs="?", default="stats", choices=("stats", "prune"))
    audit_parser.add_argument("--audit-path", default="", help="audit.jsonl path (default: hermes plugin-data)")
    audit_parser.add_argument("--skill", dest="skill_name", default=None, help="specific skill to prune")
    audit_parser.add_argument("--dry-run", action="store_true", help="dry run without modifying disk")
    bridge_parser = subparsers.add_parser("bridge", parents=[common], help="connect or inspect host agent workspaces")
    bridge_parser.add_argument("bridge_action", nargs="?", default="status", choices=("status", "init", "install", "remove", "ai-setup"))
    bridge_parser.add_argument("--host", default="auto")
    bridge_parser.add_argument("--target", default=".")
    bridge_parser.add_argument("--dry-run", action="store_true")
    bridge_parser.add_argument("--force", action="store_true")

    vault_parser = subparsers.add_parser("vault", parents=[common], help="global skill vault management (~/.agents/skills)")
    vault_parser.add_argument("vault_action", nargs="?", default="status", choices=("status", "migrate", "pin", "unpin", "sync", "link-all", "reconcile", "lock", "unlock", "patch"))
    vault_parser.add_argument("--patch-op", choices=("export", "apply", "rebase"), default="rebase", help="patch operation for vault patch")
    vault_parser.add_argument("--patch-content", default="", help="raw patch content to export")
    vault_parser.add_argument("--skill", dest="skill_name", default=None, help="skill name to pin or unpin")
    vault_parser.add_argument("--to", dest="target_root", default=None, help="target agent root directory for pin")
    vault_parser.add_argument("--from", dest="from_root", default=None, help="target agent root directory for unpin")
    vault_parser.add_argument("--dry-run", action="store_true", help="dry run without modifying disk")
    vault_parser.add_argument("--force", action="store_true", help="replace existing physical skill directory with vault junction")

    def _add_crystallize_args(p):
        p.add_argument("--name", help="skill slug name")
        p.add_argument("--desc", help="short capability description (under 60 chars)")
        p.add_argument("--cmd", help="verification command (must pass)")
        p.add_argument("--step", action="append", help="procedure step (repeatable)")
        p.add_argument("--from-traces", action="store_true", help="mine repeated, cleanly-compressed commands from telemetry traces")
        p.add_argument("--traces", action="append", help="trace file path (repeatable; default ~/.trimtok/usage.jsonl)")
        p.add_argument("--min-runs", type=int, default=3, help="minimum observed runs per command (default: 3)")
        p.add_argument("--limit", type=int, default=8, help="maximum skills to emit (default: 8)")
        p.add_argument("--dry-run", action="store_true", help="report what would be crystallized")
        p.add_argument("--category", default="devops", help="skill category (default: devops)")
        p.add_argument("--vault", default=None, help="vault directory (default: ~/.agents/skills)")
        p.add_argument("--hermes", help="Hermes profile skills directory")

    crystallize_parser = subparsers.add_parser("crystallize", parents=[common], help="crystallize execution trace into verified SKILL.md")
    _add_crystallize_args(crystallize_parser)

    mine_parser = subparsers.add_parser("mine", parents=[common], help="mine repeatable commands from telemetry into candidate skills (alias for crystallize)")
    _add_crystallize_args(mine_parser)

    from skill_proof import mcp_install

    install_parser = subparsers.add_parser("install", help="register stdio MCP server across agent clients")
    mcp_install.add_install_arguments(install_parser)

    subparsers.add_parser("clients", help="list detected agent clients and their MCP configs")

    compile_parser = subparsers.add_parser("compile", help="compile skills index and trinity protocol into AGENTS.md, CLAUDE.md, and GEMINI.md")
    compile_parser.add_argument("--workspace", default=None, help="target workspace directory (default: current directory)")
    compile_parser.add_argument("--dry-run", action="store_true", help="dry run without modifying files")

    handoff_parser = subparsers.add_parser("handoff", help="export workspace state & tickets to switch to another agent model")
    handoff_parser.add_argument("--target", default="antigravity", choices=("antigravity", "claude-code", "codex"), help="target AI coding model/CLI")
    handoff_parser.add_argument("--workspace", default=None, help="target workspace directory (default: current directory)")
    handoff_parser.add_argument("--output", default=None, help="custom path to save handoff prompt file")
    handoff_parser.add_argument("--launch", action="store_true", help="launch target agent CLI with handoff payload")

    uninstall_parser = subparsers.add_parser("uninstall", help="remove MCP registration from agent clients")
    mcp_install.add_install_arguments(uninstall_parser)

    args = parser.parse_args(argv)

    # Apply configured vector provider from Hermes config.yaml if env var is not explicitly set
    if not os.environ.get("SKILL_PROOF_EMBEDDING_PROVIDER"):
        try:
            cfg_file = core.default_hermes_home() / "config.yaml"
            if cfg_file.is_file():
                for line in cfg_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if s.startswith("embedding_provider:"):
                        val = s.partition(":")[2].strip().strip('"\'')
                        if val in ("local", "gateway", "off"):
                            os.environ["SKILL_PROOF_EMBEDDING_PROVIDER"] = val
                        break
        except Exception:
            pass

    if args.command in ("install", "uninstall", "clients"):
        mcp_env = {}
        if os.environ.get("SKILL_PROOF_EMBEDDING_PROVIDER") in ("local", "gateway"):
            mcp_env["SKILL_PROOF_EMBEDDING_PROVIDER"] = os.environ["SKILL_PROOF_EMBEDDING_PROVIDER"]
        spec = mcp_install.ServerSpec(
            name="skill-proof",
            module="skill_proof.mcp_server",
            env=mcp_env if mcp_env else None,
        )
        if args.command == "clients":
            return mcp_install.run_clients(spec)
        rc = mcp_install.run_install(spec, args, remove=(args.command == "uninstall"))
        if args.command == "install" and not getattr(args, "dry_run", False) and not getattr(args, "print_only", False):
            _sync_skill_proof_antigravity_schema()
            _sync_skill_proof_global_skill()
            try:
                from skill_proof import vault
                roots = _collect_roots(getattr(args, "root", []), profile=getattr(args, "profile", None))
                mig = vault.migrate_from_roots(roots, dry_run=False)
                v_status = vault.status(roots=roots)
                print(
                    f"[Skill Proof Setup Wizard] Global Vault: {v_status['vault_dir']} "
                    f"({v_status['vault_skills_count']} canonical skills, {mig.get('migrated_count', 0)} newly migrated)"
                )
            except Exception:
                pass
            try:
                try:
                    from . import bridge as _bridge_mod
                except (ImportError, ValueError):
                    import bridge as _bridge_mod
                ws = pathlib.Path(__file__).resolve().parent.parent.parent
                _bridge_mod.sync_global_trinity_habits(workspace_dir=ws if ws.is_dir() else None)
            except Exception:
                pass
        return rc

    if args.command == "audit":
        try:
            from . import prune as _prune_mod
        except (ImportError, ValueError):
            import prune as _prune_mod
        action = getattr(args, "audit_action", "stats")
        a_path = getattr(args, "audit_path", "") or None
        if action == "stats":
            res = _prune_mod.audit_stats(audit_path=a_path)
            if getattr(args, "json", False):
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Skill Proof Telemetry Statistics ({res['total_turns']} turns audited):")
                print(f"  Hit rate: {res['hit_rate']:.1%} (Selected: {res['status_counts'].get('selected', 0)}, Miss: {res['status_counts'].get('no_match', 0)})")
                print(f"  Vault skills: {res['vault_skills_count']} total ({res['used_skills_count']} active in telemetry, {res['dead_skills_count']} unused)")
                print("\nTop invoked skills:")
                for name, cnt in res["top_skills"]:
                    print(f"  - {name:<28}: {cnt} hits")
                if res["dead_skills"]:
                    print(f"\nUnused candidates ({len(res['dead_skills'])}):")
                    for d in res["dead_skills"][:10]:
                        print(f"  • {d}")
                    if len(res["dead_skills"]) > 10:
                        print(f"  ... and {len(res['dead_skills'])-10} more")
            return 0
        if action == "prune":
            target_skills = [args.skill_name] if getattr(args, "skill_name", None) else None
            res = _prune_mod.prune_dead_skills(skills_to_prune=target_skills, dry_run=getattr(args, "dry_run", False))
            if getattr(args, "json", False):
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                dry_label = " (dry run)" if res["dry_run"] else ""
                print(f"Skill Proof Pruning: {res['pruned_count']} archived{dry_label}, {res['skipped_count']} skipped")
                for p in res.get("pruned", []):
                    print(f"  ✓ Archived: {p['skill']} -> {p['destination']}")
                for s in res.get("skipped", []):
                    print(f"  • Skipped: {s['skill']} ({s['reason']})")
            return 0

    if args.command == "compile":
        try:
            from . import bridge as _bridge_mod
        except (ImportError, ValueError):
            import bridge as _bridge_mod
        ws = pathlib.Path(args.workspace).resolve() if getattr(args, "workspace", None) else pathlib.Path.cwd()
        actions = _bridge_mod.sync_global_trinity_habits(workspace_dir=ws, dry_run=getattr(args, "dry_run", False))
        if getattr(args, "json", False):
            print(json.dumps({"workspace": str(ws), "actions": actions}, indent=2, ensure_ascii=False))
        else:
            print(f"Skill Proof Rule Compiler (Workspace: {ws}):")
            for act in actions:
                print(f"  ✓ {act}")
        return 0

    if args.command == "handoff":
        try:
            from . import handoff as _handoff_mod
        except (ImportError, ValueError):
            import handoff as _handoff_mod
        ws = pathlib.Path(args.workspace).resolve() if getattr(args, "workspace", None) else pathlib.Path.cwd()
        out = pathlib.Path(args.output).resolve() if getattr(args, "output", None) else None
        res = _handoff_mod.export_handoff(
            target=getattr(args, "target", "antigravity"),
            workspace=ws,
            output_path=out,
            launch=getattr(args, "launch", False),
        )
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"Skill Proof Handoff Payload Generated for [{res['target'].upper()}]:")
            print(f"  ✓ Saved to: {res['handoff_file']}")
            print(f"  ✓ Tickets tracked: {res['tickets_count']}")
            print(f"  ✓ Prompt length: {res['prompt_chars']} chars (~{res['prompt_chars']//4} tokens)")
            print("\n" + "="*50)
            print(res["prompt"])
            print("="*50)
        return 0

    if args.command == "vault":
        from skill_proof import vault
        action = getattr(args, "vault_action", "status")
        roots = _collect_roots(args.root, profile=args.profile)
        if action == "migrate":
            res = vault.migrate_from_roots(roots, dry_run=getattr(args, "dry_run", False))
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Global Vault Migration: {res['migrated_count']} migrated, {res['skipped_count']} skipped into {res['vault']}")
                for item in res.get("migrated", []):
                    print(f"  + {item['skill']} <- {item['source']}")
            return 0
        if action == "pin":
            skill_name = getattr(args, "skill_name", None)
            target = getattr(args, "target_root", None)
            if not skill_name or not target:
                print("error: --skill <name> and --to <dir> are required for pin", file=sys.stderr)
                return 2
            res = vault.pin(
                skill_name,
                pathlib.Path(target),
                dry_run=getattr(args, "dry_run", False),
                force=getattr(args, "force", False),
            )
            print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else f"Pin: {res}")
            return 0 if res.get("success") else 1
        if action == "unpin":
            skill_name = getattr(args, "skill_name", None)
            target = getattr(args, "from_root", None) or getattr(args, "target_root", None)
            if not skill_name or not target:
                print("error: --skill <name> and --from <dir> are required for unpin", file=sys.stderr)
                return 2
            res = vault.unpin(skill_name, pathlib.Path(target), dry_run=getattr(args, "dry_run", False))
            print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else f"Unpin: {res}")
            return 0 if res.get("success") else 1
        if action == "sync":
            res = vault.sync(roots=roots, dry_run=getattr(args, "dry_run", False))
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Sync complete for vault: {res['vault']}")
            return 0
        if action == "link-all":
            target = getattr(args, "target_root", None)
            if not target:
                print("error: --to <dir> is required for link-all", file=sys.stderr)
                return 2
            res = vault.link_all(
                pathlib.Path(target),
                dry_run=getattr(args, "dry_run", False),
                force=getattr(args, "force", False),
            )
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Vault link-all: {res['linked']}/{res['total']} skills pinned to {res['target']}")
            return 0
        if action == "reconcile":
            res = vault.reconcile_all_clients(
                roots=roots,
                dry_run=getattr(args, "dry_run", False),
                force=getattr(args, "force", False),
            )
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Skill Proof Vault Reconciled: {res['vault_dir']} ({res['total_canonical_skills']} canonical skills)")
                for cid, cinfo in res.get("clients", {}).items():
                    print(f"  ✓ {cid:<16}: {cinfo.get('status')} -> {cinfo.get('path')}")
            return 0
        if action in ("lock", "unlock"):
            skill_name = getattr(args, "skill_name", None)
            if not skill_name:
                print(f"error: --skill <name> is required for {action}", file=sys.stderr)
                return 2
            vdir = vault.init_vault()
            target_skill = vdir / skill_name
            if not target_skill.is_dir():
                print(f"error: skill '{skill_name}' not found in vault {vdir}", file=sys.stderr)
                return 1
            ok = vault.set_skill_readonly(target_skill, readonly=(action == "lock"))
            if getattr(args, "json", False):
                print(json.dumps({"skill": skill_name, "action": action, "success": ok}))
            else:
                status_str = "LOCKED (Read-Only)" if action == "lock" else "UNLOCKED (Writable)"
                print(f"Skill Proof Vault: '{skill_name}' is now {status_str}")
            return 0 if ok else 1
        if action == "patch":
            pop = getattr(args, "patch_op", "rebase")
            skill_name = getattr(args, "skill_name", None)
            if pop in ("export", "apply") and not skill_name:
                print(f"error: --skill <name> is required for patch {pop}", file=sys.stderr)
                return 2
            if pop == "export":
                pcontent = getattr(args, "patch_content", "") or ""
                pfile = vault.export_patch(str(skill_name), pcontent)
                if getattr(args, "json", False):
                    print(json.dumps({"skill": skill_name, "patch_file": str(pfile)}))
                else:
                    print(f"Skill Proof Patch Exported: {pfile}")
                return 0
            if pop == "apply":
                ok = vault.apply_patch(str(skill_name))
                if getattr(args, "json", False):
                    print(json.dumps({"skill": skill_name, "success": ok}))
                else:
                    print(f"Skill Proof Patch Applied for '{skill_name}': {ok}")
                return 0 if ok else 1
            if pop == "rebase":
                res = vault.rebase_patches()
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2, ensure_ascii=False))
                else:
                    print(f"Skill Proof Patch Rebase: {len(res['applied'])} applied, {len(res['failed'])} failed")
                    for s in res['applied']:
                        print(f"  ✓ {s}")
                    for s in res['failed']:
                        print(f"  ✗ {s}")
                return 0
        # Default: status
        res = vault.status(roots=roots)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"Skill Proof Global Vault: {res['vault_dir']} (exists: {res['vault_exists']})")
            print(f"  Canonical Skills in Vault: {res['vault_skills_count']}")
            if res.get("clients"):
                print("  Client Ecosystems:")
                for cid, cinfo in res["clients"].items():
                    print(f"    - {cid:<16}: {cinfo['total_skills']} skills ({cinfo['pinned_to_vault']} pinned to vault, {cinfo['local_physical']} local)")
        return 0

    if args.command == "setup":
        provider = args.provider or input("Embedding provider [off/local/gateway] (off): ").strip().lower() or "off"
        if provider not in {"off", "local", "gateway"}:
            print("error: provider must be off, local, or gateway", file=sys.stderr)
            return 2
        model = args.model or "Xenova/multilingual-e5-small"
        gateway_url = args.gateway_url or "https://api.openai.com/v1/embeddings"
        gateway_key_env = args.gateway_key_env or "OPENAI_API_KEY"
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
    if args.command == "read":
        from . import reading
        try:
            payload = reading.read_skill(args.name, roots, level=args.level)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        payload["level"] = args.level
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"skill: {payload['name']}")
            print(f"level: {args.level}")
            print(f"source: {payload['source_path']}")
            print()
            print(payload["content"])
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
    if args.command in ("crystallize", "mine"):
        from . import crystallize as _cryst_mod
        from . import trace_skills as _trace_mod

        if getattr(args, "from_traces", False):
            traces = getattr(args, "traces", None) or None
            min_runs = int(getattr(args, "min_runs", 3) or 3)
            limit = int(getattr(args, "limit", 8) or 8)
            category = getattr(args, "category", "devops") or "devops"
            vault_path = getattr(args, "vault", None)
            hermes_path = getattr(args, "hermes", None)
            dry_run = bool(getattr(args, "dry_run", False))

            evidence = _trace_mod.mine_traces(traces, min_runs=min_runs, limit=limit)
            if not evidence:
                print(f"[skill-proof] No repeatable workflows found (need >= {min_runs} runs with >= 80% clean completion) in trace log.")
                return 0

            for ev in evidence:
                s_name = _trace_mod.skill_name(ev)
                s_desc = f"Repeatable {ev.signature} workflow ({ev.runs} observed runs)"
                steps = [
                    f"Run `{ev.signature}` from the project root.",
                    "Read the output; trimtok compresses it into context automatically.",
                    "Confirm the command exits 0 before treating the result as fact.",
                ]
                skill_md, proof = _cryst_mod.crystallize_skill(
                    name=s_name,
                    description=s_desc,
                    steps=steps,
                    verification_cmd=ev.signature,
                    category=category,
                    when_to_use=f"Use when the task requires `{ev.signature}` repeatedly.",
                    pitfalls=[
                        "Mined from telemetry: the observed runs compressed cleanly, but a "
                        "clean compression is not proof that the command succeeded.",
                    ],
                    evidence=_trace_mod.evidence_lines(ev),
                )
                if dry_run:
                    print(f"[dry-run] {s_name}: {ev.runs} runs ({ev.clean_runs} clean), {ev.saved_chars:,} chars saved - status {proof.status}")
                else:
                    res = _cryst_mod.save_crystallized_skill(
                        skill_md=skill_md,
                        name=s_name,
                        category=category,
                        vault_dir=vault_path,
                        hermes_skills_dir=hermes_path,
                    )
                    print(f"Crystallized skill '{proof.skill_name}' (status: {proof.status}, proof: {proof.proof_hash[:12]}...)")
                    print(f"  Vault: {res.get('vault_file')}")
                    if res.get("hermes_file"):
                        print(f"  Hermes: {res.get('hermes_file')}")
            return 0

        # Manual single-skill crystallization
        name = getattr(args, "name", "")
        desc = getattr(args, "desc", "")
        cmd = getattr(args, "cmd", "")
        if not (name and desc and cmd):
            print("error: --name, --desc and --cmd are required unless --from-traces is used", file=sys.stderr)
            return 2
        steps = getattr(args, "step", None) or ["Execute validated procedure via terminal."]
        cat = getattr(args, "category", "devops")
        v_path = getattr(args, "vault", None)
        h_path = getattr(args, "hermes", None)

        skill_md, proof = _cryst_mod.crystallize_skill(
            name=name, description=desc, steps=steps,
            verification_cmd=cmd, exit_code=0, category=cat,
        )
        res = _cryst_mod.save_crystallized_skill(
            skill_md=skill_md, name=name, category=cat,
            vault_dir=v_path, hermes_skills_dir=h_path,
        )
        print(f"Crystallized skill '{proof.skill_name}' (status: {proof.status}, proof: {proof.proof_hash[:12]}...)")
        print(f"  Vault: {res.get('vault_file')}")
        if res.get("hermes_file"):
            print(f"  Hermes: {res.get('hermes_file')}")
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
