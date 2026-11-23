# Post-restart recovery, audit and cleanup (2026-11-23)

**Owner direction D19 (2026-11-23):** the PC was restarted during development. Recover the exact state, preserve valuable work, clean up disposable temporary artifacts, re-validate the baseline, then continue the development mission in dependency order (roadmap [D19](../icm/roadmap.md#owner-decisions-2026-11-22)).

## Recovery findings

| Question | Finding (observed) |
|---|---|
| Branch and commits | `dev` at `c888d00`. `origin/dev` = `c888d00…` and `origin/main` = `066dbd0…` (`git ls-remote`), so `main` is unchanged |
| Working tree | Clean: nothing staged, modified or untracked. No stash, no extra worktree |
| Unfinished Git operation | None (no merge, rebase, cherry-pick or lock). One dangling blob, a superseded staged copy of the validation index; left alone |
| What was running at the restart | Nothing. The previous session ended with a final report right after pushing `c888d00`. No file in the repository, including git-ignored folders, is newer than that commit |
| Last recorded owner direction | D18 (Phase 2A priorities), delivered in increment 2. **No message before the restart defined a multi-phase mission**; that direction is D19, given after the restart |
| Lost or unrecoverable work | **None found** |
| Built artifact versus source | `build/TDflowMCP.tox` was the tested candidate `68e1f1f9…`, built from the uncommitted tree of `b56a40f`. A clean rebuild of `c888d00` has the **same source digest** (`1e10962d…`), so the committed source is exactly what was tested |
| Documentation versus code | Consistent. One stale line corrected: the unit-test count in [current state](../icm/current-state.md) |

## Environment after the restart
- TouchDesigner 2025.32440 and its bundled Python 3.11.10 (SQLite 3.45.1, `jsonschema`) work. CPython 3.11.8, Node.js v24.14.0 and Claude Code 2.1.289 are on `PATH`. There is still no `gh` CLI, so CI stays unobserved ([B3](../icm/roadmap.md#open-blockers-and-owner-actions)).
- No TouchDesigner process, no stale Python process and no listener on ports 9480–9483 were found. Nothing was terminated and nothing was installed.
- Git reports "dubious ownership" because the drive does not record ownership. The documented per-command `safe.directory` override was used; the global Git configuration was not changed.

## Cleanup
A manifest with every path, classification and reason was written **before** anything was moved or deleted (`.tmp/archive/2026-11-23-pre-restart/CLEANUP-MANIFEST.md`, git-ignored). Only project-owned temporary artifacts inside the repository folder were touched. No `git clean`, no reset.

| Class | Items |
|---|---|
| **Kept in place** | `.tmp/references/` (original bridge binary, [B2](../icm/roadmap.md#open-blockers-and-owner-actions)); the harness run folders `.tmp/td-*` (test audit databases, test keys, stall-watchdog dumps); `.tmp/td-dogfood/` (evidence for the unreproduced hang); `build/`; `dist/TDflowMCP-0.2.0.dev1-2a7e72f361` and its zip |
| **Archived** (`.tmp/archive/2026-11-23-pre-restart/`) | A snapshot of `build/` (evidence for candidate `68e1f1f9…`, which the rebuild overwrites); 41 loose run logs and result summaries; the uncommitted early draft `phase2a-draft/`; the spike run folders `s4`, `s4b`, `s5`, `s6`, `s7` with their results; one scratch script |
| **Deleted** (about 46 MB) | Four throwaway headless-browser profiles from spike S6 and its debugging session; `.tmp/s6/learn/`, a byte-identical duplicate of the archived `s5/learn/` (tree digests compared first); Python bytecode caches |
| **Left untouched, outside the repository** | 45 leaked unit-test folders in the system temp folder (`tdflow-aw-*`, `tdflow-cont-*`, about 6 MB). They hold throwaway test audit databases. **Owner action:** delete them at your convenience |

**Defect found and fixed:** `tests/unit/test_auditworker.py` leaked one temp folder per run on Windows (an abandoned worker kept its database open, so the folder could not be removed). The test now closes that worker. Observed: the folder count is unchanged after a run.

`.tmp/wt-e15ec55/` (the clean Phase 1B baseline build used by the gate suite's `perf_baseline` launch) no longer exists. The performance comparison launches need it rebuilt before they can run again; the audit-performance investigation is closed (D17), so it was not rebuilt.

## Baseline re-validation (clean build of `c888d00`)

| Check | Result (observed 2026-11-23) |
|---|---|
| `check_docs.py`, `scan_secrets.py` | 0 problems (74 files); 0 findings (180 files) |
| Unit tests, `discover -s tests -t .` | `Ran 280 tests … OK` on CPython 3.11.8 (2 skipped) and on TD Python 3.11.10 (1 skipped) |
| `.tox` build with `--check-reproducible` | Pass. Tox SHA-256 `e79ed3fe2faf4c5d827817ec119b3212beaf99d1b170812f42a20b742dcfc56c`; `source_commit` `c888d00…`, `source_dirty` false; no embedded hash mismatch; source digest equal across both builds; expanded differences only `.build` and `local/time/cframe.ts` |
| Phase 1A suite | 49/49 + 69/69 |
| Phase 1B security and permission suite | 43/43 + 115/115 |
| Gate suite (`gate`, `gate_crash`, `gate_restart`) | 46/46 + 47/47. OnFrame p99 1.57 ms under load and 1.42 ms with a 50 ms disk. Crash: 58 reported successes durable, 5 in-flight intents unaccounted (≤ 8 allowed) |
| Memory and restart suite | 26/26 + 99/99. 2000-operator scope: frame max 6.9 ms |
| Onboarding, live Claude Code 2.1.289 | 5/5 + 47/47; `RESULT skill_version=1.1 expected_skill_version=1.1 opacity=0.25 audit_seq=22` |
| Stall watchdog | Empty dump in every launch |
| CI | **Not observed** |

No failure occurred, so there is no restart-related issue and no new defect in the product. The original hang ([increment 2](2026-11-23-phase-2a-increment-2.md)) remains unexplained and unreproduced.
