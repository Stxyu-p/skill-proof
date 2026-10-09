#!/usr/bin/env python3
"""Optional, removable host bridges for Skill Proof.

Everything this file writes is marked with ``<!-- skill-proof:bridge:start -->``
markers or a fixed file name, so every piece can be removed again. Nothing here
is required: the engine already ships a portable ``cli.py`` and a stdio
``mcp_server.py`` for MCP-capable hosts.

    python bridge.py init --host claude-code      # drop in the PreToolUse hook
    python bridge.py init --host codex             # add an AGENTS.md section
    python bridge.py init --host cursor            # add a .cursor/rules file
    python bridge.py status                        # what is installed, what isn't
    python bridge.py remove --host codex           # delete exactly what was added
    python bridge.py hook --event pre-tool-use     # runtime: read stdin, print JSON

``hook`` is the only runtime piece: it reads one JSON object on stdin (the
Claude Code hook payload) and prints the hook response on stdout. It never
raises, never exits non-zero for input it does not understand, and never
contacts the network. Tool policy comes from ``skill-proof-bridge.json`` next
to this file (``forbidden_tools``), so the bridge stays a small, static policy
enforcer rather than a second copy of the router.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Mapping

import core

MARKER_START = "<!-- skill-proof:bridge:start -->"
MARKER_END = "<!-- skill-proof:bridge:end -->"

HOSTS = ("claude-code", "codex", "gemini", "cursor", "cline", "windsurf", "copilot", "auto")

AGENTS_SECTION = """## Skill Proof

Skill Proof selects one skill per request with deterministic lexical ranking
(no model call, no network). Ask it before loading a skill by hand:

```bash
python {cli} select --query "<what you want to do>" --json
python {cli} overlap   # near-duplicate skills worth pruning
```

{cli} auto-detects the skill roots of common agent ecosystems, so it works
from this repository without configuration. Treat its `selected` field as the
skill to load; if it returns `status: no_match` or `ambiguous`, ask instead of
guessing.
"""

GEMINI_SECTION = """## Skill Proof

Skill Proof selects one skill per request with deterministic lexical ranking
(no model call, no network). Ask it before loading a skill by hand:

```bash
python {cli} select --query "<what you want to do>" --json
python {cli} overlap   # near-duplicate skills worth pruning
```

{cli} auto-detects the skill roots of common agent ecosystems (Gemini, Antigravity,
Hermes, Codex), so it works from this workspace without configuration.
"""

COMPACT_SECTION = """## Skill Proof

Skill Proof selects one skill per request with deterministic lexical ranking.
Ask it before loading a skill by hand:

```bash
python {cli} select --query "<what you want to do>" --json
```
"""

CLINE_SECTION = COMPACT_SECTION
WINDSURF_SECTION = COMPACT_SECTION
COPILOT_SECTION = COMPACT_SECTION

HOST_FILE_REL: dict[str, str] = {
    "codex": "AGENTS.md",
    "gemini": "GEMINI.md",
    "cline": ".clinerules",
    "windsurf": ".windsurfrules",
    "copilot": ".github/copilot-instructions.md",
    "cursor": ".cursor/rules/skill-proof.mdc",
    "claude-code": "skill-proof-hook.py",
}

HOST_SECTIONS: dict[str, str] = {
    "codex": AGENTS_SECTION,
    "gemini": GEMINI_SECTION,
    "cline": CLINE_SECTION,
    "windsurf": WINDSURF_SECTION,
    "copilot": COPILOT_SECTION,
}


def host_target_path(host: str, target: pathlib.Path) -> pathlib.Path:
    return target / pathlib.Path(HOST_FILE_REL[host])


CURSOR_RULE = """---
description: Skill Proof — deterministic skill selection for this workspace
globs:
alwaysApply: false
---

Ask Skill Proof which skill to load before loading one by hand:

```bash
python {cli} select --query "<what you want to do>" --json
```

