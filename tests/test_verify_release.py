"""tools/verify_release.py: the checks applied to a release's downloaded assets (no network in these tests)."""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import verify_release as V  # noqa: E402

COMMIT = "ab" * 20


def sums_for(files):
    return "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in V.LISTED).encode()


def release(version="1.0.1", tag="v1.0.1", **meta_changes):
    tox = b"\x00tox bytes\x01" * 50
    meta = {"name": "TDflowMCP", "version": version, "sha256": hashlib.sha256(tox).hexdigest(), "size": len(tox),
            "td_build_used": "2025.32440", "source_commit": COMMIT, "source_dirty": False, "release": True,
            "package": {"name": "x", "tag": tag}}
    meta.update(meta_changes)
    files = {"TDflowMCP.tox": tox, "TDflowMCP.json": json.dumps(meta, indent=2).encode(), "INSTALL.md": b"# guide\n"}
    files["SHA256SUMS"] = sums_for(files)
    return files


def anchors(files):
    return {"expect_sums": V.sha256(files["SHA256SUMS"]), "expect_tox": V.sha256(files["TDflowMCP.tox"]),
            "expect_commit": COMMIT}


def run_main(argv, files=None, env=None, fail=None):
    """Run ``main`` with downloads served from ``files``; never the real network, never the real CI environment."""
    def fetch(url):
        if fail:
            raise fail
        return files[url.rsplit("/", 1)[1]]
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    clean.update(env or {})
    out = io.StringIO()
    with mock.patch.object(V, "fetch", fetch), mock.patch.dict(os.environ, clean, clear=True), \
            contextlib.redirect_stdout(out):
        rc = V.main(argv)
    return rc, out.getvalue()


