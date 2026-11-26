"""Download the public assets of a GitHub Release without credentials and check them.

    python tools/verify_release.py v1.0.1 --expect-sums-sha256 HEX [--expect-tox-sha256 HEX]
                                          [--expect-commit SHA] [--repo owner/name] [--require-versioned]

It is run by ``.github/workflows/verify-release.yml`` on GitHub-hosted runners, that is, from outside the network
the release was built on (whose gateway does not allow a trusted connection to GitHub's asset host). No token is
used or read: the downloads are anonymous HTTPS with certificate checking, and a download that ends anywhere but
on GitHub over HTTPS is refused.

What a PASS means:
- ``TDflowMCP.tox``, ``TDflowMCP.json``, ``SHA256SUMS`` and ``INSTALL.md`` were downloaded from the release's public
  URLs;
- ``SHA256SUMS`` lists the binary, build manifest and install guide, and each has exactly that SHA-256;
- if listed in ``SHA256SUMS``, the versioned binary is downloaded and checked for byte equality with the
  compatibility binary. ``--require-versioned`` also checks legacy aliases using ``SHA256SUMS-versioned``,
  which must name the alias with the original binary's anchored hash;
- ``TDflowMCP.json`` states the SHA-256 and size of the downloaded ``TDflowMCP.tox``, the version of the tag,
  ``"release": true`` and that tag, a clean source tree, a TouchDesigner build and a source commit;
- **anchored to what was recorded when the package was built:** ``SHA256SUMS`` itself has the SHA-256 given with
  ``--expect-sums-sha256``. Since it names the hash of every other file, that one value covers all four files.
  ``--expect-tox-sha256`` and ``--expect-commit`` (the commit the tag points to) are checked too when given.
Without ``--expect-sums-sha256`` the files are only shown to agree with each other, which someone who can replace
all of them could also arrange; the verdict then says ``NOT ANCHORED`` and the exit status is 1.

Output: one line per file with its size and SHA-256; on GitHub Actions the same as notice annotations, which are
public (job logs are not), and a job summary. Values taken from the downloaded manifest are printed only after
validation, so a hostile file cannot write workflow commands or forge a verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ASSETS = ("TDflowMCP.tox", "TDflowMCP.json", "SHA256SUMS", "INSTALL.md")
LISTED = ("TDflowMCP.tox", "TDflowMCP.json", "INSTALL.md")
ALIAS_SUMS = "SHA256SUMS-versioned"
TAG_RE = re.compile(r"^v([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})\Z")
NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}\Z")
SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}\Z")
TD_BUILD_RE = re.compile(r"^[0-9]{4}\.[0-9]{1,6}\Z")
MAX_BYTES = 64 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def valid_repo(repo: str) -> bool:
    parts = (repo or "").split("/")
    return len(parts) == 2 and all(NAME_RE.match(p) and p not in (".", "..") for p in parts)


def parse_sums(text: str) -> dict:
    """``{file name: sha256}`` from ``sha256sum`` lines (``<hex>  <name>``), as ``sha256sum -c`` reads them: lines end
    with a line feed only. Anything else is an error."""
    if "\r" in text or not text.isascii():
        raise ValueError("SHA256SUMS has characters that are not plain ASCII with line feeds")
    out = {}
    for line in text.split("\n"):
        if not line:
            continue
        m = re.match(r"^([0-9a-f]{64}) [ *]([A-Za-z0-9_.-]+)\Z", line)
        if not m or m.group(2) in out:
            raise ValueError("SHA256SUMS has a line that is not '<sha256>  <file name>' or names a file twice")
        out[m.group(2)] = m.group(1)
    return out


def versioned_downloads(sums: dict, alias: str, require_versioned: bool) -> tuple:
    if alias in sums:
        return (alias,)
    if require_versioned:
        return (alias, ALIAS_SUMS)
    return ()


def check(files: dict, tag: str, expect_tox: str = "", expect_sums: str = "", expect_commit: str = "",
          require_versioned: bool = False) -> list:
    """Problems found in the downloaded ``files`` ({name: bytes}); an empty list means everything matches."""
    problems = []
    m = TAG_RE.match(tag or "")
    if not m:
        return ["the tag is not of the form vX.Y.Z"]
    missing = [n for n in ASSETS if n not in files]
    if missing:
        return ["missing asset(s): " + ", ".join(missing)]
    try:
        sums = parse_sums(files["SHA256SUMS"].decode("ascii"))
    except UnicodeDecodeError:
        return ["SHA256SUMS has characters that are not plain ASCII with line feeds"]
    except ValueError as e:
        return [str(e)]
    alias = f"TDflowMCP-{tag}.tox"
    extra = versioned_downloads(sums, alias, require_versioned)
    missing = [name for name in extra if name not in files]
    if missing:
        problems.append("missing asset(s): " + ", ".join(missing))
    listed = (*LISTED, alias) if alias in sums else LISTED
    if set(sums) != set(listed):
        problems.append("SHA256SUMS must list exactly " + ", ".join(listed[:-1]) + " and " + listed[-1])
    for name in listed:
        if name in sums and name in files and sums[name] != sha256(files[name]):
            problems.append(f"{name} does not have the SHA-256 that SHA256SUMS states")
    tox = files["TDflowMCP.tox"]
    if alias in extra and alias in files and files[alias] != tox:
        problems.append(f"{alias} is not byte-identical to TDflowMCP.tox")
    if ALIAS_SUMS in extra and ALIAS_SUMS in files:
        try:
            alias_sums = parse_sums(files[ALIAS_SUMS].decode("ascii"))
        except ValueError:
            alias_sums = {}
        if alias_sums != {alias: sha256(tox)}:
            problems.append(f"{ALIAS_SUMS} must list only {alias} with the SHA-256 of TDflowMCP.tox")
    try:
        meta = json.loads(files["TDflowMCP.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return problems + ["TDflowMCP.json is not valid JSON"]
    if not isinstance(meta, dict):
        return problems + ["TDflowMCP.json is not a JSON object"]
    if meta.get("sha256") != sha256(tox):
        problems.append("TDflowMCP.json: sha256 is not the SHA-256 of the downloaded TDflowMCP.tox")
    if type(meta.get("size")) is not int or meta["size"] != len(tox):
        problems.append("TDflowMCP.json: size is not the size of the downloaded TDflowMCP.tox")
    if meta.get("version") != m.group(1):
        problems.append(f"TDflowMCP.json: version is not {m.group(1)}")
    if meta.get("release") is not True:
        problems.append("TDflowMCP.json: release is not true")
    if meta.get("source_dirty") is not False:
        problems.append("TDflowMCP.json: source_dirty is not false")
    package = meta.get("package")
    if not isinstance(package, dict) or package.get("tag") != tag:
        problems.append(f"TDflowMCP.json: package.tag is not {tag}")
    commit = meta.get("source_commit")
    if not isinstance(commit, str) or not COMMIT_RE.match(commit):
        problems.append("TDflowMCP.json: source_commit is not a commit id")
    elif expect_commit and commit != expect_commit:
        problems.append("TDflowMCP.json: source_commit is not the commit the tag points to")
    if not isinstance(meta.get("td_build_used"), str) or not TD_BUILD_RE.match(meta["td_build_used"]):
        problems.append("TDflowMCP.json: td_build_used is not a TouchDesigner build number")
    if expect_tox and sha256(tox) != expect_tox:
        problems.append("TDflowMCP.tox does not have the expected SHA-256")
    if expect_sums and sha256(files["SHA256SUMS"]) != expect_sums:
        problems.append("SHA256SUMS does not have the expected SHA-256")
    return problems


def manifest_facts(files: dict) -> str:
    """The manifest's version, source commit and TouchDesigner build for the report: validated values only, anything
    else as ``(invalid)``, so that downloaded text never reaches the output unchecked."""
    try:
        meta = json.loads(files.get("TDflowMCP.json", b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        meta = {}
    meta = meta if isinstance(meta, dict) else {}

    def shown(value, pattern):
        return value if isinstance(value, str) and re.match(pattern, value) else "(invalid)"
    return (f"manifest: version {shown(meta.get('version'), r'^[0-9A-Za-z.]{1,20}$')}, release "
            f"{meta.get('release') is True}, source_commit {shown(meta.get('source_commit'), COMMIT_RE.pattern)}, "
            f"td_build_used {shown(meta.get('td_build_used'), TD_BUILD_RE.pattern)}")


def command(kind: str, title: str, data: str) -> str:
    """A GitHub Actions workflow command with its data and title escaped."""
    data = str(data).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = str(title).replace("%", "%25").replace("\r", "").replace("\n", " ").replace(",", "%2C").replace(":", "%3A")
    return f"::{kind} title={title}::{data}"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tdflowmcp-verify-release"})
    with urllib.request.urlopen(req, timeout=120) as r:          # certificates are verified (the default)
        final = urllib.parse.urlsplit(r.geturl())
        host = (final.hostname or "").lower()
        if final.scheme != "https" or not (host == "github.com" or host.endswith(".githubusercontent.com")):
            raise ValueError("the download did not end on GitHub over HTTPS")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("the asset is unexpectedly large")
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or "jlopesmo/TDflowMCP")
    ap.add_argument("--expect-sums-sha256", default="")
    ap.add_argument("--expect-tox-sha256", default="")
    ap.add_argument("--expect-commit", default="")
    ap.add_argument("--require-versioned", action="store_true")
    a = ap.parse_args(argv)
    e_sums, e_tox = a.expect_sums_sha256.strip().lower(), a.expect_tox_sha256.strip().lower()
    e_commit = a.expect_commit.strip().lower()
    if (not TAG_RE.match(a.tag) or not valid_repo(a.repo) or (e_sums and not SHA_RE.match(e_sums))
            or (e_tox and not SHA_RE.match(e_tox)) or (e_commit and not COMMIT_RE.match(e_commit))):
        print("usage error: the tag must be vX.Y.Z, the repository owner/name, expected hashes 64 hexadecimal "
              "characters and the expected commit 40")
        return 2
    on_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    files, lines = {}, []

    def download(name):
        url = f"https://github.com/{a.repo}/releases/download/{a.tag}/{name}"
        try:
            files[name] = fetch(url)
        except Exception as e:                                    # a failed download is a finding, not a crash
            print(f"download failed: {name}: {type(e).__name__}")
            return
        line = f"{name}: {len(files[name])} bytes, sha256 {sha256(files[name])}"
        lines.append(line)
        print(line)
        if on_actions:
            print(command("notice", f"{a.tag} {name}", f"{len(files[name])} bytes, sha256 {sha256(files[name])}"))
    for name in ASSETS:
        download(name)
    if "SHA256SUMS" in files:
        try:
            sums = parse_sums(files["SHA256SUMS"].decode("ascii"))
        except ValueError:
            sums = {}
        for name in versioned_downloads(sums, f"TDflowMCP-{a.tag}.tox", a.require_versioned):
            download(name)
    problems = check(files, a.tag, e_tox, e_sums, e_commit, require_versioned=a.require_versioned)
    facts = manifest_facts(files)
    print(facts)
    anchored = bool(e_sums)
    anchor = ("anchored to the recorded SHA256SUMS hash" + (", the recorded .tox hash" if e_tox else "")
              + (" and the tag's commit" if e_commit else "")) if anchored else \
        "NOT ANCHORED: no recorded SHA256SUMS hash was given, so the files only agree with each other"
    verdict = "PASS" if not problems and anchored else "FAIL"
    for p in problems:
        print("problem:", p)
        if on_actions:
            print(command("error", "Release verification", p))
    ref = (f"; workflow ref {os.environ.get('GITHUB_REF', '')} at {os.environ.get('GITHUB_SHA', '')[:12]}"
           if on_actions else "")
    text = f"{verdict}: {a.repo} {a.tag}, anonymous download, {anchor}; {facts}{ref}"
    print("VERDICT " + text)
    if on_actions:
        print(command("notice" if verdict == "PASS" else "error", f"{a.tag} verdict", text))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"### Release {a.tag}: {verdict}\n\n" + "\n".join(
                    "- " + x.replace("`", "'") for x in lines + [facts, anchor]) + "\n")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
