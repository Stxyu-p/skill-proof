"""Guard, bridge, and compliance module for Skill Proof."""
from __future__ import annotations

try:
    from . import bridge, _compliance
except (ImportError, ValueError):
    import bridge
    import _compliance

for mod in (bridge, _compliance):
    for k, v in mod.__dict__.items():
        if not k.startswith("__"):
            globals()[k] = v
