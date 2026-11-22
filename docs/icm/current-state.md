# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev`. Phase 1B (secure MCP core) is complete** (owner D12). The packaged development build passes its in-TouchDesigner security and functionality acceptance suite on TouchDesigner **2025.32440**, after independent review 6. Phase 1A stays verified on the same build. This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- **Repository:** private `jlopesmo/TDflowMCP`. `main` = `066dbd0` (Phase 0 foundation), **unchanged**. Development continues on `dev`.
- **ADRs:**
  - 0002, 0003, 0006, 0007, 0009 and 0010: Conditionally Accepted, with Phase 1 gates recorded.
  - **0012 and 0013: Conditionally Accepted** (owner D12).
  - 0011: still Proposed (needed before 1C).
  - **ADR-0006 write-latency gate: open** (≈ 5.3 ms per audit append with `synchronous=FULL`; owner decision).
- **Phase 1A** ([record](../research/2026-11-22-phase-1a-component.md)): the component, embedded transport, bind gate and Setup Assistant. Re-verified on the 1B build: 49/49 + 69/69; real default browser 13/13 + 5/5.
- **Phase 1B** ([record](../research/2026-11-22-phase-1b-mcp-core.md), [review 6](../research/2026-11-22-phase-1b-review.md)), in the development build `build/TDflowMCP.tox` v`0.2.0.dev1` (tool surface `1`, SHA-256 `52d039eb…`, git-ignored, not a release):
  - **Policy:** server-side deny-by-default CRUD+execute scopes with hard denies:
    - TDflowMCP instances and their subtrees, including reads;
    - on ancestors: delete, rename, flags, extensions and cloning;
    - `tdflowProject`, `/sys`, `/local`, `/ui`, and anything outside `Projectroot`.
  - **Classification:** a creation **allowlist**. Expressions, Python, reference and file parameters, wires into DATs, and DAT text not tagged `tdflowdata` by the user are all `execute`, which is denied. **No arbitrary Python execution exists.**
  - **Identity:** salted-hash tokens, created and revoked only from a browser session paired by approval **inside TD**; CSRF on admin reads and writes; server-side logout.
  - **Durable SQLite audit:** intent before and outcome after every call; hash chain plus keyed checkpoints; tamper detection; **fail closed** (no audit, no call); a failed outcome write suspends all agents; session grouping and notes persist across a TD restart.
  - **Kill switch** (`Suspendagents`): rejects every call, including requests already queued.
  - **Enable MCP:** only after its preconditions pass.
  - **MCP protocol:** 2026-07-28 stateless and the legacy `initialize` flow; conformance tests.
  - **Tools:** 16 (14 core plus session note and session list), every call checked by policy and audited.
  - **Verified undo:** for set parameters, create, delete, connect, disconnect and DAT text (snapshot). `UNDO_CONFLICT` after human, other-agent or restart interleaving (S4b).
  - **Final in-TD acceptance:** **41/41 + 110/110** over two launches (`mcp`, `mcp_restart`); TD exit code 0; no listener left; other TD processes untouched.
- **Spikes:**
  - S4 (undo) and **S4b** (stack introspection, interleaving, ids, save/reload) ([S4 record](../research/2026-11-22-s4-undo-semantics.md));
  - **S7** (paused timeline).
- **TDMCP comparison:** documentation only ([record](../research/2026-11-22-tdmcp-comparison.md)). TDMCP requires 2025.33230+, so nothing was downloaded.
- **Tests and checks:**
  - **204 unit tests** pass on CPython 3.11.8 (2 skipped) and TD Python 3.11.10 (1 skipped; that run includes the `jsonschema` cross-check).
  - The doc checker passes.
  - The **secret scan** (new CI step) finds nothing in the repository.

## Not implemented (nothing below exists)
- **Withheld by design:**
  - approvals and the approval flow (2B);
  - arbitrary, raw or Code Mode Python execution (3, approval-gated);
  - project save.
- **Later phases:**
  - the governance dashboard (2B);
  - checkpoints, handoffs and memory tools (2A);
  - client connection instructions, Skills, `.tox` packaging and releases (1C).
- **Dev mode:** file-synced DATs are not implemented.

## Not observed / unverified
- **CI:** no GitHub Actions run has been observed for any commit ([B3](roadmap.md#open-blockers-and-owner-actions)). The new secret-scan step has run locally only.
- **Live coding-agent session:** a live Claude Code (or other client) session against the endpoint is a 1C item. The acceptance suite uses its own MCP client for both protocol eras.
- **Policy branch coverage:** not measured (no coverage tool installed).
- **Hands-on TDMCP comparison:** needs a machine with TD 2025.33230+.
- **Literal mouse drag:** not automated; `loadTox`, the same load path, is tested.
- **Cloudflare / TDMCPSkills:** facts remain documentation-level.

## Commit record
- `82b64f1`, `b70000a`, `066dbd0`, `380d317`: Phase 0. `main` = `066dbd0`.
- `377841c`, `ec259ed`: S5. `591b01e`, `3d0081e`: S6. `e680b47`, `9939af9`: Phase 1A.
- Phase 1B commit: recorded by the follow-up commit after the push is observed. CI: **not observed**.

## Phase 1C prerequisites (next)
1. **Owner decisions:**
   - Accept or revise ADR-0011 (agent integration and Skills) (B5).
   - Decide the ADR-0006 latency gate: keep `synchronous=FULL`, use WAL `NORMAL`, or batch read events.
2. **Process:** an observed CI run (B3) with the new secret-scan step.
3. **1C engineering:**
   - the client registry with support levels (item 20);
   - "Tell Your Coding Agent" connection instructions with a `${TDFLOW_TOKEN}` placeholder, plus a **live Claude Code session** against a disposable project (item 21; also ADR-0003's live-client gate);
   - the Skills bundle with assisted writes (22);
   - version management (23);
   - `.tox` packaging and the install guide (24).
4. **Follow-ups from review 6** (documented residual risks):
   - anchor checkpoints outside `Datadir` (e.g. DPAPI);
   - bind and export propagation;
   - mitigating denial of service on pairing.
