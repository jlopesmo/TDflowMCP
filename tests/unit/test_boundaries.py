"""Invariant P-7: src/tdflowmcp never imports `td` (TD access only via src/td_adapter/)."""
import ast
import unittest

from tests.unit import SRC


class NoTdImportTests(unittest.TestCase):
    def test_core_has_no_td_import(self):
        offenders = []
        for py in sorted((SRC / "tdflowmcp").rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for n in names:
                    if n == "td" or n.startswith("td."):
                        offenders.append(f"{py.relative_to(SRC)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_core_never_starts_processes_or_installs(self):
        banned = ("subprocess", "pip", "ensurepip", "winreg")
        offenders = []
        for py in sorted((SRC / "tdflowmcp").rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                offenders += [f"{py.name}:{m}" for m in mods if m.split(".")[0] in banned]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
