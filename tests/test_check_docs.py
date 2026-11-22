"""Unit tests for tools/check_docs.py (stdlib unittest)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "tools" / "check_docs.py")
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)

# Built by concatenation so this file does not itself trip the privacy scan.
WIN_USER_PATH = "C:" + "\\" + "Users" + "\\" + "someone" + "\\x"
POSIX_USER_PATH = "/" + "home" + "/someone/x"
EMAIL = "a" + "@" + "b.example.com"


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class SlugTests(unittest.TestCase):
    def test_github_style_slugs(self):
        self.assertEqual(check_docs.slugify("5. Key conclusions [I]"), "5-key-conclusions-i")
        self.assertEqual(check_docs.slugify("ADR-0001 — Product scope"), "adr-0001--product-scope")
        self.assertEqual(check_docs.slugify("Use `code` here"), "use-code-here")

    def test_underscores_are_kept(self):
        self.assertEqual(check_docs.slugify("The `tdflow_get_x` tool"), "the-tdflow_get_x-tool")

    def test_trailing_hash_without_space_is_content(self):
        m = check_docs.HEADING.match("## Learn C#")
        self.assertEqual(m.group(2), "Learn C#")
        m = check_docs.HEADING.match("## Closed heading ##")
        self.assertEqual(m.group(2), "Closed heading")


class CheckTests(unittest.TestCase):
    def _minimal_repo(self, root: Path) -> None:
        for rel in check_docs.REQUIRED_FILES:
            _write(root, rel, "# Title\n")
        _write(root, "docs/decisions/README.md", "# ADRs\n| [0001](0001-x.md) | X | Proposed |\n")
        _write(root, "docs/decisions/0001-x.md", "# ADR\n\n**Status:** Proposed\n")

    def _errors(self, root: Path) -> str:
        return "\n".join(check_docs.check(root))

    def test_minimal_repo_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            self.assertEqual(check_docs.check(root), [])

    def test_detects_missing_file_broken_link_and_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            (root / "CLAUDE.md").unlink()
            _write(root, "README.md", "# T\n[a](nope.md) [b](AGENTS.md#missing) [ok](AGENTS.md#title)\n")
            errs = self._errors(root)
            self.assertIn("missing required file: CLAUDE.md", errs)
            self.assertIn("broken link -> nope.md", errs)
            self.assertIn("missing anchor -> AGENTS.md#missing", errs)
            self.assertNotIn("AGENTS.md#title", errs)

    def test_same_file_and_duplicate_heading_anchors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "README.md", "# T\n## Dup\n## Dup\n[a](#dup) [b](#dup-1) [c](#dup-2)\n")
            errs = self._errors(root)
            self.assertNotIn("-> #dup\n", errs + "\n")
            self.assertNotIn("#dup-1", errs)
            self.assertIn("missing anchor -> #dup-2", errs)

    def test_images_and_outside_root_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            root.mkdir()
            self._minimal_repo(root)
            _write(Path(d), "outside.md", "# O\n")
            _write(root, "README.md", "# T\n![img](missing.png) [o](../outside.md)\n")
            errs = self._errors(root)
            self.assertIn("broken link -> missing.png", errs)
            self.assertIn("link leaves repository -> ../outside.md", errs)

    def test_links_in_code_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "README.md", "# T\n`[x](nope.md)`\n```\n[y](nope2.md)\n```\n")
            self.assertEqual(check_docs.check(root), [])

    def test_adr_status_and_index(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "docs/decisions/0002-y.md", "# ADR\n\n**Status:** Unaccepted maybe\n")
            _write(root, "docs/decisions/0003-z.md", "# ADR\nno status\n")
            errs = self._errors(root)
            self.assertIn("0002-y.md: status must contain", errs)
            self.assertIn("0003-z.md: missing '**Status:**' line", errs)
            self.assertIn("0002-y.md: not listed", errs)

    def test_index_status_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "docs/decisions/README.md", "# ADRs\n| [0001](0001-x.md) | X | Accepted |\n")
            self.assertIn("index status ['Accepted'] != ADR status ['Proposed']", self._errors(root))

    def test_conditional_acceptance_is_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "docs/decisions/0001-x.md", "# ADR\n\n**Status:** Conditionally Accepted (owner)\n")
            _write(root, "docs/decisions/README.md", "# ADRs\n| [0001](0001-x.md) | X | Accepted |\n")
            self.assertIn("index status ['Accepted'] != ADR status ['Conditionally Accepted']", self._errors(root))
            _write(root, "docs/decisions/README.md", "# ADRs\n| [0001](0001-x.md) | X | Conditionally Accepted |\n")
            self.assertEqual(check_docs.check(root), [])

    def test_required_files_include_new_architecture_docs(self):
        for rel in ("docs/architecture/setup-assistant.md", "docs/architecture/agent-integration.md",
                    "docs/architecture/continuity-and-memory.md", "docs/architecture/security-model.md"):
            self.assertIn(rel, check_docs.REQUIRED_FILES)

    def test_privacy_patterns_in_md_and_py(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "README.md", f"# T\n{WIN_USER_PATH} and {EMAIL}\n")
            _write(root, "tools/x.py", f"P = '{POSIX_USER_PATH}'\n")
            errs = self._errors(root)
            self.assertIn("README.md:2: user-profile path committed", errs)
            self.assertIn("README.md:2: e-mail address committed", errs)
            self.assertIn("tools/x.py:1: user-profile path committed", errs)

    def test_url_paths_are_not_user_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._minimal_repo(root)
            _write(root, "README.md", "# T\nhttps://example.org/Users/guide and ~/home/x\n")
            self.assertEqual(check_docs.check(root), [])


if __name__ == "__main__":
    unittest.main()
