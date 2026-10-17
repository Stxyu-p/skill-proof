"""Skill Proof - deterministic, offline skill router and global skill vault."""
from __future__ import annotations

import os
import pathlib
import sys

# Ensure package directory is importable as fallback
_PKG_DIR = str(pathlib.Path(__file__).resolve().parent)
if _PKG_DIR not in sys.path:
    sys.path.append(_PKG_DIR)

from . import _catalog
sys.modules.setdefault("_catalog", _catalog)
from . import models
sys.modules.setdefault("models", models)
from . import _vector
sys.modules.setdefault("_vector", _vector)
from . import _dialogue
sys.modules.setdefault("_dialogue", _dialogue)
from . import _ranking
sys.modules.setdefault("_ranking", _ranking)
from . import _compliance
sys.modules.setdefault("_compliance", _compliance)
from . import core
sys.modules.setdefault("core", core)
from . import bridge
sys.modules.setdefault("bridge", bridge)
from . import vault, mcp_install

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
        sys.modules.setdefault("cli", mod)
        globals()["cli"] = mod
        return mod
    if name == "mcp_server":
        import importlib
        mod = importlib.import_module(".mcp_server", __name__)
        sys.modules.setdefault("mcp_server", mod)
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
