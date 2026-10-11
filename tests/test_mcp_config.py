import os
import pathlib
import tempfile
import unittest
from unittest import mock

import core
import mcp_server


class MCPVectorConfigurationTests(unittest.TestCase):
    def test_mcp_vector_index_uses_explicit_provider_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "SKILL_PROOF_EMBEDDING_PROVIDER": "local",
                "SKILL_PROOF_EMBEDDING_MODEL": "e5-small",
                "SKILL_PROOF_GATEWAY_URL": "https://embeddings.example/v1",
                "SKILL_PROOF_GATEWAY_KEY_ENV": "MY_EMBEDDINGS_KEY",
                "SKILL_PROOF_MODELS_DIR": str(tmp),
                "SKILL_PROOF_VECTOR_DB": os.path.join(tmp, "vectors.db"),
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                core, "VectorIndex", return_value=object()
            ) as factory:
                index = mcp_server._vector_index()

        self.assertIsNotNone(index)
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["provider"], "local")
        self.assertEqual(kwargs["model"], "e5-small")
        self.assertEqual(kwargs["gateway_url"], "https://embeddings.example/v1")
        self.assertEqual(kwargs["gateway_key_env"], "MY_EMBEDDINGS_KEY")
        self.assertEqual(kwargs["models_dir"], tmp)
        self.assertTrue(str(kwargs["db_path"]).endswith("vectors.db"))

    def test_mcp_vector_index_defaults_to_off(self):
        keys = (
            "SKILL_PROOF_EMBEDDING_PROVIDER",
            "SKILL_PROOF_EMBEDDING_MODEL",
            "SKILL_PROOF_GATEWAY_URL",
            "SKILL_PROOF_GATEWAY_KEY_ENV",
            "SKILL_PROOF_MODELS_DIR",
            "SKILL_PROOF_VECTOR_DB",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {**{key: "" for key in keys}, "HERMES_HOME": tmp}, clear=False):
                self.assertIsNone(mcp_server._vector_index())
            self.assertFalse((pathlib.Path(tmp) / "plugin-data" / "skill-proof" / "embeddings.db").exists())

    def test_unknown_provider_setting_falls_back_to_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = pathlib.Path(tmp) / "vectors.db"
            db_path.touch()
            settings = {
                "SKILL_PROOF_EMBEDDING_PROVIDER": "typo",
                "SKILL_PROOF_VECTOR_DB": str(db_path),
            }
            with mock.patch.dict(os.environ, settings, clear=False):
                index = mcp_server._vector_index()

        self.assertEqual(index.provider, "off")


if __name__ == "__main__":
    unittest.main()
