# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev`. Phase 1C (agent onboarding) is complete in the development build, together with the ADR-0006 audit worker and project-local storage** (owner D13). A live Claude Code session connected using only the generated instructions, and the packaged component passes its in-TouchDesigner acceptance suites on **2025.32440**. Phases 1A and 1B are re-verified on the same build. This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- **Repository:** private `jlopesmo/TDflowMCP`. `main` = `066dbd0` (Phase 0 foundation), **unchanged**. Development continues on `dev`.
- **ADRs:**
  - 0002, 0003, 0006, 0007, 0009, 0010, 0012 and 0013: Conditionally Accepted, with their gates recorded.
  - **0011: Conditionally Accepted** (owner D13); its 1C gates passed.
  - **ADR-0006, revised:** main-thread latency gate passed with the audit worker. The MCP-latency tradeoff awaits owner acceptance ([B6](roadmap.md#open-blockers-and-owner-actions)).
- **Phase 1A and 1B:** as recorded in the [1A record](../research/2026-11-22-phase-1a-component.md) and the [1B record](../research/2026-11-22-phase-1b-mcp-core.md). Re-verified on the 1C build after [review 7](../research/2026-11-22-phase-1c-review.md): **1A 49/49 + 69/69; 1B 43/43 + 110/110**.
- **Audit worker** ([record](../research/2026-11-22-audit-worker-and-phase-1c.md)):
  - One SQLite worker thread, FIFO; `synchronous=FULL` + WAL kept.
  - TD's main thread never waits for disk I/O. MCP and admin requests are orchestrated on handler threads; TD steps run on the main thread via `call_main`.
  - Durable intent before execute, durable outcome before success. Fail closed on overflow, timeout, worker failure or lost durability; agents are suspended when an outcome cannot be recorded.
  - In TD:
    - OnFrame p99 ≤ 1.6 ms under load and with a simulated 50 ms disk; frame max 23.6 ms (1B: 46.5 ms).
    - Crash, kill-switch race, overflow, injected outcome failure and restart all pass.
  - **MCP latency is higher:** ≈ 83–152 ms sequential p50, against 55 ms on a clean 1B build. Cause: TouchDesigner GIL handoffs, measured.
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
- **Tests and checks:**
  - 231 unit tests on CPython 3.11.8 (2 skipped) and 248 on TD Python 3.11.10 (1 skipped).
  - Gate suite 49/49 + 98/98; onboarding 5/5 + 45/45.
  - Doc checker and secret scan pass.
  - **Packaged component** (`2a7e72f`, tox `9a063083…`): 1A, 1B, gate and live onboarding suites all pass ([validation index](validation-index.md)).

## Not implemented (nothing below exists)
- **Withheld by design:**
  - approvals and the approval flow (2B);
  - arbitrary, raw or Code Mode Python execution (3, approval-gated);
  - project save.
- **Later phases:**
  - the governance dashboard (2B);
  - checkpoints, handoffs and memory tools (2A);
  - a dashboard UI for choosing data locations beyond the `Datalocation` parameter (2B).
- **Not yet:**
  - the main-thread continuation that would cut MCP latency (proposed);
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

## Next (owner decisions first)
1. **B6:** accept the MCP-latency tradeoff of the audit worker, or request the main-thread continuation first.
2. **B3:** an observed CI run.
3. **Remaining review-6 follow-ups:** bind/export propagation; pairing DoS mitigation. Checkpoints are now anchored in private storage outside the project.
4. **Phase 2A** (continuity and memory) per the [roadmap](roadmap.md).
