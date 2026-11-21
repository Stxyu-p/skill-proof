from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

from skill_proof import _vector, cli, core, crystallize, doctor, mcp_server, vault
from skill_proof._catalog import SkillRecord, find_stub_markers, scan_catalog
from skill_proof._vector import VectorIndex


class VaultIntegrityAndDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp_dir.name)
        self.vdir = self.root / "vault"
        vault.init_vault(self.vdir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_pin_self_loop_guard_preserves_source(self):
        skill_dir = self.vdir / "sample-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: sample-skill\ndescription: Sample skill\n---\nDo it.\n", encoding="utf-8")

        # Direct self-pin to vdir must NOT delete source
        res = vault.pin("sample-skill", self.vdir, vault_dir=self.vdir, force=True)
        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("already_in_vault"))
        self.assertTrue(skill_md.is_file())
        self.assertFalse(vault.create_junction_or_link(skill_dir, skill_dir, force=True))
        self.assertTrue(skill_md.is_file())

    def test_stub_corruption_and_nested_drift_detection_zero_false_positive_on_docs(self):
        # Vault clean copy
        v_skill = self.vdir / "tdd-workflow"
        v_skill.mkdir(parents=True)
        (v_skill / "SKILL.md").write_text(
            "---\nname: tdd-workflow\ndescription: Clean canonical TDD skill\n---\n"
            "Documentation mentions inline code example `[tt:prose ... #c1]` inside backticks.\n",
            encoding="utf-8",
        )
        # Nested client copy that drifted AND got stub-corrupted on standalone line
        client_root = self.root / "hermes_skills"
        nested_dir = client_root / "software-development" / "tdd-workflow"
        nested_dir.mkdir(parents=True)
        (nested_dir / "SKILL.md").write_text(
            "---\nname: tdd-workflow\ndescription: Drifted skill\n---\n\n[tt:prose ~100w #c24O]\n",
            encoding="utf-8",
        )

        # Backticked example in vault must NOT trigger stub detector
        self.assertEqual(find_stub_markers((v_skill / "SKILL.md").read_text(encoding="utf-8")), [])

        cat = scan_catalog({"vault": self.vdir, "hermes": client_root})
        diag_codes = {d.code for d in cat.diagnostics}
        self.assertIn("stub_corrupted", diag_codes)
        self.assertIn("shadowed_by_root", diag_codes)

        st = vault.status(vault_dir=self.vdir, roots={"hermes": client_root})
        hermes_stat = st["clients"]["hermes"]
        self.assertEqual(hermes_stat["drifted_count"], 1)
        self.assertEqual(hermes_stat["stub_corrupted_count"], 1)

        # Prioritized diagnostics keep stub_corrupted/shadowed ahead of alias_skipped
        scan_out = mcp_server._tool_scan({"roots": [str(self.vdir), str(client_root)]})
        self.assertGreaterEqual(len(scan_out["diagnostics"]), 2)
        self.assertIn(scan_out["diagnostics"][0]["code"], ("stub_corrupted", "shadowed_by_root"))

    def test_crystallize_from_candidates_file_stdin_dry_run_and_protections(self):
        cand_row = {
            "schema": "skill-proof.candidate.v1",
            "slug": "sqlite-wal-check",
            "name": "sqlite-wal-check",
            "description": "Verify SQLite WAL integrity cleanly",
            "category": "devops",
            "when_to_use": "Before deploying DB changes",
            "steps": ["Run PRAGMA integrity_check", "Check journal_mode=wal"],
            "pitfalls": ["Never bump migrations live"],
            "verification_cmd": "echo ok",
            "evidence_refs": ["mem-1234"],
            "provenance_sha256": "abc123",
            "source_tool": "memcore",
        }
        cand_file = self.root / "candidates.jsonl"
        cand_file.write_text(json.dumps(cand_row) + "\n", encoding="utf-8")

        # 1. Dry-run JSON from file
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main([
                "crystallize", "--from-candidates", str(cand_file),
                "--vault", str(self.vdir), "--dry-run", "--json",
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["crystallized"][0]["skill"], "sqlite-wal-check")
        self.assertFalse((self.vdir / "devops" / "sqlite-wal-check").exists())

        # 2. Real save from stdin '-'
        buf2 = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(cand_row) + "\n")):
            with contextlib.redirect_stdout(buf2):
                rc2 = cli.main([
                    "crystallize", "--from-candidates", "-",
                    "--vault", str(self.vdir), "--json",
                ])
        self.assertEqual(rc2, 0)
        payload2 = json.loads(buf2.getvalue())
        self.assertTrue(payload2["crystallized"][0]["saved"])
        saved_md = pathlib.Path(payload2["crystallized"][0]["vault_file"])
        self.assertTrue(saved_md.is_file())

        # 3. Stub corruption inside content must be rejected
        with self.assertRaises(ValueError):
            crystallize.save_crystallized_skill(
                "---\nname: bad\ndescription: bad\n---\n\n[tt:prose ~50w #c1]\n",
                name="bad",
                vault_dir=self.vdir,
            )

        # 4. Immutable skill cannot be overwritten
        locked_dir = self.vdir / "locked-skill"
        locked_dir.mkdir(parents=True)
        (locked_dir / "SKILL.md").write_text(
            "---\nname: locked-skill\ndescription: Locked\nimmutable: true\n---\nLocked.\n",
            encoding="utf-8",
        )
        with self.assertRaises(PermissionError):
            crystallize.save_crystallized_skill(
                "---\nname: locked-skill\ndescription: Attempted overwrite\n---\nNew.\n",
                name="locked-skill",
                category="nonexistent-flat",
                vault_dir=self.vdir,
            )

    def test_apply_patch_fail_closed_and_vault_lock_rejects_traversal(self):
        sdir = self.vdir / "patch-target"
        sdir.mkdir(parents=True)
        md = sdir / "SKILL.md"
        original = "---\nname: patch-target\ndescription: Original\n---\nClean body.\n"
        md.write_text(original, encoding="utf-8")
        pfile = self.root / "bad.patch"
        pfile.write_text("not a valid git diff\n", encoding="utf-8")

        self.assertFalse(vault.apply_patch("patch-target", patch_file=pfile, vault_dir=self.vdir))
        self.assertEqual(md.read_text(encoding="utf-8"), original)

        with mock.patch.dict(os.environ, {"SKILL_PROOF_VAULT": str(self.vdir)}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["vault", "lock", "--skill", "../evil", "--json"])
            self.assertNotEqual(rc, 0)
            self.assertIn("invalid skill name", buf.getvalue())

    def test_vector_index_multi_model_migration_endianness_and_reload(self):
        vdb = self.root / "embeddings.db"
        # Create legacy single-column PK schema
        conn = sqlite3.connect(vdb)
        conn.execute(
            """
            CREATE TABLE skill_embeddings (
                skill_name TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE query_embeddings (
                query_sha256 TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        vec_a = (1.0, 0.0, 0.0, 0.0)
        conn.execute(
            "INSERT INTO skill_embeddings VALUES (?, ?, ?, ?, ?, ?)",
            ("alpha", "sha-a", "model-one", 4, struct.pack("<4f", *vec_a), 1.0),
        )
        conn.commit()
        conn.close()

        class _FakeEmbedder:
            def __init__(self, v):
                self._v = v

            def embed(self, texts, is_query=False):
                return [self._v for _ in texts]

        rec = SkillRecord(
            skill_id="test:alpha",
            root_id="test",
            relative_path="alpha/SKILL.md",
            name="alpha",
            normalized_name="alpha",
            description="Alpha skill",
            tags=(),
            aliases=(),
            related_skills=(),
            source_sha256="sha-a",
            source_bytes=0,
            source_path=self.root / "alpha" / "SKILL.md",
            root_path=self.root,
        )

        # Open with model-two -> must migrate schema cleanly and store model-two row without overwriting model-one
        idx2 = VectorIndex(db_path=vdb, provider="off", model="model-two", embedder=_FakeEmbedder((0.0, 1.0, 0.0, 0.0)))
        self.assertEqual(idx2.sync_catalog([rec]), 1)

        c2 = sqlite3.connect(vdb)
        try:
            rows = c2.execute("SELECT model FROM skill_embeddings WHERE skill_name='alpha' ORDER BY model").fetchall()
            self.assertEqual([r[0] for r in rows], ["model-one", "model-two"])
        finally:
            c2.close()

        # Cross-process reload in another VectorIndex instance when cache updated by peer
        idx_reader = VectorIndex(db_path=vdb, provider="off", model="model-two", embedder=_FakeEmbedder((0.0, 1.0, 0.0, 0.0)))
        idx_reader._memory_vectors.clear()
        self.assertEqual(idx_reader.sync_catalog([rec]), 0)
        self.assertIn("alpha", idx_reader._memory_vectors)

        # Missing local model preserves provider_configured='local' and local_model_unavailable
        idx_local = VectorIndex(db_path=vdb, provider="local", model="nonexistent/model", models_dir=self.root / "no_models")
        self.assertTrue(idx_local._local_unavailable)
        idx_local.rank("alpha query")
        snap = idx_local.trace_snapshot()
        self.assertEqual(snap["provider_configured"], "local")
        self.assertEqual(snap["fallback_reason"], "local_model_unavailable")

    def test_doctor_json_contract_and_mcp_new_tools(self):
        (self.vdir / "good-skill").mkdir(parents=True)
        (self.vdir / "good-skill" / "SKILL.md").write_text(
            "---\nname: good-skill\ndescription: Clean skill\n---\nBody.\n",
            encoding="utf-8",
        )
        fake_hermes = self.root / "absent-hermes"
        audit_path = self.root / "audit.jsonl"

        with mock.patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(fake_hermes),
                "SKILL_PROOF_VAULT": str(self.vdir),
                "SKILL_PROOF_AUDIT_PATH": str(audit_path),
                "SKILL_PROOF_EMBEDDING_PROVIDER": "off",
                "SKILL_PROOF_VECTOR_DB": str(self.root / "vec.db"),
            },
            clear=False,
        ):
            # MCP tools/list exposes 8 tools
            list_res = mcp_server.handle({"method": "tools/list"})
            names = [t["name"] for t in list_res["tools"]]
            for expected in ("skill_doctor", "skill_vault_status", "skill_overlap"):
                self.assertIn(expected, names)

            # Call skill_select -> writes standalone audit row
            sel_resp = mcp_server.handle(
                {"method": "tools/call", "params": {"name": "skill_select", "arguments": {"query": "good-skill", "roots": [str(self.vdir)]}}}
            )
            self.assertFalse(sel_resp["isError"])
            self.assertTrue(audit_path.is_file())

            # Call skill_doctor via MCP and CLI doctor --json
            doc_mcp = mcp_server.handle(
                {"method": "tools/call", "params": {"name": "skill_doctor", "arguments": {"roots": [str(self.vdir)]}}}
            )
            doc_payload = json.loads(doc_mcp["content"][0]["text"])
            self.assertEqual(doc_payload["tool"], "skill-proof")
            self.assertEqual(doc_payload["status"], "ok")
            hermes_chk = next(c for c in doc_payload["checks"] if c["id"] == "hermes_integration")
            self.assertEqual(hermes_chk["status"], "info")

            # Introduce stub corruption -> doctor status must be 'fail' and CLI exits 1
            (self.vdir / "bad-stub").mkdir()
            (self.vdir / "bad-stub" / "SKILL.md").write_text(
                "---\nname: bad-stub\ndescription: Corrupted\n---\n\n[tt:prose ~80w #c24O]\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["doctor", "--root", str(self.vdir), "--json"])
            self.assertEqual(rc, 1)
            cli_payload = json.loads(buf.getvalue())
            self.assertEqual(cli_payload["status"], "fail")

    def test_doctor_vector_readonly_and_unreadable_graceful_degradation(self):
        (self.vdir / "good-skill").mkdir(parents=True)
        (self.vdir / "good-skill" / "SKILL.md").write_text(
            "---\nname: good-skill\ndescription: Clean skill\n---\nBody.\n",
            encoding="utf-8",
        )
        vdb = self.root / "existing_embeddings.db"
        vdb.write_bytes(b"")  # empty existing file; doctor with provider=off must NOT modify it
        fake_hermes = self.root / "absent-hermes"
        audit_path = self.root / "audit_ro.jsonl"

        with mock.patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(fake_hermes),
                "SKILL_PROOF_VAULT": str(self.vdir),
                "SKILL_PROOF_AUDIT_PATH": str(audit_path),
                "SKILL_PROOF_EMBEDDING_PROVIDER": "off",
                "SKILL_PROOF_VECTOR_DB": str(vdb),
            },
            clear=False,
        ):
            payload = doctor.run_doctor(roots={"vault": self.vdir}, vault_dir=self.vdir, vector_db=vdb)
            vec_chk = next(c for c in payload["checks"] if c["id"] == "vector_index")
            self.assertEqual(vec_chk["status"], "info")
            self.assertEqual(vdb.stat().st_size, 0)

            # readonly VectorIndex with absent DB does not create a DB file
            ro_absent = self.root / "absent_ro_vec.db"
            idx_ro = VectorIndex(db_path=ro_absent, provider="local", models_dir=self.root / "no_models", readonly=True)
            self.assertIsNotNone(idx_ro)
            self.assertFalse(ro_absent.exists())

            # OperationalError during vector_index_from_env degrades to warn instead of crashing
            with mock.patch.object(
                _vector, "vector_index_from_env", side_effect=sqlite3.OperationalError("unable to open database file")
            ):
                err_payload = doctor.run_doctor(roots={"vault": self.vdir}, vault_dir=self.vdir, vector_db=vdb)
            err_chk = next(c for c in err_payload["checks"] if c["id"] == "vector_index")
            self.assertEqual(err_chk["status"], "warn")
            self.assertIn("unreadable", err_chk["detail"].lower())

    def test_vector_off_never_opens_rw_and_engine_fails_open(self):
        (self.vdir / "good-skill").mkdir(parents=True)
        (self.vdir / "good-skill" / "SKILL.md").write_text(
            "---\nname: good-skill\ndescription: Clean skill\n---\nBody.\n",
            encoding="utf-8",
        )
        vdb = self.root / "zero_off.db"
        vdb.write_bytes(b"")
        with mock.patch.dict(os.environ, {"SKILL_PROOF_EMBEDDING_PROVIDER": "off"}, clear=False):
            idx_off = _vector.vector_index_from_env(db_path=vdb)
        self.assertIsNotNone(idx_off)
        self.assertEqual(idx_off.provider, "off")
        self.assertEqual(vdb.stat().st_size, 0)

        with mock.patch.object(
            core, "vector_index_from_env", side_effect=sqlite3.OperationalError("unable to open database file")
        ):
            engine = core.SkillProofEngine.from_config({"vault": self.vdir})
            self.assertIsNone(engine.vector_index)
            with mock.patch.dict(os.environ, {"HERMES_HOME": str(self.root / "absent-hermes")}, clear=False):
                self.assertIsNone(mcp_server._vector_index())


if __name__ == "__main__":
    unittest.main()
