"""Unit tests for Skill Proof v0.15.0:
- Model Registry & Checksum Verification
- Local ONNX Embedder
- Neutral Provider Abstraction (off | local | gateway)
- Gateway graceful fallback (429, timeout, network error)
- Token-cut pre-LLM context
- CLI subcommands (models, status)
"""

import hashlib
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import cli
import core
import models
from core import Catalog, SkillRecord, VectorIndex, select_skill


class ModelRegistryTests(unittest.TestCase):
    def test_registry_contains_three_verified_models(self):
        self.assertIn("Xenova/multilingual-e5-small", models.MODEL_REGISTRY)
        self.assertIn("Xenova/multilingual-e5-small-fp32", models.MODEL_REGISTRY)
        self.assertIn("Xenova/paraphrase-multilingual-MiniLM-L12-v2", models.MODEL_REGISTRY)

        for key, spec in models.MODEL_REGISTRY.items():
            self.assertEqual(spec["dim"], 384)
            self.assertTrue(spec["license"])
            self.assertTrue(spec["credit"])
            self.assertTrue(spec["source_url"].startswith("https://huggingface.co/"))
            self.assertIn("config.json", spec["files"])
            self.assertIn("tokenizer.json", spec["files"])

    def test_check_model_status_on_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = models.check_model_status("Xenova/multilingual-e5-small", root_dir=tmp)
            self.assertFalse(st["installed"])
            self.assertFalse(st["valid"])
            self.assertGreater(len(st["missing_files"]), 0)

    def test_download_file_verified_rejects_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "bad.txt"
            # Mock urlopen to return bytes that don't match expected sha
            dummy_resp = mock.MagicMock()
            dummy_resp.__enter__.return_value = dummy_resp
            dummy_resp.read.side_effect = [b"bad content", b""]
            with mock.patch("urllib.request.urlopen", return_value=dummy_resp):
                ok = models.download_file_verified(
                    "http://example.com/bad.txt",
                    target,
                    expected_size=11,
                    expected_sha256="0000000000000000000000000000000000000000000000000000000000000000",
                )
                self.assertFalse(ok)
                self.assertFalse(target.exists())

    def test_all_registered_model_sha256_checksums_are_valid_64_hex_digits(self):
        for key, spec in models.MODEL_REGISTRY.items():
            for relpath, meta in spec["files"].items():
                self.assertRegex(
                    meta["sha256"], r"^[0-9a-f]{64}$",
                    f"{key}:{relpath} must carry a 64-hex-digit SHA-256 digest",
                )

    def test_check_model_status_and_prefetch_detect_corrupted_same_size_file(self):
        payload = b"good-model-payload"
        good_sha = hashlib.sha256(payload).hexdigest()
        spec = {
            "repo_id": "test/corrupt-model",
            "name": "Test model",
            "license": "MIT",
            "credit": "test",
            "source_url": "https://huggingface.co/test/corrupt-model",
            "dim": 384,
            "prefix_query": "query: ",
            "prefix_passage": "passage: ",
            "files": {"weights.bin": {"size": len(payload), "sha256": good_sha}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with mock.patch.dict(models.MODEL_REGISTRY, {"test/corrupt-model": spec}):
                model_dir = models.get_model_dir("test/corrupt-model", root)
                model_dir.mkdir(parents=True)
                corrupted = b"evil-model-payload"  # same byte length, different content
                self.assertEqual(len(corrupted), len(payload))
                (model_dir / "weights.bin").write_bytes(corrupted)

                status = models.check_model_status("test/corrupt-model", root)
                self.assertFalse(status["installed"])
                self.assertFalse(status["valid"])
                self.assertTrue(any("sha256 mismatch" in item for item in status["missing_files"]))

                calls = []

                def fake_download(url, target_path, expected_size, expected_sha256, timeout=120.0, callback=None):
                    calls.append((str(target_path), expected_sha256))
                    target_path.write_bytes(payload)
                    return True

                with mock.patch.object(models, "download_file_verified", side_effect=fake_download):
                    ok = models.download_model("test/corrupt-model", root)
                self.assertTrue(ok)
                self.assertEqual(len(calls), 1, "a same-size but corrupted file must be re-downloaded")
                self.assertEqual((model_dir / "weights.bin").read_bytes(), payload)
                after = models.check_model_status("test/corrupt-model", root)
                self.assertTrue(after["installed"])


class ProviderAbstractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "test_provider.db"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_provider_off_disables_vector_ranking(self):
        idx = VectorIndex(self.db_path, provider="off")
        self.assertEqual(idx.provider, "off")
        status = idx.provider_status()
        self.assertEqual(status["provider"], "off")
        self.assertFalse(status["active"])
        self.assertEqual(idx.rank("test query"), [])
        self.assertIsNone(idx.get_query_vector("test query"))

    def test_gateway_graceful_fallback_on_429(self):
        idx = VectorIndex(self.db_path, provider="gateway", api_key="sk-test", gateway_url="http://fake/v1/embeddings")
        self.assertEqual(idx.provider, "gateway")

        # Mock urllib to raise HTTP 429
        import urllib.error
        err = urllib.error.HTTPError("http://fake", 429, "Rate limit exceeded", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            vec = idx.get_query_vector("hello")
            self.assertIsNone(vec)
            ranked = idx.rank("hello")
            self.assertEqual(ranked, [])
        err.close()

    def test_gateway_missing_key_is_reported_as_fallback(self):
        idx = VectorIndex(self.db_path, provider="gateway", api_key="")

        self.assertIsNone(idx.get_query_vector("hello"))
        trace = idx.trace_snapshot()
        self.assertEqual(trace["provider_used"], "off")
        self.assertEqual(trace["fallback_reason"], "gateway_key_missing")

    def test_turn_receipt_records_provider_fallback_and_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_path = pathlib.Path(tmp) / "SKILL.md"
            rec = SkillRecord(
                "s1", "review-skill", "review-skill", "review pull requests", (),
                "r1", "SKILL.md", "sha1", 100, pathlib.Path(tmp), skill_path,
            )
            catalog = Catalog((rec,), (), "hash1")
            idx = VectorIndex(
                pathlib.Path(tmp) / "vectors.db", provider="gateway", api_key="",
            )
            engine = core.SkillProofEngine({"r1": pathlib.Path(tmp)}, vector_index=idx)
            with mock.patch("core.scan_catalog", return_value=catalog):
                engine.begin_turn(
                    turn_id="turn-1", session_id="session-1", task_id="task-1",
                    query="review a pull request", available_names={"review-skill"},
                )
            receipt = engine.receipt(turn_id="turn-1")

        self.assertEqual(receipt["provider"], "gateway")
        self.assertEqual(receipt["provider_used"], "off")
        self.assertEqual(receipt["fallback_reason"], "gateway_key_missing")
        self.assertGreaterEqual(receipt["embedding_latency_ms"], 0)
        import __init__ as plugin_module
        audit = plugin_module.SkillProofPlugin._audit_record(receipt, "session-1")
        self.assertEqual(audit["provider_used"], "off")
        self.assertEqual(audit["fallback_reason"], "gateway_key_missing")

    def test_explicit_skill_selection_does_not_attempt_embeddings(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_path = pathlib.Path(tmp) / "SKILL.md"
            rec = SkillRecord(
                "s1", "review-skill", "review-skill", "review pull requests", (),
                "r1", "SKILL.md", "sha1", 100, pathlib.Path(tmp), skill_path,
            )
            catalog = Catalog((rec,), (), "hash1")
            idx = VectorIndex(pathlib.Path(tmp) / "vectors.db", provider="gateway", api_key="")
            engine = core.SkillProofEngine({"r1": pathlib.Path(tmp)}, vector_index=idx)
            with mock.patch("core.scan_catalog", return_value=catalog):
                engine.begin_turn(
                    turn_id="turn-2", session_id="session-1", task_id="task-1",
                    query="use $review-skill", available_names={"review-skill"},
                )
            receipt = engine.receipt(turn_id="turn-2")

        self.assertEqual(receipt["fallback_reason"], "not_used")
        self.assertEqual(receipt["provider_used"], "off")

    def test_local_provider_uses_local_embedder(self):
        idx = VectorIndex(self.db_path, provider="local", model="Xenova/multilingual-e5-small")
        self.assertEqual(idx.provider, "local")
        mock_embedder = mock.MagicMock()
        mock_embedder.is_available.return_value = True
        mock_embedder.embed.return_value = [(0.1,) * 384]
        idx._local_embedder = mock_embedder

        vec = idx.get_query_vector("ทดสอบภาษาไทย")
        self.assertIsNotNone(vec)
        self.assertEqual(len(vec), 384)
        mock_embedder.embed.assert_called_once_with(["ทดสอบภาษาไทย"], is_query=True)

    def test_local_embedder_real_inference_if_installed(self):
        st = models.check_model_status("Xenova/multilingual-e5-small")
        if not st["installed"]:
            self.skipTest("Local model Xenova/multilingual-e5-small not installed")

        embedder = models.LocalEmbedder("Xenova/multilingual-e5-small")
        if not embedder.is_available():
            self.skipTest("Model files are installed but optional inference dependencies are unavailable")
        vecs = embedder.embed(["ช่วยทำ dashboard ให้หน่อย"], is_query=True)
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), 384)
        norm = sum(x * x for x in vecs[0]) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=3)


class TokenCutContextTests(unittest.TestCase):
    def test_token_cut_prompt_context(self):
        rec = SkillRecord("s1", "ui-ux-pro-max", "ui-ux-pro-max", "High taste UI/UX", (), "r1", "p1", "sha1", 100, pathlib.Path("."), pathlib.Path("."))
        catalog = Catalog((rec,), (), "hash1")

        import __init__ as plugin_module
        ctx = mock.MagicMock()
        ctx.get_config.side_effect = lambda k, d=None: d
        ctx.dispatch_tool.return_value = json.dumps({"success": True, "skills": [{"name": "ui-ux-pro-max"}]})
        plugin = plugin_module.SkillProofPlugin(ctx)
        plugin.engine._roots = {"r1": pathlib.Path(".")}

        with mock.patch("core.scan_catalog", return_value=catalog):
            # Selected turn
            res = plugin.on_pre_llm_call(
                session_id="s1", task_id="tk1", turn_id="t1", user_message="ui-ux-pro-max",
            )
            self.assertEqual(res, {"context": 'Use skill_view for "ui-ux-pro-max" first.'})

            # No match turn
            res_no = plugin.on_pre_llm_call(
                session_id="s1", task_id="tk1", turn_id="t2", user_message="something completely unrelated xyz12345",
            )
            self.assertIsNone(res_no)


class CliVectorWiringTests(unittest.TestCase):
    def test_cli_select_and_eval_use_configured_vector_index_when_active(self):
        class FakeIndex:
            _memory_vectors = []

            def __init__(self):
                self.synced = []

            def sync_catalog(self, skills):
                self.synced = list(skills)

            def rank(self, query, top_k=20):
                return []

            def trace_snapshot(self):
                return {}

            def reset_trace(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill_dir = root / "python-tdd"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_bytes(
                b"---\nname: python-tdd\ndescription: Write Python tests before implementation\n---\nBody.\n"
            )
            index = FakeIndex()
            with mock.patch.object(core, "vector_index_from_env", return_value=index) as provider:
                with mock.patch.object(core, "select_skill", wraps=core.select_skill) as select:
                    catalog, _, _ = cli._resolve_selection({"local": root}, "write tests first", 0.28, 0.05, 5)

            provider.assert_called_once_with()
            self.assertEqual([s.name for s in index.synced], ["python-tdd"])
            self.assertIs(select.call_args.kwargs["vector_index"], index)
            self.assertEqual(catalog.skills[0].name, "python-tdd")

            with mock.patch.object(core, "vector_index_from_env", return_value=index) as provider2:
                with mock.patch.object(core, "select_skill", wraps=core.select_skill) as select2:
                    _, _, explicit_selection = cli._resolve_selection(
                        {"local": root}, "Use $python-tdd", 0.28, 0.05, 5
                    )
            # Unified pipeline (plan Part 1.2): the engine always carries the
            # configured index; explicit turns skip the embedding sync and the
            # vector ranking path instead of skipping construction.
            provider2.assert_called_once_with()
            self.assertEqual(explicit_selection.status, "selected")
            self.assertEqual(explicit_selection.reason, "explicit_skill")
            self.assertIs(select2.call_args.kwargs["vector_index"], index)
            self.assertEqual([s.name for s in index.synced], ["python-tdd"])


if __name__ == "__main__":
    unittest.main()
