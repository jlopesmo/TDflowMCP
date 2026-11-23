# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev`. Phase 1C (agent onboarding) is complete in the development build, together with the ADR-0006 audit worker and project-local storage** (owner D13). **Phase 2A increment 1 (project memory, checkpoints, verified handoffs) is implemented and verified in TD** (owner D17). A live Claude Code session connected using only the generated instructions, and the packaged component passes its in-TouchDesigner acceptance suites on **2025.32440**. Phases 1A and 1B are re-verified on the same build. This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- **Repository:** private `jlopesmo/TDflowMCP`. `main` = `066dbd0` (Phase 0 foundation), **unchanged**. Development continues on `dev`.
- **ADRs:**
  - 0002, 0003, 0006, 0007, 0009, 0010, 0012 and 0013: Conditionally Accepted, with their gates recorded.
  - **0011: Conditionally Accepted** (owner D13); its 1C gates passed.
  - **ADR-0006, revised:** the latency question is closed (owner B6). The audit worker with `synchronous=FULL` is the baseline. The main-thread-continuation experiment was rejected (−0.2 % median), [spike B6](../../spikes/b6/README.md).
  - **0014 (Candidate Development Mode, A/B self-development): Conditionally Accepted** (owner D17): stage 1 (isolated candidate build/test, manual owner promotion and rollback) approved; stage 2 (same-project candidate editing) is research only, gated by S8. Not implemented ([design](../architecture/self-development.md)).
  - **Audit performance investigation: closed** (owner D17). The verified worker is the baseline.
- **Phase 1A and 1B:** as recorded in the [1A record](../research/2026-11-22-phase-1a-component.md) and the [1B record](../research/2026-11-22-phase-1b-mcp-core.md). Re-verified on the 1C build after [review 7](../research/2026-11-22-phase-1c-review.md): **1A 49/49 + 69/69; 1B 43/43 + 110/110**.
- **Audit worker** ([record](../research/2026-11-22-audit-worker-and-phase-1c.md)):
  - One SQLite worker thread, FIFO; `synchronous=FULL` + WAL kept.
  - TD's main thread never waits for disk I/O. MCP and admin requests are orchestrated on handler threads; TD steps run on the main thread via `call_main`.
  - Durable intent before execute, durable outcome before success. Fail closed on overflow, timeout, worker failure or lost durability; agents are suspended when an outcome cannot be recorded.
  - In TD:
    - OnFrame p99 ≤ 1.6 ms under load and with a simulated 50 ms disk; frame max 23.6 ms (1B: 46.5 ms).
    - Crash, kill-switch race, overflow, injected outcome failure and restart all pass.
  - **MCP latency is higher:** ≈ 83–152 ms sequential p50, against 55 ms on a clean 1B build. Cause: TouchDesigner GIL handoffs, measured.
  - In TD, a `ping` costs one frame (17 ms) and a mutation five frames (≈ 84 ms). This is the floor of off-main-thread durable writes, which the B6 continuation could not lower. Main-thread writes were faster but are excluded (frame impact).
  - In real agent sessions, model API time was 34–40 s of 38–47 s per task. TDflowMCP's share is *estimated* at ≈ 1–2 s (≈ 83 ms × 10–25 calls); it is not separately measured.
- **Project-local storage:**
  - The audit DB, sessions and DAT snapshots go in a git-ignored `.tdflowmcp/<name>/` next to the `.toe`. Credentials, the audit key and keyed checkpoints stay in private per-user storage.
  - Unsaved, read-only, network and cloud-synced folders fall back to private storage (fallbacks unit-tested; project-local in TD).
  - Moved, copied and relocated projects are detected, and nothing is relocated without Confirm Move (verified copy or adoption) or Claim As New Project. TD incremental saves count as the same project. All of this is verified in TD.
- **Phase 1C:**
  - **Client registry:** `src/integrations/clients.json`. Claude Code is primary; Codex, Cursor and VS Code Copilot are documented only.
  - **Connection instructions:** "Connect your coding agent", with the token always `${TDFLOW_TOKEN}`.
  - **Live Claude Code 2.1.289 session:** connected using the generated text; scoped edit audited; no token in any file or output.
  - **Skills:** `tdflow-core` v1.0, with a consent-based install/update/uninstall round-trip and hash manifest.
  - **Version mismatch** is surfaced on the Setup page and through `tdflow_whoami`.
  - **UX corrections:** shorter Status Detail, an Advanced page, View Agent Instructions.
  - **Packaging:** `tools/package_release.py` and the [install guide](../operations/install.md).
