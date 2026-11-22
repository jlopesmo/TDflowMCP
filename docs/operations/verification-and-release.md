# Verification, testing and release procedures

**Status:** Phase 0 procedures (§1) are active. §2–§4 are **Proposed** and take effect as the corresponding tooling is implemented.

## 1. Every change (active now)

1. `python tools/check_docs.py` passes. It checks required ICM files, relative links and anchors, ADR status lines and the ADR index.
2. `python -m unittest discover -s tests -t .` passes (currently only tests for the doc checker).
3. Affected ICM files are updated. `validation-index.md` lists what was actually executed, with results.
4. Commit message states what changed and which checks ran.

## 2. Code changes (from Phase 1)

| Gate | Command (planned) | Where |
|---|---|---|
| Unit tests | `python -m unittest discover -s tests -t .` | CI (Windows + Ubuntu, Python 3.11) and local |
| No-`td` boundary | `tests/unit/test_boundaries.py` (planned) | CI |
| Secret scan | CI step (tool selection via ADR) | CI |
| Build | `tools/build_tox` (planned) | Local machine with TD |
| In-TD integration | `tests/td` runner (planned) → `build/td-test-results.json` | Local / self-hosted |
| Live client smoke | Connect Claude Code to a scratch project, run scripted scenario | Local, recorded manually |

## 3. Security-sensitive changes

Changes to policy, identity, gate, audit, exec or network require: new or updated tests for both allow and deny paths, an update to [security-model.md](../architecture/security-model.md), and an independent review (separate agent or human) recorded in the PR.

## 4. Release (proposed)

1. All gates green on `dev`. In-TD tests are run on the **declared minimum TD build** and the latest TD build, with build numbers recorded.
2. Changelog and `current-state.md` updated. Version bumped (SemVer).
3. Owner approves the PR `dev → main`.
4. Tag `vX.Y.Z` on `main`. Build `.tox` from the tag on a clean checkout. Generate `TDflowMCP.json` (version, TD build, sha256).
5. Publish a GitHub Release with assets. Verify by downloading the asset and checking the sha256.
6. Record release evidence in `validation-index.md`.

**Never:** release from `dev`, release an untested `.tox`, or claim compatibility with a TD build that was not tested.
