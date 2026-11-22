#!/usr/bin/env python3
"""TDflowMCP documentation checker (stdlib only, Python 3.9+).

Checks:
  1. Required ICM / governance files exist.
  2. Every relative Markdown link or image resolves to an existing path inside
     the repository, and every #anchor resolves to a heading in the target
     (approximating GitHub's slug rules).
  3. Every ADR in docs/decisions/NNNN-*.md has a '**Status:**' line containing
     a recognised status word, is listed in docs/decisions/README.md, and the
     index row's status words equal the ADR's status words.
  4. No user-profile paths or e-mail addresses are committed in text files
     (.md, .py, .yml, .yaml, .json, .txt).

Usage: python tools/check_docs.py [repo_root]
Exit code 0 = pass, 1 = failures (printed one per line).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "repoinspire.md",
    "docs/icm/README.md",
    "docs/icm/context.md",
    "docs/icm/invariants.md",
    "docs/icm/current-state.md",
    "docs/icm/roadmap.md",
    "docs/icm/project-map.md",
    "docs/icm/validation-index.md",
    "docs/decisions/README.md",
    "docs/architecture/README.md",
    "docs/milestones/README.md",
    "docs/product/vision.md",
    "docs/architecture/setup-assistant.md",
    "docs/architecture/agent-integration.md",
    "docs/architecture/continuity-and-memory.md",
    "docs/architecture/security-model.md",
]

# "Conditionally Accepted" is matched first so it is distinguished from plain "Accepted".
STATUS_WORDS = ("Conditionally Accepted", "Proposed", "Accepted", "Superseded", "Rejected")
STATUS_RE = re.compile(r"\b(" + "|".join(STATUS_WORDS) + r")\b")
ADR_NAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
LINK = re.compile(r"!?\[[^\]]*\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`]*`")
USER_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+"
    r"|(?<![\w.~])/(?:home|Users)/[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
EMAIL_ALLOW = ("noreply@anthropic.com",)
TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".json", ".txt"}
SKIP_DIRS = {".git", "node_modules", "build", "dist", ".tmp", ".venv", "__pycache__"}


def slugify(heading: str) -> str:
    """Approximate GitHub's heading anchor algorithm.

    Lower-case; drop characters that are not word characters, hyphens or
    spaces (underscores are kept); spaces become hyphens.
    """
    text = re.sub(r"`([^`]*)`", r"\1", heading)          # code ticks
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> text
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def strip_code(lines):
    """Yield (lineno, line) for lines outside fenced code, inline code removed."""
    in_fence = False
    for no, line in enumerate(lines, 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield no, INLINE_CODE.sub("", line)


def anchors_for(path: Path, cache: dict) -> set:
    if path not in cache:
        seen: dict = {}
        result = set()
        for _, line in strip_code(path.read_text(encoding="utf-8").splitlines()):
            m = HEADING.match(line)
            if not m:
                continue
            base = slugify(m.group(2))
            n = seen.get(base, 0)
            result.add(base if n == 0 else f"{base}-{n}")
            seen[base] = n + 1
        cache[path] = result
    return cache[path]


def _walk(root: Path, suffixes):
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in suffixes:
            if not SKIP_DIRS.intersection(p.relative_to(root).parts):
                yield p


def markdown_files(root: Path):
    return _walk(root, {".md"})


def _status_words(text: str) -> set:
    return set(STATUS_RE.findall(text))


def check(root: Path) -> list:
    errors = []
    root = root.resolve()

    for rel in REQUIRED_FILES:
        if not (root / rel).is_file():
            errors.append(f"missing required file: {rel}")

    # Privacy patterns in all tracked-style text files.
    for f in _walk(root, TEXT_SUFFIXES):
        rel_f = f.relative_to(root).as_posix()
        for no, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in USER_PATH.finditer(line):
                errors.append(f"{rel_f}:{no}: user-profile path committed: {m.group(0)}")
            for m in EMAIL.finditer(line):
                if m.group(0).lower() not in EMAIL_ALLOW:
                    errors.append(f"{rel_f}:{no}: e-mail address committed")

    # Links and anchors.
    cache: dict = {}
    for md in markdown_files(root):
        rel_md = md.relative_to(root).as_posix()
        for no, line in strip_code(md.read_text(encoding="utf-8").splitlines()):
            for m in LINK.finditer(line):
                target = m.group(1)
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):  # http:, mailto: ...
                    continue
                file_part, _, anchor = target.partition("#")
                file_part = file_part.replace("%20", " ")
                dest = md if not file_part else (md.parent / file_part).resolve()
                try:
                    dest.relative_to(root)
                except ValueError:
                    errors.append(f"{rel_md}:{no}: link leaves repository -> {target}")
                    continue
                if not dest.exists():
                    errors.append(f"{rel_md}:{no}: broken link -> {target}")
                    continue
                if anchor and dest.is_file() and dest.suffix == ".md":
                    if anchor not in anchors_for(dest, cache):
                        errors.append(f"{rel_md}:{no}: missing anchor -> {target}")

    # ADRs.
    dec = root / "docs" / "decisions"
    index_path = dec / "README.md"
    index_lines = index_path.read_text(encoding="utf-8").splitlines() if index_path.is_file() else []
    if dec.is_dir():
        for adr in sorted(dec.glob("*.md")):
            if adr.name == "README.md":
                continue
            rel_adr = adr.relative_to(root).as_posix()
            if not ADR_NAME.match(adr.name):
                errors.append(f"{rel_adr}: ADR filename must be NNNN-kebab-title.md")
                continue
            text = adr.read_text(encoding="utf-8")
            status = re.search(r"^\*\*Status:\*\*(.*)$", text, re.M)
            adr_words = _status_words(status.group(1)) if status else set()
            if not status:
                errors.append(f"{rel_adr}: missing '**Status:**' line")
            elif not adr_words:
                errors.append(f"{rel_adr}: status must contain one of {', '.join(STATUS_WORDS)}")
            rows = [l for l in index_lines if f"({adr.name})" in l]
            if not rows:
                errors.append(f"{rel_adr}: not listed in docs/decisions/README.md")
            elif adr_words:
                cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
                index_words = _status_words(cells[-1]) if cells else set()
                if index_words != adr_words:
                    errors.append(
                        f"{rel_adr}: index status {sorted(index_words)} != ADR status {sorted(adr_words)}"
                    )
    return errors


def main(argv) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    errors = check(root)
    for e in errors:
        print(e)
    files = sum(1 for _ in markdown_files(root.resolve()))
    print(f"check_docs: {files} markdown files, {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
