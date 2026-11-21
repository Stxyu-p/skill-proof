"""Skill Proof test package."""
import pathlib
import sys

_repo_root = str(pathlib.Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

