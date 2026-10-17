"""Ranking and dialogue module for Skill Proof."""
from __future__ import annotations

try:
    from . import _ranking, _dialogue
except (ImportError, ValueError):
    import _ranking
    import _dialogue

for mod in (_ranking, _dialogue):
    for k, v in mod.__dict__.items():
        if not k.startswith("__"):
            globals()[k] = v
