# Verification, testing and release procedures

**Status:** Phase 0 procedures (§1) are active. §2–§4 are **Proposed** and take effect as the corresponding tooling is implemented.

## 1. Every change (active now)

1. `python tools/check_docs.py` passes. It checks required ICM files, relative links and anchors, ADR status lines and the ADR index.
2. `python -m unittest discover -s tests -t .` passes (currently only tests for the doc checker).
3. Affected ICM files are updated. `validation-index.md` lists what was actually executed, with results.
   - **CI evidence rule:** record a CI result only when it was observed: a run URL/ID with its conclusion, or output pasted by the owner. Otherwise write **not observed** and say why. A workflow file existing, or a push succeeding, is not CI evidence.
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

1. All gates green on `dev`, with **observed** CI evidence. In-TD tests are run on the **declared minimum TD build** and the latest TD build, with build numbers recorded.
2. Changelog and `current-state.md` updated. Version bumped (SemVer).
3. Owner approves the PR `dev → main`.
4. Tag `vX.Y.Z` (annotated) on `main`. Build from the clean tag with `python -I tools/package_release.py --release-tag vX.Y.Z`. The package includes `TDflowMCP-vX.Y.Z.tox`, the identical `TDflowMCP.tox`, `TDflowMCP.json` (version, TD build, SHA-256, tag and `"release": true`), `INSTALL.md`, and `SHA256SUMS` covering the four other files.
5. Attach those five files to the GitHub Release. List `TDflowMCP-vX.Y.Z.tox` as the primary download and keep the unversioned asset for existing links. The package ZIP is for local distribution.
6. On GitHub-hosted runners, start `verify-release` on `main` with the tag, `expect_sums_sha256` from the package's recorded `sums_sha256`, and `expect_tox_sha256` from `tox_sha256`. Keep `require_versioned` enabled. The verifier anonymously downloads each public asset over verified HTTPS. It checks the package receipts, the build manifest, the tag's commit and exact binary equality. Both runners publish hashes and verdicts as annotations.
7. Record the Windows and Ubuntu receipts in [the validation index](../icm/validation-index.md) before publishing a new direct download link.

### Versioned aliases for v1.0.0 and v1.0.1

Use each tag's saved and tested `.tox` to make a byte-identical `TDflowMCP-vX.Y.Z.tox`. Before attaching the copy, verify the original public download against its [recorded hashes](../icm/validation-index.md). Attach the copy and `SHA256SUMS-versioned` containing one LF-terminated line with the original binary's SHA-256, two spaces and the versioned filename. Keep the original `.tox`, `SHA256SUMS`, manifest, install guide and annotated tag. Run `verify-release` on `main` with `require_versioned` enabled and the original recorded hashes. The verifier checks the original manifest, the supplemental checksum and byte equality of both downloads.

Both backfills passed on GitHub-hosted Windows and Ubuntu runners. The validation index records the [run URLs, binary hashes and checksum anchors](../icm/validation-index.md#2026-11-25-versioned-download-maintenance).

**Owner-approved v1.0.0 compatibility exception (2026-11-25):** For the initial **Windows** release only, the owner approved accepting the fully tested **TouchDesigner 2025.32440** build without running the latest TouchDesigner version. The newer-build requirement in release step 1 is waived **only for v1.0.0**. Do not claim compatibility with untested newer builds or macOS. Observed passing Windows and Ubuntu GitHub CI, a tested clean-tag binary, hash verification and owner release authorization are still mandatory. Later releases return to the normal compatibility policy unless separately approved.

**v1.0.1, the same scope (owner D28, 2026-11-25):** the owner scoped v1.0.1 to Windows with **TouchDesigner 2025.32440** ([Issue #3](https://github.com/jlopesmo/TDflowMCP/issues/3#issuecomment-7175371704)) and instructed that TouchDesigner is not upgraded and that the final `.tox` is built and tested on 2025.32440. That is recorded here as the separate approval the paragraph above requires: the latest-build test of step 1 was not run for v1.0.1 either, and no compatibility with newer builds, macOS or Linux is claimed. Everything else in §4 applies.

**v1.1.0 compatibility exception (owner, 2026-11-26):** The owner approved Windows with **TouchDesigner 2025.32440** for v1.1.0, subject to passing every other release gate. The latest-build test is waived for this release only. The compatibility claim is limited to tested builds and platforms.

**Never:** release from `dev`, release an untested `.tox`, or claim compatibility with a TD build that was not tested.
