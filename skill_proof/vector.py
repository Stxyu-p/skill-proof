"""Vector search module for Skill Proof."""
from __future__ import annotations

try:
    from . import _vector
except (ImportError, ValueError):
    import _vector

for k, v in _vector.__dict__.items():
    if not k.startswith("__"):
        globals()[k] = v
