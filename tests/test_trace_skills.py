"""Trace mining: signatures, evidence thresholds, and honest proof status."""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from skill_proof.crystallize import crystallize_skill
from skill_proof.trace_skills import (
    TraceEvidence,
    command_signature,
    evidence_lines,
    mine_traces,
    skill_name,
)


def _row(cmd, gate="ok", ts=1794000000, saved=10):
    return {"cmd": cmd, "gate": gate, "ts": ts, "saved": saved}


class TestCommandSignature(unittest.TestCase):
    def test_strips_cd_chain_and_keeps_verb(self):
        self.assertEqual(
            command_signature("cd C:/Users/x/repo && uv run pytest tests/test_a.py"),
            "uv run pytest",
        )

    def test_keeps_python_module_verb(self):
        self.assertEqual(
            command_signature("python -m unittest tests/test_vault.py"),
            "python -m unittest",
        )

    def test_rejects_printf_shell_noise(self):
        self.assertIsNone(command_signature("printf '%s\\n' '--- root ---'"))

    def test_path_arguments_reduce_to_basename(self):
        self.assertEqual(
            command_signature("python C:/Users/x/repo/tests/test_a.py"),
            "python test_a.py",
        )

    def test_rejects_inline_payloads(self):
        self.assertIsNone(command_signature('python -c "print(1)"'))

    def test_rejects_read_only_and_plumbing(self):
        for cmd in ("git status -s", "ls -la", "grep -rn foo .", "find /c/x"):
            self.assertIsNone(command_signature(cmd), cmd)

    def test_rejects_bare_interpreter_without_verb(self):
        self.assertEqual(command_signature("pytest tests/"), "pytest tests")
        self.assertIsNone(command_signature("pytest"))
        self.assertIsNone(command_signature(""))


class TestMineTraces(unittest.TestCase):
    def _write(self, rows):
        tmp = tempfile.TemporaryDirectory()
        path = pathlib.Path(tmp.name) / "usage.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        self.addCleanup(tmp.cleanup)
        return path

    def test_keeps_only_repeated_commands(self):
        path = self._write(
            [_row("uv run pytest", ts=100), _row("uv run pytest", ts=200),
             _row("uv run pytest", ts=300), _row("make deploy")]
        )
        found = mine_traces([path], min_runs=3)
        self.assertEqual([e.signature for e in found], ["uv run pytest"])
        self.assertEqual(found[0].runs, 3)
        self.assertEqual(found[0].first_seen, "1970-01-01T00:01:40Z")

    def test_reverted_runs_do_not_count_as_clean(self):
        path = self._write(
            [_row("gh repo view", gate="reverted_gate") for _ in range(5)]
            + [_row("gh repo view")]
        )
        self.assertEqual(mine_traces([path], min_runs=3), [])

    def test_small_passthrough_counts_as_clean(self):
        path = self._write(
            [_row("python -m unittest", gate="passthrough_small") for _ in range(4)]
        )
        found = mine_traces([path], min_runs=3)
        self.assertEqual(found[0].clean_runs, 4)

    def test_limit_and_malformed_lines(self):
        rows = [_row("uv run pytest")] * 5 + [_row("make test")] * 6
        path = self._write(rows)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("not json\n\n")
        self.assertEqual(len(mine_traces([path], min_runs=3, limit=1)), 1)
        self.assertEqual(mine_traces([path])[0].signature, "make test")

    def test_missing_file_is_empty_not_error(self):
        self.assertEqual(mine_traces([pathlib.Path("does-not-exist.jsonl")]), [])


if __name__ == "__main__":
    unittest.main()
