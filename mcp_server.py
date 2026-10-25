#!/usr/bin/env python3
"""Skill Proof standalone MCP server entrypoint."""
import sys
from skill_proof.mcp_server import main

if __name__ == "__main__":
    sys.exit(main())