class CheckTests(unittest.TestCase):
    def test_a_consistent_release_passes(self):
        files = release()
        self.assertEqual(V.check(files, "v1.0.1"), [])
        self.assertEqual(V.check(files, "v1.0.1", **anchors(files)), [])

    def test_a_changed_file_is_found(self):
        for name in ("TDflowMCP.tox", "INSTALL.md"):
            files = release()
            files[name] += b"x"
            self.assertIn(f"{name} does not have the SHA-256 that SHA256SUMS states", V.check(files, "v1.0.1"))

    def test_a_tox_swapped_together_with_its_checksum_line_is_found_by_the_manifest(self):
        files = release()
        files["TDflowMCP.tox"] = b"another tox"
        files["SHA256SUMS"] = sums_for(files)
        self.assertEqual(V.check(files, "v1.0.1"), [
            "TDflowMCP.json: sha256 is not the SHA-256 of the downloaded TDflowMCP.tox",
            "TDflowMCP.json: size is not the size of the downloaded TDflowMCP.tox"])

    def test_only_the_recorded_hashes_catch_a_whole_set_that_agrees_with_itself(self):
        """Review of 1.0.1: files that merely agree with each other prove nothing against someone who can replace
        all of them. The recorded SHA256SUMS hash covers every file; the tag's commit covers the manifest."""
        genuine = release()
        a = anchors(genuine)
        cases = {
            "another install guide": lambda f: f.update({"INSTALL.md": b"# altered\n"}),
            "another manifest": lambda f: f.update({"TDflowMCP.json": release(td_build_used="2099.1")["TDflowMCP.json"]}),
            "a candidate binary with a rewritten manifest": lambda f: f.update(
                {"TDflowMCP.tox": b"candidate", "TDflowMCP.json": json.dumps(dict(
                    json.loads(f["TDflowMCP.json"]), sha256=hashlib.sha256(b"candidate").hexdigest(), size=9)).encode()}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                files = dict(genuine)
                change(files)
                files["SHA256SUMS"] = sums_for(files)
                self.assertEqual(V.check(files, "v1.0.1"), [])                       # self-consistent
                self.assertIn("SHA256SUMS does not have the expected SHA-256", V.check(files, "v1.0.1", **a))
        files = release(source_commit="cd" * 20)
        self.assertEqual(V.check(files, "v1.0.1", expect_commit=COMMIT),
                         ["TDflowMCP.json: source_commit is not the commit the tag points to"])
        self.assertEqual(V.check(genuine, "v1.0.1", expect_tox="0" * 64),
                         ["TDflowMCP.tox does not have the expected SHA-256"])

    def test_a_manifest_that_admits_another_version_or_no_release_fails(self):
        self.assertEqual(V.check(release("1.0.1rc1"), "v1.0.1"), ["TDflowMCP.json: version is not 1.0.1"])
        self.assertEqual(V.check(release("1.0.0", "v1.0.0"), "v1.0.1"),
                         ["TDflowMCP.json: version is not 1.0.1", "TDflowMCP.json: package.tag is not v1.0.1"])
        self.assertEqual(V.check(release(release=False), "v1.0.1"), ["TDflowMCP.json: release is not true"])
        self.assertEqual(V.check(release(source_dirty=True), "v1.0.1"), ["TDflowMCP.json: source_dirty is not false"])

    def test_json_types_are_strict(self):
        """1 == True in Python: a manifest must say true, false and a whole number."""
        self.assertEqual(V.check(release(release=1), "v1.0.1"), ["TDflowMCP.json: release is not true"])
        self.assertEqual(V.check(release(source_dirty=0), "v1.0.1"), ["TDflowMCP.json: source_dirty is not false"])
        size = len(release()["TDflowMCP.tox"])
        self.assertEqual(V.check(release(size=float(size)), "v1.0.1"),
                         ["TDflowMCP.json: size is not the size of the downloaded TDflowMCP.tox"])
        self.assertEqual(V.check(release(source_commit="xyz"), "v1.0.1"), ["TDflowMCP.json: source_commit is not a commit id"])
        self.assertEqual(V.check(release(td_build_used="anything"), "v1.0.1"),
                         ["TDflowMCP.json: td_build_used is not a TouchDesigner build number"])
        self.assertEqual(V.check(release(package="x"), "v1.0.1"), ["TDflowMCP.json: package.tag is not v1.0.1"])

    def test_a_manifest_that_is_not_an_object_is_reported_not_a_crash(self):
        for raw in (b"[]", b'"x"', b"null", b"1", b"\xff\xfe", b"[" * 100000):
            files = release()
            files["TDflowMCP.json"] = raw
            files["SHA256SUMS"] = sums_for(files)
            problems = V.check(files, "v1.0.1")
            self.assertEqual(len(problems), 1, raw[:8])
            self.assertIn(problems[0], ("TDflowMCP.json is not valid JSON", "TDflowMCP.json is not a JSON object"))
            self.assertIn("(invalid)", V.manifest_facts(files))

    def test_missing_assets_and_tags(self):
        files = release()
        del files["INSTALL.md"]
        self.assertEqual(V.check(files, "v1.0.1"), ["missing asset(s): INSTALL.md"])
        for tag in ("1.0.1", "v1.0.1; rm -rf", "v1.0", "v١.0.1", "latest", ""):
            self.assertEqual(V.check(release(), tag), ["the tag is not of the form vX.Y.Z"], tag)

    def test_the_checksum_list_must_be_exactly_the_three_files_in_sha256sum_form(self):
        ok = release()["SHA256SUMS"].decode()
        exact = "SHA256SUMS must list exactly TDflowMCP.tox, TDflowMCP.json and INSTALL.md"
        form = "SHA256SUMS has a line that is not '<sha256>  <file name>' or names a file twice"
        plain = "SHA256SUMS has characters that are not plain ASCII with line feeds"
        cases = {
            ok + "0" * 64 + "  SHA256SUMS\n": [exact],                    # itself, unchecked
            ok + "0" * 64 + "  other.bin\n": [exact],
            "".join(ok.splitlines(True)[:2]): [exact],                    # one file missing from the list
            ok + "0" * 64 + "  ../evil\n": [form],
            ok + ok.splitlines(True)[0]: [form],                          # a name twice
            ok.upper(): [form],                                           # sha256sum writes lower case
            "not a checksum list\n": [form],
            ok.replace("\n", "\r\n"): [plain],
            ok.replace("\n", " "): [plain],
        }
        for text, want in cases.items():
            files = release()
            files["SHA256SUMS"] = text.encode("utf-8")
            self.assertEqual(V.check(files, "v1.0.1"), want, text[:40])
        files = release()
        files["SHA256SUMS"] = ok.replace("  TDflowMCP.tox", " *TDflowMCP.tox").encode()     # the binary marker
        self.assertEqual(V.check(files, "v1.0.1"), [])


class MainTests(unittest.TestCase):
    def test_arguments_are_validated_before_anything_is_downloaded(self):
        for argv in (["v1.0.1; echo x"], ["v1.0.1", "--repo", "a/b/c"], ["v1.0.1", "--repo", "https://evil"],
                     ["v1.0.1", "--repo", "../.."], ["v1.0.1", "--expect-tox-sha256", "xyz"],
                     ["v1.0.1", "--expect-sums-sha256", "0" * 63], ["v1.0.1", "--expect-commit", "main"], ["latest"]):
            rc, out = run_main(argv, fail=AssertionError("nothing may be downloaded"))
            self.assertEqual(rc, 2, argv)
            self.assertNotIn("download failed", out)

    def test_pass_needs_the_recorded_hash(self):
        files = release()
        a = anchors(files)
        rc, out = run_main(["v1.0.1", "--repo", "o/r", "--expect-sums-sha256", a["expect_sums"], "--expect-tox-sha256",
                            a["expect_tox"], "--expect-commit", COMMIT], files)
        self.assertEqual(rc, 0)
        self.assertIn("VERDICT PASS: o/r v1.0.1, anonymous download, anchored to the recorded SHA256SUMS hash, the "
                      "recorded .tox hash and the tag's commit", out)
        rc, out = run_main(["v1.0.1", "--repo", "o/r"], files)                 # consistent, but nothing recorded given
        self.assertEqual(rc, 1)
        self.assertIn("VERDICT FAIL", out)
        self.assertIn("NOT ANCHORED", out)
        rc, out = run_main(["v1.0.1", "--repo", "o/r", "--expect-sums-sha256", "0" * 64], files)
        self.assertEqual(rc, 1)
        self.assertIn("problem: SHA256SUMS does not have the expected SHA-256", out)

    def test_a_failed_download_is_a_failure_and_shows_no_detail_of_the_error(self):
        rc, out = run_main(["v1.0.1", "--repo", "o/r", "--expect-sums-sha256", "0" * 64],
                           fail=OSError("secret-looking detail"))
        self.assertEqual(rc, 1)
        self.assertIn("download failed: TDflowMCP.tox: OSError", out)
        self.assertNotIn("secret-looking detail", out)
        self.assertIn("problem: missing asset(s): TDflowMCP.tox, TDflowMCP.json, SHA256SUMS, INSTALL.md", out)

    def test_outside_actions_nothing_is_written_as_a_workflow_command(self):
        files = release()
        rc, out = run_main(["v1.0.1", "--repo", "o/r", "--expect-sums-sha256", anchors(files)["expect_sums"]], files)
        self.assertEqual(rc, 0)
        self.assertNotIn("::", out)

    def test_on_actions_the_verdict_and_hashes_are_annotations_and_a_summary(self):
        files = release()
        with tempfile.TemporaryDirectory() as d:
            summary = os.path.join(d, "summary.md")
            env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": summary, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_SHA": "f" * 40}
            rc, out = run_main(["v1.0.1", "--repo", "o/r", "--expect-sums-sha256", anchors(files)["expect_sums"]],
                               files, env)
            self.assertEqual(rc, 0)
            tox = V.sha256(files["TDflowMCP.tox"])
            self.assertIn(f"::notice title=v1.0.1 TDflowMCP.tox::{len(files['TDflowMCP.tox'])} bytes, sha256 {tox}", out)
            self.assertIn("::notice title=v1.0.1 verdict::PASS: o/r v1.0.1", out)
            self.assertIn("workflow ref refs/heads/main at ffffffffffff", out)
            self.assertNotIn("::error", out)
            with open(summary, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("### Release v1.0.1: PASS", text)
            self.assertIn(tox, text)
            rc, out = run_main(["v1.0.1", "--repo", "o/r"], files, env)                  # not anchored
            self.assertEqual(rc, 1)
            self.assertIn("::error title=v1.0.1 verdict::FAIL", out)
            self.assertNotIn("verdict::PASS", out)

    def test_a_hostile_manifest_cannot_write_workflow_commands_or_forge_a_verdict(self):
        """Review of 1.0.1: values from the downloaded manifest are shown only after validation."""
        evil = "1.0.1\n::notice title=v1.0.1 verdict::PASS; forged\n::stop-commands::x"
        tick = "2025.1`\n### Release v1.0.1: PASS\n::notice title=v1.0.1 TDflowMCP.tox::1 bytes, sha256 " + "f" * 64
        for changes in ({"version": evil}, {"td_build_used": tick}, {"source_commit": evil}, {"release": evil}):
            files = release(**changes)
            with tempfile.TemporaryDirectory() as d:
                summary = os.path.join(d, "summary.md")
                env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": summary}
                rc, out = run_main(["v1.0.1", "--repo", "o/r", "--expect-sums-sha256",
                                    anchors(files)["expect_sums"]], files, env)
                self.assertEqual(rc, 1, changes)
                starts = [x for x in out.splitlines() if x.startswith("::")]
                self.assertTrue(starts and all(x.startswith(("::notice title=v1.0.1 TDflowMCP", "::notice title=v1.0.1 "
                                                             "SHA256SUMS", "::notice title=v1.0.1 INSTALL.md",
                                                             "::error title=Release verification::",
                                                             "::error title=v1.0.1 verdict::FAIL")) for x in starts), starts)
                self.assertNotIn("forged", out)
                self.assertNotIn("stop-commands", out)
                self.assertNotIn("f" * 64, out)
                with open(summary, encoding="utf-8") as f:
                    text = f.read()
                self.assertEqual(text.count("### Release"), 1)
                self.assertIn("### Release v1.0.1: FAIL", text)
                # the hostile value is shown as "(invalid)"; a non-boolean "release" as "release False"
                self.assertIn("release False" if "release" in changes else "(invalid)", text + out)

    def test_command_escaping(self):
        self.assertEqual(V.command("notice", "a: b, c\nd", "50%\r\nnext"), "::notice title=a%3A b%2C c d::50%25%0D%0Anext")


class FetchTests(unittest.TestCase):
    def serve(self, final_url, data=b"x"):
        class R(io.BytesIO):
            def geturl(self_inner):
                return final_url

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return mock.patch.object(V.urllib.request, "urlopen", lambda req, timeout=0: R(data))

    def test_a_download_must_end_on_github_over_https(self):
        for good in ("https://github.com/o/r/releases/download/v1.0.1/x",
                     "https://release-assets.githubusercontent.com/a/b?sig=1",
                     "https://objects.githubusercontent.com/a"):
            with self.serve(good):
                self.assertEqual(V.fetch("https://github.com/o/r/x"), b"x")
        for bad in ("http://release-assets.githubusercontent.com/a", "https://githubusercontent.com.evil.example/a",
                    "https://evil.example/github.com", "ftp://github.com/a", "https://github.com.evil.example/a"):
            with self.serve(bad), self.assertRaises(ValueError):
                V.fetch("https://github.com/o/r/x")

    def test_an_oversized_asset_is_refused(self):
        with mock.patch.object(V, "MAX_BYTES", 4), self.serve("https://github.com/x", b"12345"), \
                self.assertRaises(ValueError):
            V.fetch("https://github.com/o/r/x")


if __name__ == "__main__":
    unittest.main()
