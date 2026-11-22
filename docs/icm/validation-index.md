# TDflowMCP — Validation index

Every check that exists, how to run it, and its last **executed** result. Unrun checks are marked **not executed**.

## Checks

| Check | Command | Scope | Last result |
|---|---|---|---|
| Doc checker | `python tools/check_docs.py` | Required files, relative links + anchors, ADR status/index, privacy patterns | See Phase 0 run below |
| Doc checker unit tests | `python -m unittest discover -s tests -t .` | `tools/check_docs.py` behavior | See Phase 0 run below |
| CI workflow `docs.yml` | GitHub Actions on push/PR | Both of the above on Python 3.11 (Ubuntu + Windows) | **Not executed.** No GitHub Actions run has been observed yet |
| MCP protocol / policy / audit unit tests | — | — | **Do not exist** (Phase 1) |
| In-TD integration tests | — | — | **Do not exist** (Phase 1) |
| `.tox` build | — | — | **Does not exist** (Phase 1) |

## Phase 0 runs

Recorded at commit time. The Phase 0 commit SHA is in [current-state.md](current-state.md).

| Date | Environment | Check | Result |
|---|---|---|---|
| 2026-11-22 | Windows 11, CPython 3.11.8 (Git Bash) | `python tools/check_docs.py` | **Pass**: `check_docs: 35 markdown files, 0 problem(s)`, exit 0 (after review corrections) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | `python -m unittest discover -s tests -t .` | **Pass**: `Ran 12 tests … OK` |
| 2026-11-22 | n/a | Independent review | Performed; 20 findings, dispositions in [review record](../research/2026-11-22-phase-0-independent-review.md) |

## Local environment facts used by the docs (file inspection only, 2026-11-22)

- TouchDesigner 2025.32440 install present with `python311.dll`, `_sqlite3.pyd`, `jsonschema` 4.23 and `pip` 24.2. A legacy install bundles `python39.dll`. **TouchDesigner was not launched.**
- Node.js v24.14.0 and Python 3.11.8 present on PATH. The `gh` CLI is not installed.
