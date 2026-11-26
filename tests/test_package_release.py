"""Exercise package assembly with prebuilt bytes in place of the TouchDesigner build."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import package_release as package  # noqa: E402
import verify_release as verify  # noqa: E402


class PackageTests(unittest.TestCase):
    def test_tagged_package_and_zip_cover_both_binary_names(self):
        self.check_package("2.3.4", "v2.3.4")

    def test_development_package_retains_the_unversioned_filename(self):
        self.check_package("2.3.4.dev1", "")

    def check_package(self, version, tag):
        commit = "ab" * 20
        tox = b"example prebuilt component"
        guide = b"# Installation guide\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / ".tmp" / f"release-{commit[:10]}"

            def git(*args):
                if args[0] == "worktree":
                    worktree.mkdir(parents=True)
                return "tag" if args[0] == "cat-file" else commit

            def run(args, *, cwd, **kwargs):
                if args[0] != sys.executable:
                    self.assertIn("remove", args)
                    return
                self.assertEqual(Path(cwd), worktree)
                build = worktree / "build"
                build.mkdir()
                (build / "TDflowMCP.tox").write_bytes(tox)
                meta = {"version": version, "source_commit": commit, "source_dirty": False,
                        "td_build_used": package.MIN_TD, "sha256": verify.sha256(tox), "size": len(tox)}
                (build / "TDflowMCP.json").write_text(json.dumps(meta), encoding="utf-8")
                install = worktree / "docs" / "operations" / "install.md"
                install.parent.mkdir(parents=True)
                install.write_bytes(guide)
                return mock.Mock(returncode=0)

            argv = ["package_release.py"] + (["--release-tag", tag] if tag else [])
            with mock.patch.object(package, "ROOT", directory), mock.patch.object(package, "git", git), \
                    mock.patch.object(package.subprocess, "run", run), mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                package.main()

            report = json.loads(output.getvalue())
            out = root / report["package"]
            files = {file.name: file.read_bytes() for file in out.iterdir()}
            binary_names = ["TDflowMCP.tox"]
            if tag:
                binary_names.append(f"TDflowMCP-{tag}.tox")
                self.assertEqual(verify.check(files, tag, expect_tox=verify.sha256(tox),
                                              expect_sums=report["sums_sha256"], expect_commit=commit,
                                              require_versioned=True), [])
            self.assertCountEqual(files, [*binary_names, "INSTALL.md", "TDflowMCP.json", "SHA256SUMS"])
            self.assertEqual(files["INSTALL.md"], guide)
            for name in binary_names:
                self.assertEqual(files[name], tox)
            self.assertEqual(verify.parse_sums(files["SHA256SUMS"].decode("ascii")),
                             {name: verify.sha256(data) for name, data in files.items() if name != "SHA256SUMS"})
            with zipfile.ZipFile(root / report["zip"]) as archive:
                self.assertEqual({path: archive.read(path) for path in archive.namelist()},
                                 {f"{out.name}/{name}": data for name, data in files.items()})
