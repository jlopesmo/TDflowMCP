"""Unit tests for tools/scan_secrets.py (fixtures are assembled at runtime so the repo itself stays clean)."""
import importlib.util
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
spec = importlib.util.spec_from_file_location("scan_secrets", os.path.join(ROOT, "tools", "scan_secrets.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


class ScanTests(unittest.TestCase):
    def kinds(self, text):
        return {k for _, k in S.scan_text(text)}

    def test_detects(self):
        tok = "tdf_" + "A1b2C3d4E5f6" + "." + "x" * 43
        self.assertIn("tdflowmcp_token", self.kinds(f"TOKEN={tok}"))
        self.assertIn("private_key", self.kinds("-----BEGIN " + "PRIVATE KEY-----"))
        self.assertIn("aws_access_key", self.kinds("AKIA" + "ABCDEFGHIJKLMNOP"))
        self.assertIn("github_token", self.kinds("ghp_" + "a" * 36))
        self.assertIn("windows_profile_path", self.kinds("C:" + "\\" + "Users" + "\\" + "alice" + "\\x"))
        self.assertIn("posix_home_path", self.kinds("/home/" + "alice/.ssh"))

    def test_ignores_placeholders(self):
        self.assertEqual(self.kinds("tdf_<token_id>.<secret>  ${TDFLOW_TOKEN}  %LOCALAPPDATA%\\TDflowMCP"), set())
        self.assertEqual(self.kinds("tdf_ABCDEFGHIJKL.short"), set())
        self.assertEqual(self.kinds("/" + "home" + "/runner/work"), set())

    def test_repository_is_clean(self):
        self.assertEqual(S.scan(S.tracked_files()), [])


if __name__ == "__main__":
    unittest.main()
