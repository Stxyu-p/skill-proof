import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from skill_proof.cli import main


class TestCrystallizeCLI(unittest.TestCase):
    def test_crystallize_help_and_subcommand_exists(self):
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stdout", new=io.StringIO()):
                main(["crystallize", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_mine_alias_help(self):
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stdout", new=io.StringIO()):
                main(["mine", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_crystallize_manual_workflow(self):
        tmp = tempfile.mkdtemp(prefix="sp-cli-test-")
        vault_dir = pathlib.Path(tmp) / "vault"

        buf = io.StringIO()
        with patch("sys.stdout", new=buf):
            rc = main([
                "crystallize",
                "--name", "deploy-staging",
                "--desc", "Deploy current build to staging environment.",
                "--cmd", "npm run deploy:staging",
                "--vault", str(vault_dir),
            ])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Crystallized skill 'deploy-staging'", output)
        self.assertIn("status: declared", output)
        skill_text = (vault_dir / "deploy-staging" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("status: declared", skill_text)
        self.assertNotIn("status: verified", skill_text)

    def test_crystallize_manual_verify_runs_command(self):
        tmp = tempfile.mkdtemp(prefix="sp-cli-verify-")
        vault_dir = pathlib.Path(tmp) / "vault"

        buf = io.StringIO()
        with patch("sys.stdout", new=buf):
            rc = main([
                "crystallize",
                "--name", "echo-verify",
                "--desc", "Verify real command execution.",
                "--cmd", "python -c \"print(42)\"",
                "--verify",
                "--vault", str(vault_dir),
            ])
        self.assertEqual(rc, 0)
        self.assertIn("status: verified", buf.getvalue())

    def test_crystallize_manual_verify_failure_returns_nonzero(self):
        tmp = tempfile.mkdtemp(prefix="sp-cli-verify-fail-")
        vault_dir = pathlib.Path(tmp) / "vault"

        buf = io.StringIO()
        with patch("sys.stdout", new=buf):
            rc = main([
                "crystallize",
                "--name", "fail-verify",
                "--desc", "Verify failed command exits non-zero.",
                "--cmd", "python -c \"import sys; sys.exit(1)\"",
                "--verify",
                "--vault", str(vault_dir),
            ])
        self.assertEqual(rc, 1)
        self.assertIn("status: unverified", buf.getvalue())

    def test_crystallize_from_traces_dry_run(self):
        tmp = tempfile.mkdtemp(prefix="sp-cli-trace-")
        trace_file = pathlib.Path(tmp) / "usage.jsonl"
        rows = [
            {"cmd": "uv run pytest", "gate": "ok", "ts": 1794000000, "saved": 100},
            {"cmd": "uv run pytest", "gate": "ok", "ts": 1794000100, "saved": 120},
            {"cmd": "uv run pytest", "gate": "ok", "ts": 1794000200, "saved": 150},
        ]
        trace_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        buf = io.StringIO()
        with patch("sys.stdout", new=buf):
            rc = main([
                "crystallize",
                "--from-traces",
                "--traces", str(trace_file),
                "--min-runs", "2",
                "--dry-run",
            ])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("[dry-run] workflow-uv-run-pytest: 3 runs", output)

    def test_mine_from_missing_traces_fails_gracefully(self):
        buf = io.StringIO()
        with patch("sys.stdout", new=buf):
            rc = main([
                "mine",
                "--from-traces",
                "--traces", "nonexistent-traces.jsonl",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("No repeatable workflows found", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