- `selected` present → load that skill.
- `status: no_match` or `ambiguous` → do not guess; ask which skill to use.
- `cli.py overlap` lists near-duplicate skills before you add another one.
- The bridge is optional; delete this file and the skill still exists.
"""

CLAUDE_HOOK_SNIPPET = """{{
  "hooks": {{
    "PreToolUse": [
      {{
        "matcher": "*",
        "hooks": [
          {{
            "type": "command",
            "command": "python \\"{hook}\\" --event pre-tool-use"
          }}
        ]
      }}
    ]
  }}
}}
"""


def _here() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent


def _policy_path(target: pathlib.Path) -> pathlib.Path:
    return target / "skill-proof-bridge.json"


def load_policy(target: pathlib.Path) -> dict:
    path = _policy_path(target)
    if not path.is_file():
        return {"forbidden_tools": [], "note": "Add tool names here to deny them through the hook."}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"forbidden_tools": [], "note": "policy file unreadable; nothing denied"}
    if not isinstance(data, dict) or not isinstance(data.get("forbidden_tools"), list):
        return {"forbidden_tools": [], "note": "policy file malformed; nothing denied"}
    return data


def write_policy(target: pathlib.Path, *, dry_run: bool = False) -> pathlib.Path:
    path = _policy_path(target)
    if path.exists():
        return path
    payload = {
        "forbidden_tools": [],
        "note": (
            "Tool names listed here are denied by bridge.py hook. "
            "Delete this file (or the whole bridge) to stop gating."
        ),
        "version": core.__version__,
    }
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _replace_section(text: str, body: str) -> tuple[str, str]:
    """Insert or refresh the marked section. Returns (new_text, action)."""
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start != -1 and end != -1 and end > start:
        closing = end + len(MARKER_END)
        return text[:start] + f"{MARKER_START}\n{body}\n{MARKER_END}" + text[closing:], "updated"
    separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    addition = f"{separator}{MARKER_START}\n{body}\n{MARKER_END}\n"
    return text + addition, "added"


def _remove_section(text: str) -> tuple[str, str]:
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        return text, "absent"
    closing = end + len(MARKER_END)
    head, tail = text[:start], text[closing:]
    cleaned = head + tail.lstrip("\n")
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned, "removed"


def _existing_section(text: str) -> tuple[str, str]:
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        return "", "absent"
    body = text[start + len(MARKER_START) : end].strip("\n")
    return body, "present"


def detect_host_environments(target: pathlib.Path) -> list[str]:
    """Inspect target folder and system environment to detect active agent hosts."""
    detected = []
    if (target / ".cursor").is_dir() or (target / ".cursor" / "rules").is_dir():
        detected.append("cursor")
    if (target / ".claude").is_dir() or (target / "settings.json").is_file():
        detected.append("claude-code")
    if (target / "GEMINI.md").is_file() or (target / ".gemini").is_dir():
        detected.append("gemini")
    if (target / ".clinerules").is_file():
        detected.append("cline")
    if (target / ".windsurfrules").is_file():
        detected.append("windsurf")
    if (target / ".github" / "copilot-instructions.md").is_file() or (target / ".github").is_dir():
        detected.append("copilot")
    if (target / "AGENTS.md").is_file() or (target / ".codex").is_dir():
        detected.append("codex")
    if not detected:
        # Default to universal codex/agents standard
        detected.append("codex")
    return detected


def init_host(host: str, target: pathlib.Path, *, dry_run: bool = False, force: bool = False) -> list[str]:
    """Write the optional files for *host*. Returns human-readable actions."""
    if host == "auto":
        detected = detect_host_environments(target)
        actions = []
        for h in detected:
            actions.extend(init_host(h, target, dry_run=dry_run, force=force))
        return actions

    if host == "claude-code":
        hook_path = target / "skill-proof-hook.py"
        source = _here() / "bridge.py"
        actions = []
        if not hook_path.exists() or force:
            body = (
                '#!/usr/bin/env python3\n'
                '"""Skill Proof hook for Claude Code (optional, removable).\n\n'
                'Installed by `python bridge.py init --host claude-code`. Delete this file\n'
                'and remove the PreToolUse entry from settings.json to uninstall.\n'
                '"""\n'
                "import pathlib\n"
                "import runpy\n"
                "import sys\n\n"
                f"sys.path.insert(0, {str(_here())!r})\n"
                f"runpy.run_path({str(source)!r}, run_name='__main__')\n"
            )
            if not dry_run:
                hook_path.parent.mkdir(parents=True, exist_ok=True)
                hook_path.write_text(body, encoding="utf-8")
            actions.append(f"wrote {hook_path}" + (" (dry run)" if dry_run else ""))
        else:
            actions.append(f"kept existing {hook_path} (use --force to rewrite)")
        write_policy(target, dry_run=dry_run)
        actions.append("paste this into settings.json (hooks.PreToolUse):")
        actions.append(CLAUDE_HOOK_SNIPPET.format(hook=hook_path).rstrip())
        return actions

    if host in HOST_SECTIONS:
        path = host_target_path(host, target)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        body, action = _replace_section(existing, HOST_SECTIONS[host].format(cli=f"python {_here() / 'cli.py'}"))
        if existing == body:
            actions = [f"{path}: already up to date"]
        else:
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
            actions = [f"{path}: {action}" + (" (dry run)" if dry_run else "")]
        write_policy(target, dry_run=dry_run)
        return actions

    # cursor
    path = host_target_path("cursor", target)
    body = CURSOR_RULE.format(cli=f"python {_here() / 'cli.py'}")
    if path.is_file() and not force:
        actions = [f"{path}: already exists (use --force to rewrite)"]
    else:
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        actions = [f"{path}: {'written' if not dry_run else 'would write (dry run)'}"]
    write_policy(target, dry_run=dry_run)
    return actions


