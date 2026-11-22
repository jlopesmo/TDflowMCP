"""Secret scanner for CI and local use (Phase 1A item 6). Stdlib only.

    python tools/scan_secrets.py            # scans every git-tracked file; exit 1 on findings

Detects real-format TDflowMCP agent tokens, private keys, common cloud/API tokens and expanded
user-profile paths. Binary and historical-reference files are skipped. Findings print as
``path:line: kind`` without echoing the matched value.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Iterable, List, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PATTERNS = [
    ("tdflowmcp_token", re.compile(r"tdf_[A-Za-z0-9]{12}\.[A-Za-z0-9_\-]{43,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|github_pat_[A-Za-z0-9_]{60,}")),
    ("slack_token", re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("windows_profile_path", re.compile(r"[A-Za-z]:\\\\?Users\\\\?(?!Public\b)[A-Za-z0-9._-]{2,}\\\\?", re.I)),
    ("posix_home_path", re.compile(r"/(?:home|Users)/(?!runner\b|user\b|username\b|you\b)[a-z][a-z0-9._-]{2,}/")),
]
SKIP_PREFIXES = ("reference/historical/",)
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".zip", ".tox", ".toe", ".pyc", ".ico", ".pdf"}


def tracked_files() -> List[str]:
    out = subprocess.run(["git", "-c", f"safe.directory={ROOT.replace(os.sep, '/')}", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("git ls-files failed: " + out.stderr.strip())
    return [f for f in out.stdout.splitlines() if f]


def scan_text(text: str) -> List[Tuple[int, str]]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for kind, rx in PATTERNS:
            if rx.search(line):
                hits.append((i, kind))
    return hits


def scan(files: Iterable[str]) -> List[str]:
    findings = []
    for rel in files:
        if rel.startswith(SKIP_PREFIXES) or os.path.splitext(rel)[1].lower() in SKIP_EXT:
            continue
        p = os.path.join(ROOT, rel)
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        for line, kind in scan_text(data.decode("utf-8", "replace")):
            findings.append(f"{rel}:{line}: {kind}")
    return findings


def main(argv=None) -> int:
    files = (argv or sys.argv[1:]) or tracked_files()
    findings = scan(files)
    for f in findings:
        print(f)
    print(f"scan_secrets: {len(list(files))} files, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
