from __future__ import annotations

"""Compliance layer: guard results and the turn-policy store."""

import json
import os
import pathlib
from collections import Counter
from dataclasses import dataclass, field, replace
from statistics import median
from typing import Any, Mapping, Optional, Protocol, Sequence

from _catalog import default_hermes_home


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str
    message: str = ""


class PolicyStoreProtocol(Protocol):
    """Structural seam for reading/writing the active turn state (plan 5.1)."""

    def read(self) -> dict[str, Any]:
        ...

    def write(self, state: Mapping[str, Any]) -> None:
        ...


@dataclass
class FilePolicyStore:
    """Reads and writes ``active_turn.json``, the state host hooks enforce."""

    path: pathlib.Path

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, state: Mapping[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(dict(state), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # fail-open: advisory state must never break a turn


class InMemoryPolicyStore:
    """In-memory seam implementation for tests and embedders of the bridge."""

    def __init__(self, state: Optional[Mapping[str, Any]] = None) -> None:
        self.state: dict[str, Any] = dict(state or {})

    def read(self) -> dict[str, Any]:
        return dict(self.state)

    def write(self, state: Mapping[str, Any]) -> None:
        self.state = dict(state)


def default_policy_store() -> FilePolicyStore:
    """``<hermes home>/plugin-data/skill-proof/active_turn.json``."""
    return FilePolicyStore(default_hermes_home() / "plugin-data" / "skill-proof" / "active_turn.json")


def read_audit_records(path) -> tuple[list[dict[str, Any]], int]:
    """Read a ``skill-proof.audit.v1`` JSONL file: (records, corrupt_line_count)."""
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    records: list[dict[str, Any]] = []
    corrupt = 0
    for line in raw:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(item, dict) and item.get("schema") == "skill-proof.audit.v1":
            records.append(item)
    return records, corrupt


def calibrate_audit(
    records: Sequence[Mapping[str, Any]],
    *,
    min_score: float,
    min_margin: float,
    path: str = "",
    corrupt: int = 0,
) -> dict[str, Any]:
    """Deterministic threshold/synonym suggestions from audit records (plan 3.1).

    Advisory only: it never mutates config and never writes the log.
    """
    total = len(records)
    by_status = Counter(str(row.get("status") or "unknown") for row in records)
    overrides = [row for row in records if row.get("override_loaded")]
    scores = [
        float(row["score"])
        for row in records
        if isinstance(row.get("score"), (int, float)) and not isinstance(row.get("score"), bool)
    ]
    min_score = float(min_score)
    min_margin = float(min_margin)
    suggestions: list[dict[str, Any]] = []
    miss_ratio = by_status.get("no_match", 0) / total if total else 0.0
    if miss_ratio >= 0.35 and min_score > 0.10:
        suggestions.append(
            {
                "setting": "min_score",
                "current": min_score,
                "suggested": round(max(0.10, min_score - 0.04), 2),
                "reason": (
                    f"{miss_ratio:.0%} of audited turns ended no_match; "
                    "a lower threshold recovers near-threshold hits."
                ),
            }
        )
    ambiguous_ratio = by_status.get("ambiguous", 0) / total if total else 0.0
    if ambiguous_ratio >= 0.20 and min_margin > 0.0:
        suggestions.append(
            {
                "setting": "min_margin",
                "current": min_margin,
                "suggested": round(max(0.0, min_margin - 0.02), 2),
                "reason": (
                    f"{ambiguous_ratio:.0%} of audited turns were ambiguous; a smaller "
                    "margin lets the lexical winner through after candidate review."
                ),
            }
        )
    if len(overrides) >= 3:
        top = [
            name
            for name, _ in Counter(str(row.get("override_loaded")) for row in overrides).most_common(5)
        ]
        suggestions.append(
            {
                "setting": "synonyms",
                "current": None,
                "suggested": top,
                "reason": (
                    f"{len(overrides)} turns loaded a different skill than was selected; "
                    "map those names as synonyms or aliases."
                ),
            }
        )
    return {
        "path": path,
        "turns": total,
        "corrupt": corrupt,
        "by_status": dict(sorted(by_status.items())),
        "median_selected_score": round(median(scores), 3) if scores else None,
        "overrides": len(overrides),
        "suggestions": suggestions,
    }


def format_calibrate(payload: Mapping[str, Any]) -> str:
    """Human-readable rendering of :func:`calibrate_audit` output."""
    lines = [f"Skill Proof calibration over {payload.get('turns', 0)} audited turns"]
    by_status = payload.get("by_status") or {}
    if by_status:
        lines.append("  status: " + ", ".join(f"{key}={value}" for key, value in by_status.items()))
    if payload.get("median_selected_score") is not None:
        lines.append(f"  median selected score: {payload['median_selected_score']}")
    lines.append(f"  host loaded something else: {payload.get('overrides', 0)} turns")
    suggestions = payload.get("suggestions") or []
    if not suggestions:
        lines.append("  no threshold changes suggested - settings match the recorded outcomes.")
    for item in suggestions:
        current = item.get("current")
        current_text = f" (current {current})" if current is not None else ""
        lines.append(f"  -> {item['setting']}: {item['suggested']}{current_text}: {item['reason']}")
    return "\n".join(lines)