def remove_host(host: str, target: pathlib.Path) -> list[str]:
    """Undo exactly what init_host wrote for *host*."""
    actions = []
    if host == "claude-code":
        hook_path = target / "skill-proof-hook.py"
        if hook_path.is_file():
            hook_path.unlink()
            actions.append(f"deleted {hook_path}")
        else:
            actions.append(f"{hook_path}: not present")
        actions.append("remove the PreToolUse entry that mentions skill-proof-hook.py from settings.json")
        return actions

    if host in HOST_SECTIONS:
        path = host_target_path(host, target)
        if not path.is_file():
            return [f"{path}: not present"]
        text = path.read_text(encoding="utf-8")
        cleaned, action = _remove_section(text)
        if action == "removed" and cleaned != text:
            path.write_text(cleaned, encoding="utf-8")
        actions.append(f"{path}: {action}")
        return actions

    path = host_target_path("cursor", target)
    if path.is_file():
        path.unlink()
        actions.append(f"deleted {path}")
    else:
        actions.append(f"{path}: not present")
    return actions


def status(target: pathlib.Path) -> dict[str, Any]:
    policy = _policy_path(target)
    cursor_path = host_target_path("cursor", target)
    hook_path = host_target_path("claude-code", target)

    def _sec(p: pathlib.Path) -> tuple[str, bool]:
        if not p.is_file():
            return "absent", False
        _, state = _existing_section(p.read_text(encoding="utf-8"))
        return state, True

    hosts_status: dict[str, Any] = {
        "claude-code": {"hook_file": str(hook_path), "present": hook_path.is_file()},
        "cursor": {"file": str(cursor_path), "present": cursor_path.is_file()},
    }
    for host in ("codex", "gemini", "cline", "windsurf", "copilot"):
        p = host_target_path(host, target)
        state, present = _sec(p)
        hosts_status[host] = {"file": str(p), "section": state, "present": present}

    return {
        "version": core.__version__,
        "target": str(target),
        "policy": {"path": str(policy), "present": policy.is_file()},
        "hosts": hosts_status,
        "note": "Every bridge file is optional and removable with `python bridge.py remove --host ...`",
    }


def run_hook(payload: Mapping) -> dict:
    """Claude Code PreToolUse response for one payload. Never raises."""
    tool = str(payload.get("tool_name") or "").strip()
    policy = load_policy(_here())
    forbidden = {str(name).strip().lower() for name in policy.get("forbidden_tools", [])}
    if tool and tool.lower() in forbidden:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Skill Proof policy forbids {tool} (skill-proof-bridge.json). "
                    "Remove it from forbidden_tools or delete the bridge to allow it."
                ),
            }
        }
    # Nothing to say: let the host decide as if we were not installed.
    return {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bridge", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "write the optional files for a host"),
        ("remove", "delete exactly what init wrote"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--host", required=True, choices=HOSTS)
        sub.add_argument("--target", default=".", help="project directory (default: cwd)")
        if name == "init":
            sub.add_argument("--dry-run", action="store_true", help="print, write nothing")
            sub.add_argument("--force", action="store_true", help="overwrite an existing file")
    subparsers.add_parser("status", help="what is installed").add_argument(
        "--target", default="."
    )
    sub_install = subparsers.add_parser("install", help="one-line auto-detection and installation")
    sub_install.add_argument("--host", default="auto", choices=HOSTS, help="target agent host (default: auto)")
    sub_install.add_argument("--target", default=".", help="project directory (default: cwd)")
    sub_install.add_argument("--dry-run", action="store_true", help="print, write nothing")
    sub_install.add_argument("--force", action="store_true", help="overwrite an existing file")
    subparsers.add_parser("ai-setup", help="print deterministic instructions for AI coding agents")
    hook_parser = subparsers.add_parser("hook", help="runtime entrypoint (stdin JSON -> stdout JSON)")
    hook_parser.add_argument("--event", default="pre-tool-use")
    args = parser.parse_args(argv)

    if args.command == "hook":
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
            payload = payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            payload = {}
        response = run_hook(payload) if args.event == "pre-tool-use" else {}
        if response:
            print(json.dumps(response, ensure_ascii=False))
        return 0

    if args.command == "ai-setup":
        cli_path = _here() / "cli.py"
        print("=== Skill Proof AI Agent Setup Instructions ===")
        print(f"1. Check skill roots: python {cli_path} roots")
        print(f"2. Select skill for task: python {cli_path} select --query \"<user request>\" --json")
        print(f"3. Auto-install workspace bridge: python {_here() / 'bridge.py'} install")
        return 0

    target = pathlib.Path(getattr(args, "target", ".")).expanduser()
    if args.command == "status":
        print(json.dumps(status(target), indent=2, ensure_ascii=False))
        return 0
    if args.command in ("init", "install"):
        for line in init_host(
            args.host, target, dry_run=args.dry_run, force=args.force
        ):
            print(line)
        return 0
    if args.command == "ai-setup":
        cli_path = _here() / "cli.py"
        print("=== Skill Proof AI Agent Setup Instructions ===")
        print(f"1. Check skill roots: python {cli_path} roots")
        print(f"2. Select skill for task: python {cli_path} select --query \"<user request>\" --json")
        print(f"3. Auto-install workspace bridge: python {_here() / 'bridge.py'} install")
        return 0
    for line in remove_host(args.host, target):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