- **Protocol fix found live:** MCP 2026-07-28 caching hints (`ttlMs`, `cacheScope`) on `tools/list` and `server/discover`.
- **Dogfood round 1** ([record](../research/2026-11-22-dogfood-1.md)): five real Claude Code development sessions (inspect, edit/verify/undo, build and wire, DAT + session notes, boundaries) in a disposable project.
  - All tasks succeeded and every boundary held (H3, R1, G2).
  - Fixed and regression-tested: the `get_parameters` page-filter `INTERNAL_ERROR`; `get_errors` silence on never-cooked operators (`never_cooked` and `cook_count`, without forcing cooks).
  - Added: a `names` filter for `get_parameters`, and `inputs` and `x`/`y` per `list_operators` row.
  - Friction and missing capabilities are recorded (network summary tool, grouped undo, denial wording).
  - Verified on candidate builds from the uncommitted tree (see the [validation index](validation-index.md)). On `a414f3c9…`: 1A, 1B (113 checks), gate, onboarding and the full five-task dogfood pass 2, where every boundary held. On `1625ec54…` (never-cooked change): 1B 115/115 and dogfood `inspect` pass 3. **On the review-8 candidate `2dac3a27…`:** every suite passes, including dogfood pass 4 under full agent isolation ([review 8](../research/2026-11-22-dogfood-review.md)).
- **Tests and checks:**
  - 231 unit tests on CPython 3.11.8 (2 skipped) and 248 on TD Python 3.11.10 (1 skipped).
  - Gate suite 49/49 + 98/98; onboarding 5/5 + 45/45.
  - Doc checker and secret scan pass.
  - **Packaged component** (`2a7e72f`, tox `9a063083…`): 1A, 1B, gate and live onboarding suites all pass ([validation index](validation-index.md)).

