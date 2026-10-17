"""Catalog module for Skill Proof."""
from __future__ import annotations

try:
    from . import _catalog
except (ImportError, ValueError):
    import _catalog

for k, v in _catalog.__dict__.items():
    if not k.startswith("__"):
        globals()[k] = v
