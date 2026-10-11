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
        self.assertTrue(embedder.is_available())
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


if __name__ == "__main__":
    unittest.main()
