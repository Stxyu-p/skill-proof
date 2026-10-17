"""CLI entry point for skill-proof."""
from __future__ import annotations

import pathlib
import sys

_PKG_DIR = str(pathlib.Path(__file__).resolve().parent)
if _PKG_DIR not in sys.path:
    sys.path.append(_PKG_DIR)

from . import cli

def main(argv=None) -> int:
    return cli.main(argv)

if __name__ == "__main__":
    sys.exit(main())
