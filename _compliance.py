"""skill-proof root shim: forwards to skill_proof._compliance."""
from __future__ import annotations
import sys
import skill_proof._compliance as _m

sys.modules[__name__] = _m
for _k, _v in _m.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
