"""Portability tests: cross-agent root detection, the CLI, and the MCP server."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import core

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]


def _skill_bytes(name: str, description: str) -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody text.\n".encode("utf-8")


class RootDetectionTests(unittest.TestCase):
    def test_detects_only_existing_roots_in_precedence_order(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_detect_") as tmp:
            home = pathlib.Path(tmp) / "home"
            cwd = pathlib.Path(tmp) / "project"
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".agents" / "skills").mkdir(parents=True)
            (home / ".gemini" / "antigravity-cli" / "skills").mkdir(parents=True)
            (cwd / ".agents" / "skills").mkdir(parents=True)
            (home / ".cursor" / "skills").mkdir(parents=True)

            detected = core.detect_agent_roots(home=home, cwd=cwd, hermes_home="")

            self.assertEqual(
                list(detected),
                ["project-agents", "agents", "claude", "antigravity-cli", "cursor"],
            )
            self.assertEqual(detected["agents"], home / ".agents" / "skills")
            self.assertNotIn("gemini", detected)
            self.assertNotIn("opencode", detected)

    def test_detects_codex_and_gemini_config_roots(self):
        with tempfile.TemporaryDirectory(prefix="skill_proof_detect_extra_") as tmp:
            home = pathlib.Path(tmp) / "home"
            (home / ".codex" / "skills").mkdir(parents=True)
            (home / ".gemini" / "config" / "skills").mkdir(parents=True)
            detected = core.detect_agent_roots(home=home, hermes_home="")
            self.assertIn("codex", detected)
            self.assertIn("gemini-config", detected)
            self.assertEqual(detected["codex"], home / ".codex" / "skills")
            self.assertEqual(detected["gemini-config"], home / ".gemini" / "config" / "skills")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_cli_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        folder = self.root / "demo-skill"
        folder.mkdir()
        (folder / "SKILL.md").write_bytes(_skill_bytes("demo-skill", "Demo workflow"))

    def run_cli(self, *arguments):
        completed = subprocess.run(
            [sys.executable, str(_PLUGIN_DIR / "cli.py"), *arguments],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def test_select_json(self):
        output = self.run_cli("select", "--query", "Use $demo-skill", "--root", str(self.root), "--json")
        payload = json.loads(output)
        self.assertEqual(payload["version"], core.__version__)
        self.assertEqual(payload["decision"]["status"], "selected")
        self.assertEqual(payload["selected"]["name"], "demo-skill")
        self.assertEqual(payload["selected"]["root_id"], "root-1")

    def test_scan_json_counts_and_suggestions(self):
        output = self.run_cli("scan", "--root", str(self.root), "--json")
        payload = json.loads(output)
        self.assertEqual(payload["skill_count"], 1)
        self.assertEqual(payload["diagnostic_counts"], {})
        self.assertEqual(payload["root_suggestions"], [])

    def test_select_plain_text(self):
        output = self.run_cli("select", "--query", "Use $demo-skill", "--root", str(self.root))
        self.assertIn("decision: selected (explicit_skill)", output)
        self.assertIn("demo-skill", output)

    def test_roots_json(self):
        payload = json.loads(self.run_cli("roots", "--json"))
        self.assertEqual(payload["version"], core.__version__)
        self.assertIsInstance(payload["roots"], dict)


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill_proof_mcp_")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        folder = self.root / "demo-skill"
        folder.mkdir()
        (folder / "SKILL.md").write_bytes(_skill_bytes("demo-skill", "Demo workflow"))

    def converse(self, requests):
        completed = subprocess.run(
            [sys.executable, str(_PLUGIN_DIR / "mcp_server.py")],
            input="\n".join(json.dumps(item) for item in requests) + "\n",
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    def test_initialize_lists_tools_and_calls_select(self):
        responses = self.converse(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "skill_select",
                        "arguments": {"query": "Use $demo-skill", "roots": [str(self.root)]},
                    },
                },
            ]
        )
        # The notification gets no response.
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "skill-proof")
        tool_names = [tool["name"] for tool in responses[1]["result"]["tools"]]
        self.assertEqual(tool_names, ["skill_roots", "skill_scan", "skill_select"])
        call = responses[2]["result"]
        self.assertFalse(call["isError"])
        payload = json.loads(call["content"][0]["text"])
        self.assertEqual(payload["selected"]["name"], "demo-skill")
        self.assertEqual(payload["decision"]["status"], "selected")

    def test_unknown_method_and_tool_errors_do_not_crash(self):
        responses = self.converse(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "no/such/method"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "no-such-tool", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "skill_select", "arguments": {"query": "  "}},
                },
                {"jsonrpc": "2.0", "id": 4, "method": "ping"},
            ]
        )
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["error"]["code"], -32601)
        self.assertEqual(responses[1]["error"]["code"], -32601)
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertEqual(responses[3]["result"], {})


class VersionConsistencyTests(unittest.TestCase):
    def test_manifest_matches_core_version(self):
        manifest = (_PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        version_line = next(
            line for line in manifest.splitlines() if line.startswith("version:")
        )
        self.assertEqual(version_line.split(":", 1)[1].strip(), core.__version__)


if __name__ == "__main__":
    unittest.main()
