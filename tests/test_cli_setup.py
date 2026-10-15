import pathlib
import tempfile
import unittest
from unittest import mock

import cli
import models


class SetupConfigTests(unittest.TestCase):
    def test_section_lookup_requires_exact_yaml_key(self):
        lines = [
            "plugins:",
            "  entries:legacy",  # a scalar key, not the entries mapping
            "  entries-extra:",
            "  entries:",
            "    skill-proof:",
        ]

        section = cli._find_section(lines, 0, len(lines), 0, "entries")

        self.assertEqual(section, (3, 5, 2))

    def test_updates_only_skill_proof_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "config.yaml"
            path.write_text(
                "plugins:\n"
                "  enabled: [skill-proof, other]\n"
                "  entries:\n"
                "    other:\n"
                "      settings:\n"
                "        keep: true\n"
                "    skill-proof:\n"
                "      allow_tool_override: false\n"
                "      settings:\n"
                "        skill_roots:\n"
                "          - C:/skills\n"
                "        embedding_provider: off\n"
                "unrelated:\n"
                "  keep: value\n",
                encoding="utf-8",
            )
            settings = {
                "embedding_provider": "gateway",
                "embedding_model": "Xenova/multilingual-e5-small",
                "gateway_url": "https://embeddings.example/v1",
                "gateway_key_env": "MY_KEY_ENV",
            }

            cli._write_plugin_settings(path, settings)
            first = path.read_text(encoding="utf-8")
            cli._write_plugin_settings(path, settings)
            second = path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn("enabled: [skill-proof, other]", second)
        self.assertIn("allow_tool_override: false", second)
        self.assertIn("keep: value", second)
        self.assertEqual(second.count("embedding_provider:"), 1)
        self.assertIn('gateway_url: "https://embeddings.example/v1"', second)

    def test_write_plugin_settings_replaces_empty_flow_mapping_and_multiline_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = pathlib.Path(tmp) / "empty.yaml"
            empty_path.write_text(
                "plugins:\n"
                "  entries:\n"
                "    skill-proof:\n"
                "      settings: {}\n"
                "unrelated:\n"
                "  keep: value\n",
                encoding="utf-8",
            )
            cli._write_plugin_settings(empty_path, {"embedding_provider": "off"})
            empty_first = empty_path.read_text(encoding="utf-8")
            cli._write_plugin_settings(empty_path, {"embedding_provider": "off"})
            empty_second = empty_path.read_text(encoding="utf-8")

            block_path = pathlib.Path(tmp) / "block.yaml"
            block_path.write_text(
                "plugins:\n"
                "  entries:\n"
                "    skill-proof:\n"
                "      settings:\n"
                "        skill_roots:\n"
                "          - C:/old-skills\n"
                "        embedding_provider: off\n",
                encoding="utf-8",
            )
            cli._write_plugin_settings(
                block_path, {"skill_roots": ["C:/new-skills"], "embedding_provider": "local"}
            )
            block_out = block_path.read_text(encoding="utf-8")

        self.assertNotIn("settings: {}", empty_first)
        self.assertIn('embedding_provider: "off"', empty_first)
        self.assertIn("keep: value", empty_first)
        self.assertEqual(empty_second, empty_first)
        self.assertNotIn("C:/old-skills", block_out)
        self.assertEqual(block_out.count("skill_roots:"), 1)
        self.assertIn('skill_roots: ["C:/new-skills"]', block_out)
        self.assertIn('embedding_provider: "local"', block_out)

    def test_setup_off_writes_default_provider_without_creating_vector_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli.core, "default_hermes_home", return_value=pathlib.Path(tmp)):
                result = cli.main(["setup", "--provider", "off"])

            config = (pathlib.Path(tmp) / "config.yaml").read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertIn('embedding_provider: "off"', config)
        self.assertFalse((pathlib.Path(tmp) / "plugin-data" / "skill-proof" / "embeddings.db").exists())

    def test_setup_local_asks_before_prefetching_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli.core, "default_hermes_home", return_value=pathlib.Path(tmp)), \
                 mock.patch.object(cli, "_missing_local_runtime_dependencies", return_value=[]), \
                 mock.patch("builtins.input", side_effect=["local", "", "y"]), \
                 mock.patch.object(models, "download_model", return_value=True) as download:
                result = cli.main(["setup"])

            config = (pathlib.Path(tmp) / "config.yaml").read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        download.assert_called_once_with("Xenova/multilingual-e5-small")
        self.assertIn('embedding_provider: "local"', config)

    def test_setup_local_refuses_to_write_config_when_runtime_dependencies_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli.core, "default_hermes_home", return_value=pathlib.Path(tmp)), \
                 mock.patch.object(cli, "_missing_local_runtime_dependencies", return_value=["tokenizers"]):
                result = cli.main(["setup", "--provider", "local", "--model", "e5-small"])

            self.assertFalse((pathlib.Path(tmp) / "config.yaml").exists())

        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