- **Phase 2A increment 1: project memory** ([record](../research/2026-11-22-phase-2a-memory.md), [continuity §7](../architecture/continuity-and-memory.md#7-phase-2a-increment-1-what-is-implemented-2026-11-22), [review 9](../research/2026-11-22-phase-2a-review.md)):
  - **Create Project Memory** makes an optional, user-created `tdflowProject` COMP (embedded authority, `tfp_` id). **Claim Project Memory** accepts its current records after an edit, an older save or a copy.
  - Four tools: `tdflow_task_list`, `tdflow_task_checkpoint`, `tdflow_handoff_create`, `tdflow_handoff_resume` (tool surface stays 1; Skill **1.1**).
  - The audit log is the ledger. Edited, missing, foreign or invalid records make memory read-only for agents until the user claims. Two authorities are refused.
  - Checkpoints and handoffs carry server-verified facts (audited changes, a scope fingerprint).
  - Claims are tagged against the caller's audited changes and the live project; the new `input` kind came from dogfooding.
  - Resume reports out-of-band edits by operator path.
  - Tell Your Coding Agent has a Resume section (counts only).
  - **Verified in TD** on candidate `d2a9e077…`:
    - memory suite 14/14 + 55/55 (ADR-0012 gates and the ADR-0013 stale-write gate);
    - a real two-agent Claude Code handoff with an out-of-band edit detected (7/7 + 32/32).
  - **Memory access** derives from operator scopes and role, **conditionally accepted (owner D18)**. Dedicated memory permissions are required before agents can write authoritative ICM, decisions or Git-backed docs.
  - **Not verified:** repo mode.
- **Phase 2A increment 2** ([record](../research/2026-11-23-phase-2a-increment-2.md), owner D18):
  - **Restart persistence verified in TD:** after a real restart from the saved `.toe`, records, `Projectid`, storage identity, audit chain and fingerprints are intact, and MCP stays off.
  - **Restoration:** a record written after the last save is reported missing (memory read-only) and is recovered through Claim Project Memory.
  - **Project identity verification:** a `tdflowProject` with another `Projectid` is read-only until claimed; this also holds with zero records. A truncated ledger fails closed.
  - **Frame hitch:** chunked fingerprints and a 1000-operator walk bound; frame max 7.1 ms on a 2000-operator scope.
  - Status is labelled `agent_reported`.
  - **Hang:** a test-only stall watchdog runs in every in-TD suite. Two more two-agent dogfood runs and all suites showed no stall. The original hang is unexplained and unreproduced.
  - Verified on candidate `68e1f1f9…`: memory + restart 26/26 + 99/99; 1A 49/49 + 69/69; 1B 43/43 + 115/115; gate 46/46 + 47/47. [Review 10](../research/2026-11-23-phase-2a-increment-2.md#independent-review-10): no High findings, all fixed.

## Not implemented (nothing below exists)
- **Withheld by design:**
  - approvals and the approval flow (2B);
  - arbitrary, raw or Code Mode Python execution (3, approval-gated);
  - project save.
- **Later phases:**
  - the governance dashboard (2B);
  - repo-mode project memory, `tdflow-project.json`, the `Projectid` link check and mode migration (later 2A increments);
  - task, decision and ICM record types (`tdflow_memory_get/put`), archiving;
  - a dashboard UI for choosing data locations beyond the `Datalocation` parameter (2B).
- **Not yet:**
  - a 1.0 release (packages are `.dev` development builds);
  - dev-mode file-synced DATs.

## Not observed / unverified
- **CI:** no GitHub Actions run has been observed for any commit ([B3](roadmap.md#open-blockers-and-owner-actions)).
- **Other clients:** Codex CLI, Cursor and VS Code Copilot connections are documented only, with no live test. Codex facts came from a summarised documentation fetch.
- **Literal paths not reproduced in TD:** network, cloud-synced and read-only folders are unit-tested with injected classifiers. Windows junction rejection for Skills is unit-tested; the in-TD round-trip did not create junctions.
- **Policy branch coverage:** not measured (no coverage tool installed).
- **Hands-on TDMCP comparison:** needs TD 2025.33230+.
- **Literal mouse drag:** not automated; `loadTox`, the same load path, is tested.

## Commit record
- `82b64f1`, `b70000a`, `066dbd0`, `380d317`: Phase 0. `main` = `066dbd0`.
- `377841c`, `ec259ed`: S5. `591b01e`, `3d0081e`: S6. `e680b47`, `9939af9`: Phase 1A. `e15ec55`, `a4660cd`: Phase 1B.
- `2a7e72f`: Phase 1C + audit worker + project-local storage (review 7 fixes). Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `2a7e72f…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Package `dist/TDflowMCP-0.2.0.dev1-2a7e72f361` (tox `9a063083…`) built from a clean worktree of that commit; all four in-TD suites pass on it. This record was added by the follow-up commit. CI: **not observed**.
- `99a10f4`: B6 decided (continuation rejected, preserved as a spike), dogfood round 1 with fixes, review 8, ADR-0014 proposed. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `99a10f4…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate builds from the pre-commit tree (`2dac3a27…`); no package was built for this commit. This record was added by the follow-up commit. CI: **not observed**.
- `fe46568`: owner D17 (ADR-0014 Conditionally Accepted, stage 1; audit performance closed) and **Phase 2A increment 1** (project memory, checkpoints, verified handoffs), review 9. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `fe46568…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate `d2a9e077…` built from the pre-commit tree; the commit changes only tests and docs relative to that build. No package was built. This record was added by the follow-up commit. CI: **not observed**.
- `930154b`: owner D18 (memory permissions, conditional, ADR-0013) and **Phase 2A increment 2** (restart persistence, project identity, restoration, chunked fingerprints, stall watchdog), review 10. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `930154b…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate `68e1f1f9…` built from the pre-commit tree; the commit adds only docs and test-record changes relative to it. No package was built. This record was added by the follow-up commit. CI: **not observed**.

## Next (owner decisions first)
1. **Phase 2A increment 3:**
   - repo mode with `tdflow-project.json`, the repository `Projectid` link check and migration (the remaining ADR-0013 gates);
   - the dogfood friction: default operator placement, handoff status, per-parameter drift.
2. **Hang:** keep the stall watchdog running in every suite; analyse any dump.
3. **ADR-0014 stage 1** (approved, D17): isolated candidate build/test bound to the `.tox` hash, plus a manual promotion recorded by the new A. Not started.
4. **B3:** an observed CI run.
5. **Remaining review-6 follow-ups:** bind/export propagation; pairing DoS mitigation. Checkpoints are now anchored in private storage outside the project.
