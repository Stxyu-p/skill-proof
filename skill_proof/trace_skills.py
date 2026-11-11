"""Mine repeatable workflows from telemetry traces into candidate skills.

Records every transform applied: the tool, the command (for terminal calls),
the gate, and the characters saved. A command that shows up many times and
always compresses cleanly is a workflow the agent actually repeats -- that
is the raw material for a crystallized skill.

Two rules keep this honest:

* Mining is evidence, not verification. A run where compression succeeded
  says nothing about whether the command succeeded, so mined skills get
  an ``unverified`` proof and an ``Evidence`` block stating exactly what
  was observed. Governance still decides whether they become truth.
* Commands are read-only input. Nothing here writes to the telemetry store.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime
import json
import pathlib
import re
import shlex
from typing import Sequence

DEFAULT_TRACE_PATH = "~/.trimtok/usage.jsonl"

_NOISE_PROGRAMS = frozenset(
    {
        "ls", "cd", "pwd", "echo", "cat", "head", "tail", "wc", "mkdir",
        "rm", "mv", "cp", "touch", "true", "false", "sleep", "clear", "printf",
        "export", "source", "which", "whereis", "type", "env", "set",
        "find", "grep", "egrep", "rg", "ag", "fd", "sed", "awk", "diff",
    }
)

_INLINE_FLAGS = frozenset({"-c", "-e", "--eval"})

_NON_REVERT_GATES = frozenset(
    {"ok", "passthrough_small", "passthrough_reference", "passthrough_sentinel",
     "passthrough_policy"}
)

_READ_ONLY_GIT = frozenset(
    {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "blame", "grep"}
)

_CD_PREFIX_RE = re.compile(r"^\s*cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s*(?:&&|;)\s*")


@dataclasses.dataclass(frozen=True)
class TraceEvidence:
    """What the telemetry actually shows for one command signature."""

    signature: str
    runs: int
    clean_runs: int
    saved_chars: int
    first_seen: str
    last_seen: str
    sample_command: str

    @property
    def clean_ratio(self) -> float:
        return self.clean_runs / self.runs if self.runs else 0.0


def _strip_shell_prefix(command: str) -> str:
    """Drop `cd <dir> &&` chains and leading env assignments."""
    text = str(command or "").strip()
    while True:
        stripped = _CD_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    parts = text.split()
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts.pop(0)
    return " ".join(parts)


def command_signature(command: str) -> str | None:
    """Program plus its first verb, e.g. ``uv run pytest`` or ``git push``.

    Returns None for one-offs (inline ``-c`` payloads), shell plumbing, and
    anything too short to be a workflow.
    """
    text = _strip_shell_prefix(command)
    if not text:
        return None
    for separator in ("&&", "||", ";", "|"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return None

    prog = pathlib.Path(tokens[0]).name
    if prog.endswith((".exe", ".cmd", ".bat")):
        prog = prog.rsplit(".", 1)[0]
    prog = prog.lower()

    if prog in _NOISE_PROGRAMS:
        return None

    for flag in _INLINE_FLAGS:
        if flag in tokens[1:]:
            return None

    if prog == "git":
        verbs = [t for t in tokens[1:] if not t.startswith("-")]
        if not verbs or verbs[0].lower() in _READ_ONLY_GIT:
            return None
        return f"git {verbs[0].lower()}"

    if prog in ("python", "python3", "python.exe", "py"):
        sub = tokens[1:]
        if sub and sub[0] == "-m" and len(sub) > 1:
            return f"python -m {sub[1]}"
        scripts = [t for t in sub if not t.startswith("-")]
        if scripts:
            s_name = pathlib.Path(scripts[0]).name
            return f"python {s_name}"
        return None

    if prog in ("uv", "poetry", "pipenv", "pnpm", "npm", "yarn", "bun"):
        verbs = [t for t in tokens[1:] if not t.startswith("-")]
        if len(verbs) >= 2 and verbs[0] in ("run", "exec"):
            return f"{prog} {verbs[0]} {verbs[1]}"
        if verbs:
            return f"{prog} {verbs[0]}"
        return None

    verbs = [t for t in tokens[1:] if not t.startswith("-")]
    if verbs:
        v_name = pathlib.Path(verbs[0]).name
        return f"{prog} {v_name}"
    return None


def skill_name(evidence: TraceEvidence) -> str:
    """Deterministic skill slug from a command signature."""
    s = re.sub(r"[^\w\s-]", "", evidence.signature.lower())
    slug = re.sub(r"[-\s]+", "-", s).strip("-")
    return f"workflow-{slug}" if not slug.startswith("workflow-") else slug


def _format_iso(timestamp: float | int | None) -> str:
    if not timestamp:
        return "1970-01-01T00:00:00Z"
    try:
        dt = datetime.datetime.fromtimestamp(float(timestamp), tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError):
        return "1970-01-01T00:00:00Z"


def mine_traces(
    trace_files: Sequence[str | pathlib.Path] | None = None,
    *,
    min_runs: int = 3,
    min_clean_ratio: float = 0.8,
    limit: int = 8,
) -> list[TraceEvidence]:
    """Scan usage logs, cluster by command signature, return repeated workflows."""
    paths: list[pathlib.Path] = []
    if trace_files:
        for p in trace_files:
            paths.append(pathlib.Path(p).expanduser().resolve())
    else:
        default = pathlib.Path(DEFAULT_TRACE_PATH).expanduser().resolve()
        if default.is_file():
            paths.append(default)

    if not paths:
        return []

    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for path in paths:
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cmd = record.get("cmd") or record.get("command")
                    if not cmd:
                        continue
                    sig = command_signature(cmd)
                    if sig:
                        buckets[sig].append(record)
        except OSError:
            continue

    evidence: list[TraceEvidence] = []
    for sig, runs in buckets.items():
        total = len(runs)
        if total < min_runs:
            continue
        clean = sum(1 for r in runs if r.get("gate") in _NON_REVERT_GATES)
        ratio = clean / total if total else 0.0
        if ratio < min_clean_ratio:
            continue
        saved = sum(int(r.get("saved") or 0) for r in runs)
        timestamps = [r.get("ts") for r in runs if r.get("ts")]
        first_seen = _format_iso(min(timestamps)) if timestamps else "1970-01-01T00:00:00Z"
        last_seen = _format_iso(max(timestamps)) if timestamps else "1970-01-01T00:00:00Z"
        sample = runs[-1].get("cmd") or sig
        evidence.append(
            TraceEvidence(
                signature=sig,
                runs=total,
                clean_runs=clean,
                saved_chars=saved,
                first_seen=first_seen,
                last_seen=last_seen,
                sample_command=str(sample),
            )
        )

    evidence.sort(key=lambda e: (e.runs, e.clean_runs, e.saved_chars), reverse=True)
    return evidence[:limit]


def evidence_lines(evidence: TraceEvidence) -> list[str]:
    """Human-readable bullet list describing what telemetry proves."""
    pct = int(evidence.clean_ratio * 100)
    return [
        f"- **Observed runs**: {evidence.runs} runs recorded in telemetry.",
        f"- **Clean completion ratio**: {pct}% ({evidence.clean_runs}/{evidence.runs} runs).",
        f"- **Context savings**: ~{evidence.saved_chars:,} characters.",
        f"- **Window**: {evidence.first_seen} .. {evidence.last_seen}",
        f"- **Observed invocation**: `{evidence.sample_command}`",
    ]
