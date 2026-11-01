import pathlib
import tempfile
import unittest

from skill_proof import vault


class TestVaultPathHardening(unittest.TestCase):
    def test_safe_skill_name_rejects_traversal(self):
        bad_names = [
            "../evil",
            "../../etc/passwd",
            "..",
            ".",
            "a/b",
            "a\\b",
            "a b",
            "a$b",
            "a;rm -rf",
        ]
        for n in bad_names:
            self.assertIsNone(vault._safe_skill_name(n), f"expected rejection for {n!r}")

    def test_safe_skill_name_accepts_valid(self):
        good_names = [
            "ask-matt",
            "code-review",
            "test-driven-development",
            "python-debugpy",
            "x",
            "a.b",
        ]
        for n in good_names:
            self.assertEqual(vault._safe_skill_name(n), n, f"expected acceptance for {n!r}")

    def test_pin_rejects_unsafe_skill_name(self):
        res = vault.pin("../evil", pathlib.Path(tempfile.gettempdir()))
        self.assertFalse(res["success"])
        self.assertIn("invalid skill name", res["error"])

    def test_unpin_rejects_unsafe_skill_name(self):
        res = vault.unpin("../evil", pathlib.Path(tempfile.gettempdir()))
        self.assertFalse(res["success"])
        self.assertIn("invalid skill name", res["error"])

    def test_export_patch_rejects_unsafe_name(self):
        with self.assertRaises(ValueError):
            vault.export_patch("../../evil", "patch", vault_dir=pathlib.Path(tempfile.gettempdir()))

    def test_apply_patch_returns_false_for_unsafe_name(self):
        self.assertFalse(vault.apply_patch("../../evil", vault_dir=pathlib.Path(tempfile.gettempdir())))


if __name__ == "__main__":
    unittest.main()
