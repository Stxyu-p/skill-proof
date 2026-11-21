"""Skill Proof - deterministic, offline skill router and global skill vault."""
from __future__ import annotations

import os
import pathlib
import sys

from . import (
    _catalog,
    _compliance,
    _dialogue,
    _ranking,
    _vector,
    bridge,
    core,
    doctor,
    mcp_install,
    models,
    vault,
)

# Clean public aliases
catalog = _catalog
ranking = _ranking
dialogue = _dialogue
vector = _vector
guard = bridge


def __getattr__(name: str):
    if name == "cli":
        import importlib
        mod = importlib.import_module(".cli", __name__)
        globals()["cli"] = mod
        return mod
    if name == "mcp_server":
        import importlib
        mod = importlib.import_module(".mcp_server", __name__)
        globals()["mcp_server"] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .core import (
    SkillProofEngine,
    VectorIndex,
    __version__,
    calibrate_audit,
    default_hermes_home,
    detect_agent_roots,
    format_calibrate,
    normalize_identifier,
    read_audit_records,
    scan_catalog,
)
