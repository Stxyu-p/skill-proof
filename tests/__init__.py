"""Skill Proof test package."""
import pathlib
import sys

_pkg = str(pathlib.Path(__file__).resolve().parent.parent / "skill_proof")
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)

