"""Cross-Agent Session Handoff Serializer.

Exports the current workspace state, active tickets, git diff, and MemCore
constraints into a clean, bounded payload for switching to another AI model
(e.g. Antigravity/Argon, Claude Code, Codex).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sqlite3
from typing import Any, Optional

def collect_handoff_state(workspace: Optional[pathlib.Path] = None) -> dict[str, Any]:
    ws = (workspace or pathlib.Path.cwd()).resolve()
    
    # 1. Git status & diff
    git_status = ""
    git_diff_stat = ""
    try:
        res = subprocess.run(["git", "-C", str(ws), "status", "-s"], capture_output=True, text=True, check=False)
        git_status = res.stdout.strip()
        res_diff = subprocess.run(["git", "-C", str(ws), "diff", "--stat"], capture_output=True, text=True, check=False)
        git_diff_stat = res_diff.stdout.strip()
    except Exception:
        pass

    # 2. Tickets from .scratch
    tickets = []
    scratch_dir = ws / ".scratch"
    if scratch_dir.is_dir():
        for issue_file in sorted(scratch_dir.glob("**/issues/*.md")):
            try:
                content = issue_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                title = lines[0].lstrip("#").strip() if lines else issue_file.stem
                status = "ready-for-agent"
                for line in lines:
                    if "status:" in line.lower():
                        status = line.replace("*", "").split(":", 1)[1].strip().lower()
                        break
                tickets.append({
                    "id": issue_file.name,
                    "title": title,
                    "status": status,
                    "path": str(issue_file.relative_to(ws)),
                })
            except Exception:
                pass

    # 3. MemCore Constraints & Rules
    memories = []
    try:
        db = pathlib.Path.home() / ".memcore" / "memory.db"
        if db.is_file():
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = conn.execute("""
                SELECT v.content, m.type FROM memory m
                JOIN memory_version v ON v.id = m.current_version_id
                WHERE m.lifecycle = 'accepted'
                LIMIT 10
            """)
            for content, mtype in cur.fetchall():
                if content:
                    memories.append(f"[{mtype}] {content.strip()}")
            conn.close()
    except Exception:
        pass

    return {
        "workspace": str(ws),
        "git_status": git_status,
        "git_diff_stat": git_diff_stat,
        "tickets": tickets,
        "memories": memories,
    }


def format_handoff_prompt(state: dict[str, Any], target: str = "antigravity") -> str:
    lines = [
        f"# Session Handoff -> {target.upper()}",
        "",
        f"**Workspace:** `{state['workspace']}`",
        "",
        "## Pending & Active Tickets",
    ]
    if state["tickets"]:
        for t in state["tickets"]:
            box = "[x]" if t["status"] == "complete" else "[ ]"
            lines.append(f"- {box} **{t['title']}** (`{t['path']}`)")
    else:
        lines.append("No open tickets found in `.scratch/`.")

    lines.extend([
        "",
        "## Git Working Tree State",
    ])
    if state["git_status"]:
        lines.append("```text")
        lines.append(state["git_status"])
        if state["git_diff_stat"]:
            lines.append("\nDiff stat:")
            lines.append(state["git_diff_stat"])
        lines.append("```")
    else:
        lines.append("Working tree clean.")

    if state["memories"]:
        lines.extend([
            "",
            "## Active MemCore Standing Constraints",
        ])
        for m in state["memories"][:5]:
            lines.append(f"- {m}")

    lines.extend([
        "",
        "## Next Steps",
        "Continue executing the next ready-for-agent ticket from the frontier.",
    ])
    return "\n".join(lines)


def export_handoff(
    target: str = "antigravity",
    workspace: Optional[pathlib.Path] = None,
    output_path: Optional[pathlib.Path] = None,
    launch: bool = False,
) -> dict[str, Any]:
    ws = (workspace or pathlib.Path.cwd()).resolve()
    state = collect_handoff_state(ws)
    prompt = format_handoff_prompt(state, target=target)
    
    out_file = output_path or (ws / ".scratch" / f"handoff-{target}.md")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(prompt, encoding="utf-8")
    
    launched = False
    if launch and target in ("antigravity", "agy"):
        try:
            # Launch agy with the handoff prompt
            cmd = ["agy", prompt]
            subprocess.Popen(cmd, cwd=str(ws))
            launched = True
        except Exception:
            launched = False

    return {
        "success": True,
        "target": target,
        "handoff_file": str(out_file),
        "prompt_chars": len(prompt),
        "tickets_count": len(state["tickets"]),
        "launched": launched,
        "prompt": prompt,
    }
