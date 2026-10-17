#!/usr/bin/env python3
"""skill-proof root shim: forwards to skill_proof.cli."""
from __future__ import annotations
import sys
import skill_proof.cli as _m

sys.modules[__name__] = _m
for _k, _v in _m.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if __name__ == "__main__":
    sys.exit(_m.main())
