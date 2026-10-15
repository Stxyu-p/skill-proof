from __future__ import annotations

"""Compliance layer: guard results and the turn-policy store."""

import json
import os
import pathlib
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional, Protocol

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
