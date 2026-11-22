# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev`. Phase 1A is complete** (owner GO, D11): a development build of `TDflowMCP.tox` passes its in-TouchDesigner acceptance suite and a real default-browser run on TouchDesigner **2025.32440**, the minimum and tested build (D10). No TD upgrade is needed. This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- Private repository `jlopesmo/TDflowMCP`. `main` = `066dbd0` (Phase 0 foundation), **unchanged**. Development continues on `dev`.
- **ADRs:**
  - 0002 (embedded loopback transport): Conditionally Accepted; revised after S6; component integration verified.
  - 0007 (source-first build): Conditionally Accepted; gates S2 and S3 passed.
  - 0010 (Setup Assistant): Conditionally Accepted; Phase 1A acceptance passed.
  - 0011–0013: still Proposed.
- **Spikes (all in disposable, text-generated projects only):**
  - S5: the 2025.32440 *Web Server DAT* is unsafe (it binds `0.0.0.0`) and is never used there.
  - S6: the embedded Python transport is safe on 2025.32440.
  - **S2:** source-first build.
  - **S3:** in-TD runner.
  - **S4:** undo semantics: everything except DAT text is undoable inside a block; nothing outside one.
  - **S7:** an independent local timeline keeps the component serving while the project timeline is paused.

  Records: [S4](../research/2026-11-22-s4-undo-semantics.md), [S5](../research/2026-11-22-s5-compatibility-2025.32440.md), [S6](../research/2026-11-22-s6-embedded-python-transport.md), [Phase 1A](../research/2026-11-22-phase-1a-component.md).
- **Development build** (`tools/build_tox/build.py`): `build/TDflowMCP.tox` v`0.1.0.dev1`, SHA-256 `46eab026c8ee87e847c12bc057b00d83313b053d22df265907738fb7e8d43f8b`, built with TD 2025.32440. The `.tox` is **git-ignored and not a release artifact**; rebuild it from source with one command.
  - **Embedded:** 11 core modules, the TD adapter, the Setup page, troubleshooting data and a hash manifest.
  - **Parameters:** 15 custom parameters.
  - **Reproducibility:** the source digest is reproducible; the binary differs only by TD metadata.
- **Phase 1A component** (verified by the in-TD suite, 48/48 + 69/69, and the real-browser run, 12/12 + 5/5):
  - **States and parameters:**
    - Controller states Off · Starting · Setup · Stopping · Error. Enabled and Suspended are reserved and unreachable.
    - Parameters Open Setup / Stop Server / Run Diagnostics / Copy Tell Agent; dynamic Status, Status Detail, Setup URL, Port Status, Compatibility, Tell Your Agent; Port 9480.
  - **Listener safety:**
    - No listener before Open Setup.
    - 503 until the loopback-only binding is verified.
    - LAN not reachable; re-verification every 10 s.
    - Conflict detection, including another component in the same project; the port is never changed silently.
  - **Threading and robustness:**
    - Main-thread-only TD operations.
    - Queue timeouts (504, never dispatched) and error recovery.
    - Stop and restart; stop during Starting; idle stop.
    - Extension re-init; deletion (listener gone in 0.01 s); project-exit handler.
    - Serving continues with the project timeline paused.
  - **No MCP, no code execution:** `/mcp` returns `503 MCP_DISABLED` for every request; there are no write endpoints and no code-execution routes.
  - **Discovery:** `/.well-known/tdflowmcp` returns only `{product, version}`.
  - **Secrets scan:** no secrets or paths in parameters, the agent text, diagnostics, HTTP responses or the TD log (failure path included).
  - **Failure reporting:** browser-launch and clipboard failures are reported in Status Detail.
- **Embedded Setup Assistant:**
  - Read-only status, compatibility and dependency/port diagnostics, troubleshooting guidance.
  - Dynamic Tell Your Coding Agent setup and repair text, with Copy, served from assets inside the `.tox`.
  - Opened in the real default browser from Open Setup.
- **142 unit tests pass** on CPython 3.11.8 and on TD's bundled Python 3.11.10 (1 skipped: non-Windows only).
- **Doc checker** passes.
- **Independent reviews:** review 4 (transport) and [review 5](../research/2026-11-22-phase-1a-review.md) (component: no High; all findings fixed or mitigated).

## Not implemented (nothing below exists)
- **MCP core:** protocol layer, policy engine, identity/token store, durable audit store, pairing/admin session, Enable MCP / Suspend Agents, MCP tools.
- **Later phases:** client connection instructions, Skills, continuity services, `tdflowProject`, dashboard.
- **Releases:** no release packaging, no committed `.tox`.
- **Dev mode:** file-synced DATs (ADR-0007 §3) are not implemented; builds embed the text.

## Not observed / unverified
- **CI:** no GitHub Actions run has been observed for any commit (no GitHub integration here; [B3](roadmap.md#open-blockers-and-owner-actions)).
- **Literal mouse drag:** a mouse drag of the `.tox` was not automated. `loadTox`, the same component-load path, was tested.
- **S4 open cases:** undo interleaving with user edits, and after save/reload.
- **Other environments:** newer TD builds and macOS are untested; S1 (Web Server DAT comparison) is optional.
- **Cloudflare:** the codemode/mcp page could not be fetched first-hand.
- **TDMCP / TDMCPSkills:** facts are documentation-level only.

## Commit record
- `82b64f1`, `b70000a`, `066dbd0`, `380d317`: Phase 0. `main` = `066dbd0`.
- `377841c`, `ec259ed`: S5 and 1A prep.
- `591b01e`, `3d0081e`: S6 embedded transport.
- Phase 1A commit: component, builder, in-TD tests, S4/S7, review 5. Recorded by the follow-up commit after the push is observed. CI: **not observed**.

## Phase 1B prerequisites (next)
1. **Decisions (owner):**
   - Accept or revise ADR-0012 (session logs) and ADR-0013 (`tdflowProject` hard deny) before 1B ([B5](roadmap.md#open-blockers-and-owner-actions)).
   - Decide whether to keep the in-memory transport audit ring until the 1B audit store exists.
2. **Undo:** a design for DAT-text inverse snapshots (S4), plus the S4 interleaving and restart tests.
3. **Engineering:** policy engine, identity/token store with hashed tokens, the audit store (fail-closed, flood aggregation), the pairing/admin session, Enable MCP / Suspend Agents, then the protocol layer and the 14 core tools, each under main-thread undo blocks.
4. **Process:** an observed CI run (B3) and CI secret scanning (item 6).
