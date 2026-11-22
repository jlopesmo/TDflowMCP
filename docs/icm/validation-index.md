# TDflowMCP — Validation index

This file lists every check that exists, how to run it, and its last **executed and observed** result. Unrun checks are marked **not executed**. Unobservable results are marked **not observed**, with the reason.

## Checks

| Check | Command | Scope | Last result |
|---|---|---|---|
| Doc checker | `python tools/check_docs.py` | Required files, relative links/images + anchors, ADR status vs index, privacy patterns in `.md/.py/.yml/.yaml/.json/.txt` | See runs below |
| Doc checker unit tests | `python -m unittest discover -s tests -t .` | `tools/check_docs.py` | See runs below |
| CI workflow `docs.yml` | GitHub Actions on push to `dev`/`main` and on every pull request | Both of the above on Python 3.11, Ubuntu + Windows | **Not observed.** The private repo, no `gh` CLI, and a denied credential-based API read mean no run status, URL or log has been seen for any commit. Owner action [B3](roadmap.md#open-blockers-and-owner-actions) |
| Independent documentation review | separate agent, read-only | All docs + tooling | Review 1 and review 2, see runs below |
| Compatibility checks, protocol/policy/audit unit tests, in-TD tests, `.tox` build | — | — | **Do not exist** (Phase 1A/1B) |

## Runs

| Date | Environment | Check | Result |
|---|---|---|---|
| 2026-11-22 | Windows 11, CPython 3.11.8 | `check_docs.py` (foundation `82b64f1`) | Pass: `35 markdown files, 0 problem(s)` |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (foundation) | Pass: `Ran 12 tests … OK` |
| 2026-11-22 | — | Independent review 1 | 20 findings; dispositions in [review 1](../research/2026-11-22-phase-0-independent-review.md) |
| 2026-11-22 | — | Independent review 2 (revision) | 21 findings (3 High, 11 Medium, 7 Low), all addressed; see [review 2](../research/2026-11-22-phase-0-revision-review.md) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | `check_docs.py` (revision, pre-commit) | Pass: `47 markdown files, 0 problem(s)` |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (revision, pre-commit) | Pass: `Ran 14 tests in 0.238s OK` |

## Local environment facts (file inspection only, 2026-11-22)

- **TouchDesigner 2025.32440** is installed, with `python311.dll`, `_sqlite3.pyd`, `jsonschema` 4.23 and `pip` 24.2. A legacy install bundles `python39.dll`. **2025.33230+ is not installed.** TouchDesigner was not launched.
- Node.js v24.14.0 and Python 3.11.8 are on PATH. There is no `gh` CLI.
- Original bridge hashes were verified with `sha256sum`: the `.tox` matches the audit's stated SHA-256, and the committed audit/zip copies are byte-identical to the supplied files.
