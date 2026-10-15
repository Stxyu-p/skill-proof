from __future__ import annotations

"""Compliance layer: guard results and the turn-policy store."""

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str
    message: str = ""
