# TDflowMCP — Current state

**Observed 2026-11-23 (re-verified after a PC restart), branch `dev`; the one-click connection (1.0.0rc3, owner D22) was observed 2026-11-24.** Phase 1C (agent onboarding) is complete in the development build, together with the ADR-0006 audit worker and project-local storage** (owner D13). **Phase 2A increment 1 (project memory, checkpoints, verified handoffs) is implemented and verified in TD** (owner D17). A live Claude Code session connected using only the generated instructions, and the packaged component passes its in-TouchDesigner acceptance suites on **2025.32440**. Phases 1A and 1B are re-verified on the same build. **Phase 2A increments 2 and 3 are implemented and verified in TD** (restart persistence and identity; repository-mode memory with both remaining ADR-0013 gates), and the earlier unexplained hang is explained and handled (a minimized window). This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- **Repository:** public `jlopesmo/TDflowMCP` (owner direction 2026-11-25, [ADR-0008](../decisions/0008-upstream-reuse-policy.md)). `main` was `066dbd0` (Phase 0 foundation) until PR #1 merged the frozen rc6 state as `5f86345` on 2026-11-25; the release commits follow ([Release 1.0.0](#release-100--2026-11-25)). Development continues on `dev`.
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
  - **Client registry:** `src/integrations/clients.json`. Claude Code is primary; Codex, Cursor and VS Code Copilot are documented only. (That is the 1.0.0 state. On `dev`, Codex is verified on Windows: see [1.0.1 release candidate](#101-release-candidate-one-click-codex-owner-d27).)
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
- **Tests and checks** (latest observed run: 2026-11-23, clean build `e79ed3fe…` of `c888d00`, after the PC restart; [recovery record](../research/2026-11-23-post-restart-recovery.md)):
  - 345 tests (`discover -s tests -t .`) on CPython 3.11.8 (2 skipped) and on TD Python 3.11.10 (1 skipped).
  - On the increment-3 candidate `273b185e…` (uncommitted tree): 1A 49/49 + 69/69; 1B 47/47 + 126/126; gate (gate, crash, restart) 46/46 + 47/47; memory + restart + repository 51/51 + 179/179; onboarding live 5/5 + 47/47.
  - Doc checker and secret scan pass.
  - After the PC restart, a clean rebuild of `c888d00` had the same source digest as the candidate tested before it ([recovery record](../research/2026-11-23-post-restart-recovery.md)).
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
  - **Hang:** a test-only stall watchdog runs in every in-TD suite. Two more two-agent dogfood runs and all suites showed no stall. The hang was unexplained at the time; **it is explained since 2026-11-23** (a minimized TouchDesigner window, below).
  - Verified on candidate `68e1f1f9…`: memory + restart 26/26 + 99/99; 1A 49/49 + 69/69; 1B 43/43 + 115/115; gate 46/46 + 47/47. [Review 10](../research/2026-11-23-phase-2a-increment-2.md#independent-review-10): no High findings, all fixed.
- **Phase 2A increment 3** ([record](../research/2026-11-23-phase-2a-increment-3.md), [continuity §8](../architecture/continuity-and-memory.md#8-phase-2a-increment-3-repository-mode-2026-11-23), [review 11](../research/2026-11-23-phase-2a-increment-3-review.md), owner D19):
  - **Repository mode:** task records as files in a folder inside the project's own folder tree (`tdflow/memory/`, with `tdflow-project.json`). The component then holds only the link and a derived index. File I/O never runs on TouchDesigner's main thread.
  - **Both remaining ADR-0013 gates passed in TD:** a mismatched `Projectid` blocks linking, reading, writing, claiming and migrating; a migration in both directions preserves every record and hash.
  - **Migration and re-link** on the paired Setup page: preview with a digest, then exactly that preview; audited before and after; refused while the ledger is not clean.
  - **Dedicated permission (owner condition D18):** writing in repository mode needs the capability `memory:repo:write`, issued only by the user at token creation. **Accepted by the owner (D20)**, with no authority over project ICM, decisions or Git-backed docs ([ADR-0013](../decisions/0013-project-memory-model.md#conditional-decision-on-memory-permissions-owner-d18-2026-11-22)).
  - **Untrusted folder contents:** files the audit log does not know are never used without the user's claim, cannot crash the tools, and cannot put text into server-labelled parts of an answer.
  - **Friction fixes:** default placement of created operators; an optional `status` on handoffs; the changed parameters named on resume. Skill **1.2**.
  - **Not verified:** a parent-folder link (`..`) in TD (unit-tested); a link on a cloud-synced or network folder.
- **The earlier "unexplained hang" is explained and handled** ([S9](../research/2026-11-23-s9-minimized-window.md)):
  - TouchDesigner 2025.32440 runs **no frames while its window is minimized** (measured: 0 frames in 4 s, also with independent time).
  - Before: timeouts, then the listener closed after 20 s and MCP turned off. Now: calls that need TouchDesigner are refused at once with `TDFLOW_NOT_COOKING` and the reason, nothing executes, the listener and MCP stay, and work continues when the window is restored. Verified in TD (1B `minimized.*` checks).
  - Windows only; elsewhere the earlier behaviour applies. While minimized, nothing that touches the project can run, and the `Suspendagents` parameter waits for frames (the dashboard's kill switch does not). The owner asked for background checks (D20): bind re-verification and the idle stop now run without frames (Phase 2B increment 2, below).
- **Phase 2B increment 1: approvals and the governance dashboard** ([record](../research/2026-11-23-phase-2b-increment-1.md), [security model §4](../architecture/security-model.md#4-approvals), owner D19):
  - **Approvals:** a call that runs code (`tdflow_set_parameters` expressions and reference parameters; **operator** role) or deletes more than 50 operators waits for the user and is executed only by the identical re-call, once, with targets and policy re-checked. Nothing waits on the main thread. Other code-running effects stay denied.
  - **Dashboard** at `/ui/` (paired admin session): overview, approval queue with complete arguments, live activity and audit search, sessions, agents and tokens, project memory, reports that state whether they verify against the keyed checkpoints, settings (mandatory audit shown as fixed; optional diagnostic log; data-location request). Its kill switch works while TouchDesigner runs no frames.
  - Skill **1.3**. Tool surface stays `1` (`tdflow_approval_status` is additive).
- **Phase 2B increment 2 (owner D20)** ([record](../research/2026-11-23-phase-2b-increment-2.md)):
  - **Minimized window:** while TouchDesigner runs no frames, the transport re-verifies the loopback binding every 10 s on a background thread (TD-free; anything but a positive verdict closes the listener) and enforces the idle stop. Idle now counts only meaningful activity (agent requests, admin actions), not page loads or automatic polling. Verified in TD (`minimized.BINDING_RE_VERIFIED_IN_THE_BACKGROUND`); the idle stop while paused is unit-tested.
  - **Permission editing** on the dashboard: add or remove allow and deny grants per agent, within the agent's role and the project root; audited first; pending approvals of that agent stop matching.
  - **Code-running changes with approval and staging:** code-DAT text, and the six Execute DAT types created switched off, with activation and every other Execute DAT parameter as approval-gated changes. Verified in TD end to end (`code.*`: the approved code runs only after the approved activation and stops after the approved deactivation; builders cannot toggle callbacks).
  - [Review 14](../research/2026-11-23-phase-2b-increment-2.md#independent-review-review-14): 0 High, 2 Medium, all addressed. Committed as `a656c75`.
- **Phase 3 increment 1: Code Mode** ([record](../research/2026-11-23-phase-3-code-mode.md), [ADR-0004 notes](../decisions/0004-code-mode-strategy.md#implementation-notes-phase-3-2026-11-23), owner D20):
  - `tdflow_execute_script`: **off until the user turns it on** (dashboard setting, private storage, audited); then the operator role, a grant on the declared scope and the user's approval of the exact text, every time. The script runs on a handler thread; its facade calls are normal audited calls, and the engine enforces the tool allowlist, the scope, the budgets and the kill switch per call. Not a sandbox, and every request says so.
  - Verified in TD on candidate `8c829556…` (`script.*`); [review 15](../research/2026-11-23-phase-3-code-mode.md#independent-review-15): 2 Medium, all addressed.
- **Phase 3 increment 2: experiments and project save** ([record](../research/2026-11-23-phase-3-experiments.md)):
  - `tdflow_experiment_create` / `_promote` / `_discard`: a tagged copy next to the original to try changes on; promote replaces the original (name, position, wiring) in one undo block; discard deletes only experiments. A copy is checked like reading every source operator and creating every copied one, with approval for any code it carries (hash-bound) and its executable operators switched off.
  - `tdflow_save_project`: approval only, with a grant on the project root.
  - Verified in TD on candidate `8476555f…` (`experiment.*`, `save.*`); [review 16](../research/2026-11-23-phase-3-experiments.md#independent-review-16): 3 Medium, all addressed.
- **Phase 4: `tdflow_describe_network`** (dogfood finding 5): one network level with wiring, errors, TouchDesigner's own cook times and heuristic hints; never cooks anything. Verified in TD (`network.*`). Skill **1.4**.
- **Phase 4 extensions and Phase 3 snapshots** ([record](../research/2026-11-23-phase-4-extensions.md)):
  - `tdflow_capture_top`: a TOP's image as MCP image content (bounded size, one per second), never in the audit log.
  - `tdflow_lease_scope`: an agent reserves a scope; other agents' changes and undos there get `LEASED`. The user can end leases on the dashboard; a lease stops counting when its holder loses its token or permission. Not the project root; renewal for at most an hour.
  - Inert snapshots (`inert: true` on experiments), restored by promote.
  - Code Mode benchmark measured (ADR-0004 gate): one script 1534 ms / 1978 bytes against 20 calls 1750 ms / 5613 bytes, plus one approval.
  - Skill **1.5**. Verified in TD on candidate `1b8657a0…`; [review 17](../research/2026-11-23-phase-4-extensions.md#independent-review-17): 5 Medium, all addressed. Not built: host-project source-first (TDXN interop evaluation).
- **Tests** on candidate `8c829556…` (uncommitted tree): 403 unit tests; 1B 48/48 + 178/178; 1A 49/49 + 71/71; gate 46/46 + 48/48; memory 51/51 + 182/182; onboarding live 5/5 + 48/48.
- **First-run UX redesign (owner D21)** ([record](../research/2026-11-23-first-run-ux.md)), version **1.0.0rc2**:
  - **Diagnostics:** automatic on drop-in, project load and reinit (no listener, store or file), and manual from Run Diagnostics on the first page. Separate Environment Ready / MCP Ready / MCP Enabled / Agent Connected states.
  - **Pages:** TDflowMCP, Agents, Web, Advanced. Next Step is context-aware.
  - **Coding Agent:** Auto (lookup without execution).
  - **TouchDesigner only:** Connect Agent and a clipboard-only token handoff kept out of Windows Clipboard History; native management of agents, scopes, tokens and approvals through the dashboard's backend.
  - **Web:** one-click sign-in for pages opened from TouchDesigner (single-use 30-s fragment ticket, 5-s read-only warm-up, fail-closed interception); pairing only for typed URLs.
  - **Verified in TD** on candidate `c11706ce…` and on the **packaged** `TDflowMCP-1.0.0rc2-132e8c3317` (tox `dfabfaca…`, commit `132e8c3`), including the TouchDesigner-only first run with a live Claude Code session and a restart from the saved project. [Review 18](../research/2026-11-23-first-run-ux.md#independent-review-18-security): 1 High, 3 Medium, all addressed.

- **One-click agent connection (owner D22)** ([record](../research/2026-11-24-one-click-connection.md), [ADR-0015](../decisions/0015-consent-based-client-connection.md), Conditionally Accepted), version **1.0.0rc3**:
  - **Connect Agent → Approve → start working** for Claude Code:
    - Auto discovery (read-only) and a plan in a TouchDesigner dialog. The approval creates the identity, puts the credential in the OS-protected store (Windows DPAPI) and writes one user-scope `tdflow` entry with a fixed `headersHelper`.
    - Enable MCP is turned on only when the approved plan says so; Next Step shows each stage.
    - **Agent Connected — Ready** appears only after an authenticated request that follows the configuration.
    - Repair Connection, Disconnect Agent (rollback), backups.
  - **The helper releases the token only after the listener proves the project's connect key** (HMAC over a fresh nonce; review 19). No token is in any file, command line, environment variable, parameter, DAT or log.
  - **Real-client validation**, with a disposable client home: the native suite passes 46/46 + 35/35 on candidate `57ad00af…` and on the **packaged** `TDflowMCP-1.0.0rc3-f033b72d48` (tox `ffbb3ea1…`; all six suites and the visual review pass on the package).
    - The configuration TDflowMCP wrote, used unmodified: Claude Code CLI 2.1.289 and the VS Code extension's engine 2.1.330 connect (`claude mcp list` ✔ Connected).
    - A live session works without any token step (`RESULT role=builder created=yes outside=denied`).
    - Suspend/resume; restart reconnects without setup; revocation (client reports HTTP 401); repair; a damaged client file fails cleanly; Disconnect removes exactly the entry.
    - No plaintext token in any file, including the saved `.toe`.
  - **The real dialog:** in TouchDesigner it opens, TouchDesigner stays responsive, and its button approves (`screens.*`).
  - **Other agents** (Codex, Cursor, VS Code Copilot): a manual fallback, with the reason stated. The live onboarding suite now exercises that path with the generic client.
  - **Browser:** the one-click ticket is retired. Pages opened from TouchDesigner pair through a TouchDesigner dialog with number matching (1B `web.*`).
  - **UX-01–07:**
    - diagnostics show Running… / Checks done / Last Check / counts;
    - every status line fits the default 416-px parameter window (26 characters; unit-tested; visual review);
    - client- and platform-specific instructions, with no Setup-page or contradictory steps.
  - [Review 19](../research/2026-11-24-one-click-connection.md#independent-review-19-security): 0 High, 4 Medium (port-squatter token harvest, PowerShell transcription, PowerShell 7 parent, disconnect order), all addressed. A usability review's findings were addressed or recorded.

- **rc4: the owner's restart finding, automated acceptance, corrections (owner D23)** ([record](../research/2026-11-24-rc4-restart-and-acceptance.md)), version **1.0.0rc4**:
  - **Root cause:** a misleading interface over intact data, not data loss.
    - After reopening, storage stays closed until a user action, and the UI reported the unopened registry as `0 agents` / `Ready: pulse Connect Agent`.
    - The owner's project data was inspected read-only (copies; names and counts only): 5 agents, 5 unrevoked tokens, the connection record, the DPAPI key, 80 audit events and the `tdflow` entry, all intact.
    - The new restart scenario reproduces the owner's exact screen on the rc3 package.
  - **Fix (UX-09):**
    - A read-only startup inspection of this project's private folder: the credential store, a non-secret `summary.json` with enabled agents and connected clients, and key files. It opens and creates nothing.
    - What the user sees: `Turn on Enable MCP.`, `1 agent · not loaded`, `(not loaded yet)`, and `Data: confirm (Advanced)` while a move or copy decision waits.
  - **UX-08:** the dialog shows a compact plan that fits TouchDesigner's fixed dialog, with the buttons visible; the full plan is in the viewer. Widening the dialog was tried and rejected (it clipped the buttons). Verified with the real dialog in TD.
  - **[Review 20](../research/2026-11-24-rc4-restart-and-acceptance.md#independent-review-20-storage-identity-startup-credentials):** 3 Medium, all addressed:
    - the hint now needs an enabled agent;
    - a waiting storage decision is shown;
    - an unsaved project carrying a saved project's identity is `COPIED` (pre-existing).

    Also: a pending connection record before the key is stored, and Repair never reuses a disabled agent.
  - **Automated acceptance** (native suite, four TouchDesigner launches with a disposable client home and real Claude Code clients): first run, restart, second restart, abrupt kill and recovery. The A–G matrix is in the record.

- **rc5: Copy Agent Prompt (owner D24)** ([record](../research/2026-11-24-copy-agent-prompt.md)), version **1.0.0rc5**:
  - **Copy Instructions** (TDflowMCP page) is renamed **Copy Agent Prompt**. It copies a short first message for a new coding-agent chat, built from the readiness state and allowlisted facts.
    - When ready, the agent uses the existing `tdflow` server, calls `tdflow_whoami`, gives a brief overview, checks memory counts when relevant and authorized, respects approvals, and changes nothing until given a task.
    - Otherwise it is a recovery message with the one next step, and it never claims a connection.
  - **Tell Your Coding Agent** shows `Prompt ready · connected`, `Prompt ready: copy it`, `Prompt ready (manual)` or `Not ready: <step>` (14 states, each at most 26 characters).
  - The full text is unchanged under View Full Instructions, the `tellagent` DAT and the Setup page.
  - **Review 21:** 0 High, 2 Medium (a manual "ready" without an enabled agent; another agent's request counted as connected), both fixed; Low findings addressed or accepted.
  - **A real Claude Code session started from the copied prompt alone** called `tdflow_whoami`, then inspected the project, reported its role and scope, and changed nothing (native suite `prompt.*`, [validation index](validation-index.md)).
- **1.0.0rc6 (owner D25, [ADR-0016](../decisions/0016-capabilities-page-and-permission-editing.md), [record](../research/2026-11-25-capabilities-and-final-polish.md)), verified on TouchDesigner 2025.32440:**
  - **Capabilities page:** a capability matrix (TDflowMCP support, each role, the selected agent; Available · Approval Required · Denied · Not Implemented · Protected) whose statuses match `Policy.evaluate`. Permission editing (role, scopes, repository-memory permission) through preview, digest and explicit confirmation of more access, audited, with the agent's open approvals cancelled in the same transaction; native/web parity verified in both directions.
  - **`tdflow_capabilities`:** a real Claude Code session asked "What can you do?" reported its own capabilities and how to open the page; Skill 1.6.
  - **Main page:** Run Diagnostics, Enable MCP, Suspend Agents first; `No agent: Connect Agent`.
  - **Browser sign-in:** numbered code lines and buttons `1`–`3` + Deny (rc5's dialog clipped the codes and hid Deny).
  - **Dashboard fix:** the `hidden` attribute is respected (the "not running frames" warning and Suspend/Resume were always shown).
  - Independent review 22: security 0 High, 2 Medium, 4 Low; usability 0 High, 7 Medium, 7 Low; all Medium fixed, Low fixed or accepted with reasons.
- **The 1.0 feature set is frozen** (owner D25, [roadmap](roadmap.md#10-feature-freeze-owner-d25-2026-11-25)): only release-blocking defects, documentation and required compatibility fixes until 1.0.

## Not implemented (nothing below exists)
- **Withheld by design:**
  - raw Python execution with full `td` access (`tdflow_execute_python`): not built, and disabled by default if it ever is;
  - wiring data into DATs, creating Script, network or file-system operators: not approvable;
  - project save.
- **Decided against:** a WebSocket or SSE event stream on the dashboard; it polls (D20 rationale in the [increment 2 record](../research/2026-11-23-phase-2b-increment-2.md#event-delivery-d20-item-4)).
- **Later phases:**
  - per-grant `allowed_op_types` and `param_denylist` editing (grants themselves are editable);
  - task, decision and ICM record types (`tdflow_memory_get/put`), archiving (adding an agent-writable record type reopens owner condition D18);
- **Not yet:**
  - dev-mode file-synced DATs.

## Not observed / unverified
- **CI before 2026-11-25:** no GitHub Actions run was observed for the commits recorded below as "CI: not observed". Runs have been observed since then ([validation index](validation-index.md), [Release 1.0.0](#release-100--2026-11-25)).
- **Other clients:** Cursor and VS Code Copilot connections are documented manual fallbacks only, with no live test. Client facts were re-checked against vendor documentation on 2026-11-24 ([client audit](../research/2026-11-24-one-click-connection.md#client-audit)). In the released 1.0.0 the same holds for Codex; on `dev` (1.0.1rc1) Codex is verified on Windows.
- **Codex, not observed by an agent** (1.0.1): the chat window of the Codex VS Code extension on the owner's profile with a model session. The owner ran that check and reported it passed (D28, [Issue #3](https://github.com/jlopesmo/TDflowMCP/issues/3#issuecomment-7175371704)); agents drove the engines directly.
- **Codex, not observed at all:** the Codex desktop app (not installed); Codex on macOS or Linux; what a Codex version too old for `http_headers_helper` does with the entry (reasoned, not run); whether a new chat in an already open VS Code window is enough without reloading it.
- **The Capabilities page on the owner's real profile** (rc6): automated with a stub API in headless Edge and against the real component API in the 1B suite; not opened by the owner yet.
- **The owner's real profile with the Claude Code VS Code chat UI** (including pasting the rc5 Copy Agent Prompt there; the same text was validated with the Claude Code CLI): not executed by an agent. It requires the owner's own approval in TouchDesigner ([checklist](../operations/release-candidate-1.0.md)). The extension's engine is validated against the written configuration.
- **macOS automatic setup** (Keychain, sh helper): implemented from documentation, **not validated**. Pre-existing: on macOS the private root is `~/TDflowMCP` with default permissions.
- **Constrained Language Mode** (Windows PowerShell): the helper fails closed (no HMAC without .NET). Not tested on a CLM machine.
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

- `c888d00`: push evidence for `930154b`. **Post-restart recovery (2026-11-23, owner D19):** nothing was lost; the tree was clean and level with `origin/dev`; a clean build of this commit (tox `e79ed3fe…`) passes every baseline suite ([record](../research/2026-11-23-post-restart-recovery.md)).

- `3a53e72`: post-restart recovery record and D19. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `3a53e72…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). CI: **not observed**.
- `1233815`: **Phase 2A increment 3** (repository-mode memory, link check, migration, the repository-memory capability, friction fixes) and the minimized-window handling (S9), reviews 11 and 12. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `1233815…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate `273b185e…` built from the pre-commit tree; a clean build of the commit (tox `a2250474…`, `source_dirty` false) has the same source digest `ffb75892…`. No package was built. This record was added by the follow-up commit. CI: **not observed**.
- `1d62848`, `6494c66`: **1.0.1**, the release commit and the merge of PR #4 into `main` (tagged `v1.0.1`); see [Release 1.0.1](#release-101--2026-11-25). CI runs #65–#68 passed. Package `dist/TDflowMCP-1.0.1-6494c66e17` (tox `873d9f7522bffcb9…`), published and verified.
- `2bb48e5`: **1.0.1rc1**, the one-click Codex connection on Windows (owner D27, ADR-0017), reviews 23. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `2bb48e5390…`, `refs/heads/main` = `b5db6118…` and `refs/tags/v1.0.0^{}` = `33af0828…`, both unchanged). Package `dist/TDflowMCP-1.0.1rc1-2bb48e5390` (tox `95e53f84ec961f21…`, zip `8aee93bc…`), built from a clean worktree; every suite passes on the packaged `.tox` ([validation index](validation-index.md#2026-11-25-101rc1-one-click-codex-commit-package-ci)). CI run #60: Windows passed, Ubuntu failed in one new test. `4a333ae`: CI publishes failing tests as annotations (run #61 named the test). `4d28dd9`: that test fixed, and the packaged-component record; CI run #62 passed on Windows and Ubuntu. Not tagged, not published.
- `33af082`, `b5db611`: **1.0.0**, the release commit (tagged `v1.0.0`) and its record; see [Release 1.0.0](#release-100--2026-11-25). CI runs #56–#59 passed.
- `36ce1e5`: **1.0.0rc6**, the Capabilities page, permission editing, `tdflow_capabilities`, the main-page order, the sign-in codes (owner D25, ADR-0016), review 22; **1.0 feature freeze**. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `36ce1e5628…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Package `dist/TDflowMCP-1.0.0rc6-36ce1e5628` (tox `67b393cebe04ef14…`, zip `25733376…`), built from a clean worktree; **every suite, the visual review and the browser checks pass on the packaged `.tox`** ([validation index](validation-index.md)). This record was added by the follow-up commit. Not tagged, not published. CI: **not observed**.
- `6c36460`: **1.0.0rc5**, Copy Agent Prompt (owner D24), review 21. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `6c36460f74…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Package `dist/TDflowMCP-1.0.0rc5-6c36460f74` (tox `84a83b7d71530dec…`, zip `dbfbebe2…`), built from a clean worktree; **every suite and the visual review pass on the packaged `.tox`**, including a live Claude Code session started from the copied prompt ([validation index](validation-index.md)). This record was added by the follow-up commit. Not tagged, not published. CI: **not observed**.
- `9372e70`: **1.0.0rc4** (UX-08 corrected), owner D23. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `9372e70c28…`, `refs/heads/main` = `066dbd0…`). Package `dist/TDflowMCP-1.0.0rc4-9372e70c28` (tox `eb2dcd7357bbe952…`, zip `807de04e…`), built from a clean worktree; **every suite and the visual review pass on the packaged `.tox`** ([validation index](validation-index.md)). Not tagged, not published. CI: **not observed**.
- `727a9db`: **1.0.0rc4** first commit (owner D23). Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `727a9dbd19…`, `refs/heads/main` = `066dbd0…`). Its package `TDflowMCP-1.0.0rc4-727a9dbd19` (tox `9496e504…`) passed every suite. The visual review of that package then showed the widened dialog clipping its buttons, so it is **superseded** by the next commit (UX-08 correction); it was not published. CI: **not observed**.
- `f033b72`: **1.0.0rc3**, the one-click agent connection (owner D22, ADR-0015), review 19 and a usability review. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `f033b72d48…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Package `dist/TDflowMCP-1.0.0rc3-f033b72d48` (tox `ffbb3ea1fa404d66…`, zip `6d094b8b…`), built from a clean worktree of that commit; **every suite passes on the packaged `.tox`**, including the real-client one-click connection ([validation index](validation-index.md)). This record was added by the follow-up commit. Not tagged, not published. CI: **not observed**.
- `132e8c3`: **1.0.0rc2**, first-run UX redesign (owner D21), review 18. Pushed to `origin/dev` with its evidence commit (push observed with `git ls-remote` in the next record). Packaged from a clean worktree; every suite passes on the package. CI: **not observed**.
- `348360a` / `ba6480a`: **1.0.0rc1** and its packaged acceptance. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `ba6480a…`, `refs/heads/main` = `066dbd0…`). CI: **not observed**.
- `47e47dc`: **Phase 4** (capture, leases, inert snapshots, benchmark), review 17. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `47e47dc…`, `refs/heads/main` = `066dbd0…`). Verified on candidate `1b8657a0…`. CI: **not observed**.
- `df0ea46`: **Phase 3 increment 2** (experiments, approval-only save), review 16. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `df0ea46…`, `refs/heads/main` = `066dbd0…`). Verified on candidate `8476555f…` built from the pre-commit tree. CI: **not observed**.
- `4c55ca0`: **Phase 3 increment 1** (Code Mode) and the Phase 4 network summary, review 15. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `4c55ca0…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate `8c829556…` built from the pre-commit tree. No package was built. CI: **not observed**.
- `a656c75`: **Phase 2B increment 2** (owner D20: background checks while minimized, permission editing, staged code-running changes), review 14. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `a656c75…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate `2397613a…` built from the pre-commit tree. No package was built. This record was added by a later commit. CI: **not observed**.
- `0dd5bfd`: **Phase 2B increment 1** (approvals with identical re-call, the operator role, the `/ui/` governance dashboard), review 13. Pushed to `origin/dev` and observed with `git ls-remote` (`refs/heads/dev` = `0dd5bfd…`, `refs/heads/main` = `066dbd0…`, so `main` is unchanged). Verified on candidate `84f76d69…` built from the pre-commit tree; a clean build of the commit (tox `788925fa…`, `source_dirty` false) has the same source digest `14dac47f…`. No package was built. This record was added by the follow-up commit. CI: **not observed**.

## Next (owner direction D22: one-click connection; D20: finish TDflowMCP)
The list below was written for rc6. Items 0, 2 and 4 are overtaken by [Release 1.0.0](#release-100--2026-11-25); the optional owner checks, macOS and the open owner questions remain.

0. **1.0.0rc6** ([checklist](../operations/release-candidate-1.0.md)), **feature-frozen**: rc5 plus the Capabilities page, the main-page order and the sign-in codes (D25). The optional owner check of the Capabilities page on the real profile. rc5: Copy Agent Prompt (D24). The owner's remaining acceptance steps on the real profile, now including pasting the copied prompt into a VS Code Claude Code chat. The restart finding is explained and fixed; everything else is automated. **macOS** validation. Owner decision: OAuth 2.1, the route to automatic setup for Codex, Cursor and Copilot ([ADR-0015](../decisions/0015-consent-based-client-connection.md#alternatives)).
1. **Phase 3 and 4:** done on `dev` (see above), except host-project source-first and file snapshots outside the project.
2. **1.0 release candidate** `1.0.0rc1` (commit `348360a`, package `TDflowMCP-1.0.0rc1-348360a56b`, all suites pass on the packaged `.tox`): [checklist](../operations/release-candidate-1.0.md), awaiting the owner's acceptance. Dogfood round 3 (Phase 3/4 tools) passed.
3. **ADR-0014 stage 1** (approved, D17), if it does not delay completion: isolated candidate build/test bound to the `.tox` hash, plus a manual promotion. Not started.
4. **Owner:** accept or reject the 1.0 release candidate; the release steps after that (PR `dev → main`, tag, clean build from the tag, publication) are the owner's.
5. **Owner question:** whether to add agent-writable task, decision or ICM records (reopens D18).
6. **B3:** an observed CI run. **Remaining review-6 follow-ups:** bind/export propagation; pairing DoS mitigation.

## Public release integration checkpoint — 2026-11-25

- **Owner authorization:** prepare and share the finished 1.0 Windows build through `main` and a GitHub Release. Repository visibility is already **public**; default branch currently `dev` (must switch to `main` for the public landing page). PR [#1](https://github.com/jlopesmo/TDflowMCP/pull/1) is open; release not merged, tagged or published.
- **Frozen implementation:** v1.0.0rc6, source commit `36ce1e5`, packaged `.tox` hash begins `67b393ce`; RC6 development/evidence through `8b15421`. The artifact is git-ignored; publish it as a release asset with SHA256SUMS, not as an unverifiable source-tree binary.
- **CI evidence now observable:** PR #1 run 42 failed Windows and Ubuntu unit tests for portable test assumptions, with doc/secret scan passed. `4275b8a` (Windows-only helper tests) and `fdbd857` (8.3 path identity) are release-gate test corrections; subsequent CI results pending. See validation index.
- **Outstanding:** latest TouchDesigner build separate test or explicit owner policy decision; observed passing CI; release asset; public README/default branch and licensing terms. `main` stays unchanged until those gates are closed.

## Release authorization and CI closure — 2026-11-25

- **Owner D26:** approved initial public **Windows-only v1.0.0** on **TouchDesigner 2025.32440**, with an explicit one-release exception to newer-build testing. No claims for macOS or newer TouchDesigner. The previous D25 feature freeze stands.
- **CI observed passing:** GitHub Actions [PR #52](https://github.com/jlopesmo/TDflowMCP/actions/runs/45665945561) and [push #51](https://github.com/jlopesmo/TDflowMCP/actions/runs/45665928515), both Windows and Ubuntu; docs checker, secret scan and unit tests pass after portable test fixes. Latest documentation-only head must also pass before merge.
- **Release artifact:** tagged-source v1.0.0 binary and checksums still require a clean local TouchDesigner build, package tests, GitHub Release asset publication and independent download verification. No binary has yet been published; release receipts belong in the validation index.
- **GitHub:** public repository; PR #1 proposes dev into main; default branch remains dev pending post-merge settings change. Public-source licensing remains deferred, but owner explicitly authorizes binary sharing for evaluation. `main` remains unchanged until actual merge.

## Release 1.0.0 — 2026-11-25

The two sections above are earlier checkpoints of the same day; this one supersedes their "pending" items. Evidence is in the [validation index](validation-index.md#2026-11-25-v100-release).

- **Merged:** PR #1 merged `dev` into `main` as `5f86345` (trees identical). Post-merge CI [run #55](https://github.com/jlopesmo/TDflowMCP/actions/runs/45666920078) passed on Windows and Ubuntu.
- **Version 1.0.0** (GitHub Issue #2): `1.0.0rc6` with the final version label. Also changed: the status API's `phase` (`1.0`), the component's node comment, the package manifest's release flag (`package_release.py --release-tag`), and the install guide's evaluation terms. No feature, tool, permission or security behaviour changed; the 1.0 feature freeze (D25) holds.
- **Verified on the release-commit tree** (TouchDesigner 2025.32440, Windows): 496 unit tests on both Pythons; 1B, 1A, gate, memory, onboarding and native suites, the visual review and the browser checks all pass with the same counts as rc6; the Version parameter shows `1.0.0 (tools 1)`.
- **Compatibility claimed:** Windows with TouchDesigner 2025.32440 only (owner D26). Newer builds and macOS: not verified.
- **Release commit:** `33af082` on `dev` and `main`; CI runs #56 and #57 passed on Windows and Ubuntu.
- **Tagged:** annotated `v1.0.0` on `33af082`.
- **Built from the tag** in a clean worktree on TouchDesigner 2025.32440: `TDflowMCP.tox` SHA-256 `466a1b5e577c695e5c924e031116173b68af4f8172799317e9c9d103931d5358`, source digest `348683a0…` (the tested candidate's). Every suite, the visual review and the browser checks pass on this packaged file.
- **Published:** GitHub Release [v1.0.0](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.0) with `TDflowMCP.tox`, `TDflowMCP.json`, `SHA256SUMS` and `INSTALL.md`. The page is public, and GitHub's own digests of the four assets equal the local hashes.
- **Default branch:** `main` (observed). `main` is protected against force pushes and deletion.
- **Public download verified outside the build network:** [verify-release run #2](https://github.com/jlopesmo/TDflowMCP/actions/runs/45738968626) downloaded the original four assets anonymously on Ubuntu and Windows with certificate checking. Both runners matched the recorded SHA-256 values and the annotated tag's source commit.
- **Not claimed:** compatibility beyond Windows with TouchDesigner 2025.32440; an open-source license.

## 1.0.1 release candidate: one-click Codex (owner D27)

**On `dev` only, version `1.0.1rc1`; observed 2026-11-25.** `main`, the tag `v1.0.0` and the published release are unchanged. Decision: [ADR-0017](../decisions/0017-codex-one-click-connection.md) (Conditionally Accepted). Record: [research](../research/2026-11-25-codex-one-click-connection.md). Evidence: [validation index](validation-index.md). Checklist: [1.0.1 candidate](../operations/release-candidate-1.0.1.md).

- **What Codex supports, verified before building:** the Codex CLI 0.160.0 and the engine of the Codex VS Code extension 26.1120.50957 (codex-cli 0.171.0-alpha.13.2) both run `mcp_servers.<id>.http_headers_helper`. Observed: started through `cmd.exe /Q /D /C` with a reduced environment; headers cached per connection; run again once after a 401; a non-zero exit sends no request, an empty object sends requests without credentials.
- **Implemented:**
  - **Coding Agent: Codex → Connect Agent → Approve → new Codex chat**, on Windows. One table `[mcp_servers.tdflow]` (`url`, `http_headers_helper`) in `~/.codex/config.toml`, edited as text and verified by re-parsing; every other byte kept; backup; exact undo by Disconnect Agent; Repair Connection.
  - The ADR-0015 vault and listener proof, in a separate credential slot; the Codex helper exits 1 and prints nothing on any failure. The Claude Code helper is byte-for-byte the released one (pinned by digest in a test).
  - Codex and Claude Code connected to one project at the same time, each with its own agent, key and audit records.
  - The connected client's name and version for legacy-era MCP clients (`Codex 0.160.0`).
  - The manual fallback remains for Codex outside Windows and for an extension older than 26.1117.
- **Verified in TouchDesigner 2025.32440 with real clients** (disposable client home and project): the Codex suite 55/55 + 64/64 with the extension's engine and the CLI's engine, next to a real Claude Code CLI; every other suite, the visual review and the browser checks pass on the same build (candidate `b37e1d94…`, source digest `486e33be…`).
- **Committed and packaged:** `2bb48e5` on `dev`. Package `dist/TDflowMCP-1.0.1rc1-2bb48e5390` from a clean worktree (tox `95e53f84ec961f21…`, the same source digest, zip `8aee93bc…`); **every suite, the visual review and the browser checks pass on the packaged `.tox`**. Not tagged, not published.
- **CI: passing.** Run #60 on `2bb48e5` and run #61 on `4a333ae` passed on Windows and failed on Ubuntu in one new unit test that used a Windows path (a test assumption; no product code involved). Fixed in `4d28dd9`: [run #62](https://github.com/jlopesmo/TDflowMCP/actions/runs/45702971854) passed on Windows and Ubuntu ([validation index](validation-index.md#2026-11-25-101rc1-one-click-codex-commit-package-ci)). CI now publishes failing tests as public annotations (`tools/ci_unittest.py`), because job logs need a sign-in.
- **Reviews:** independent security review 23 (0 High; 1 Medium and 8 Low fixed, also in the Claude Code path; 1 Low accepted and documented) and a usability review (all High and Medium addressed).
- **Also changed for Claude Code users** (wording and hardening, no new capability): `Start a new Claude chat.`; `Error in Claude settings` / `Can't edit Claude settings` with the reason in View Connection Details; the approval dialog ends with "Then: Copy Agent Prompt, paste it in a new Claude Code chat."; the chooser button says `Claude`; a racing writer can no longer change the approved action.
- **Not changed:** the 30 tools, tool surface `1`, Skill 1.6, permissions, approvals, the audit format, the capability list.
- **Known limits:** Windows only; not for Codex cloud tasks or remote sessions; the minimum Codex version is not documented by the vendor; a different `CODEX_HOME` for Codex than for TouchDesigner is not detected; hand-written inline or dotted `mcp_servers` layouts are refused, not converted.
- **Not observed:** see [Not observed / unverified](#not-observed--unverified) (the Codex chat window on the owner's profile and a model session; the desktop app; macOS and Linux).
- **Accepted and released:** the owner's hands-on check passed and the owner approved the release (D28). See [Release 1.0.1](#release-101--2026-11-25).

## Release 1.0.1 — 2026-11-25

Evidence is in the [validation index](validation-index.md#2026-11-25-v101-release). Steps not listed as done here were not done.

- **Owner acceptance (D28), reported by the owner, not observed by an agent:** with `1.0.1rc1` in a saved disposable project, OpenAI Codex selected and Connect Agent approved, the Codex chat in VS Code returned `tdflow_whoami` (Builder, `/project1/**`), and TouchDesigner showed `Agent Connected — Ready` and `Codex 0.171.0`. The owner approved releasing 1.0.1 and accepted [ADR-0017](../decisions/0017-codex-one-click-connection.md) as delivered for the tested Windows and Codex interfaces.
- **Version 1.0.1:** `1.0.1rc1` with the final version label; the status API's `phase` is `1.0.1`. No feature, tool, permission or security behaviour changed from the candidate.
- **Published-release check from outside the build network:** the workflow `verify-release` (`tools/verify_release.py`) downloads the public assets anonymously on GitHub-hosted runners and checks them against the SHA-256 of `SHA256SUMS` recorded when the package was built (it covers every asset), the manifest and the commit the tag points to. The build network still cannot make a trusted connection to GitHub's asset host.
- **Compatibility claimed:** Windows with TouchDesigner 2025.32440; Claude Code and Codex as in the [1.0.1 matrix](../operations/release-candidate-1.0.1.md#compatibility-matrix-tested-only). Not verified: newer TouchDesigner builds, macOS, Linux, the Codex desktop app.
- **Release commit:** `1d62848` on `dev`; CI runs #65 and #66 passed on Windows and Ubuntu.
- **Merged:** PR #4 into `main` as `6494c66` (tree identical to `1d62848`); CI runs #67 and #68 passed. `dev` was fast-forwarded to it.
- **Tagged:** annotated `v1.0.1` on `6494c66`. The tag `v1.0.0` and its release are unchanged.
- **Built from the tag** in a clean worktree on TouchDesigner 2025.32440: `TDflowMCP.tox` SHA-256 `873d9f7522bffcb9d86618ccea4dfeb90d9b820063738f975944a1775c832532`, version `1.0.1`, source digest `5801a93e…` (the tested candidate's). Every suite, the visual review and the browser checks pass on this packaged file.
- **Published:** GitHub Release [v1.0.1](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.1) with `TDflowMCP.tox`, `TDflowMCP.json`, `SHA256SUMS` and `INSTALL.md`, marked latest. GitHub's own digests of the four assets equal the local hashes.
- **Public download verified from outside the build network:** [verify-release run #1](https://github.com/jlopesmo/TDflowMCP/actions/runs/45726797505): on `ubuntu-latest` and `windows-latest`, an anonymous download of the four assets passed against the recorded hashes and the tag's commit.
- **Repository:** the default branch is `main`, protected against force pushes and deletion (observed before the merge; not changed).
- **Issue #3:** closed after the verification above. [Issue #2](https://github.com/jlopesmo/TDflowMCP/issues/2) tracks the v1.0.0 download receipts.

## Versioned downloads — 2026-11-25

- **Handoff audit:** `main` (the default branch) and `dev` matched at `1db52415943666419b0c68658dcdfe5d805e28f2`. Both tags still pointed to their recorded release commits. GitHub's public API showed both releases published, v1.0.1 as Latest and the four original assets on each. The v1.0.1 download workflow's Windows and Ubuntu jobs were confirmed successful.
- **Owner-approved packaging:** tagged packages include the primary `TDflowMCP-vX.Y.Z.tox` and an identical `TDflowMCP.tox`. Local tests checked both filenames, the manifest and checksums, ZIP contents and verification using fixture bytes. [Decision](../decisions/0007-source-first-build.md#decision) and [validation](validation-index.md#2026-11-25-versioned-download-maintenance).
- **Published and verified:** v1.0.0 and v1.0.1 each offer a versioned `.tox`, the unversioned compatibility download and `SHA256SUMS-versioned`. On Windows and Ubuntu, anonymous downloads confirmed byte equality and matched the original hashes for both releases. The README recommends the verified versioned v1.0.1 download. [Validation](validation-index.md#2026-11-25-versioned-download-maintenance)

## v1.1 developer capabilities — 2026-11-26

The v1.1 candidate extends the existing parameter and DAT tools with Python syntax checks, literal operator reference validation, DAT diffs and source hashes, staged Execute DAT authoring and source-aware approvals. Live Claude Code and both Codex engines verified the preview workflow. The owner approved requiring an owner-locked TOP before capture or resolution reads. The `1.1.0rc2` package passed 96/96 in-TD and 286/286 harness checks on TouchDesigner 2025.32440. The tests observed no additional cooks for eligible locked output, refused unlocked output or direct and inherited bypass. Source now carries the 1.1.0 release version for integration. The [v1.1 record](../milestones/v1.1-developer-capabilities.md) lists scope and release gates. [Validation](validation-index.md#2026-11-26-v11-developer-capabilities)

Local `dev` records the verified authoring and DAT source changes at `0cd5d616236355ed65f2cd43f7ea25d9683870e6`. The source-guard package also passed the project-memory and full audit gates. At this checkpoint GitHub `main` and `dev` both resolved to `65f0acc04cd956d5dfa73b97e4b34421c47bf4ee`, and the existing annotated release tags resolved to their prior objects. [Validation](validation-index.md#2026-11-26-v11-developer-capabilities)

Post-restart validation recovered matching local and GitHub `dev` at `222e26e69c8cc4fa47d85846a966cbb50ac7ec99`. [CI #81](https://github.com/jlopesmo/TDflowMCP/actions/runs/45768541400) passed on Windows and Ubuntu. The independently reviewed guard limits TOP image and resolution reads to eligible locked outputs on Windows with TouchDesigner 2025.32440. The final recovery candidate passed 103/103 in-TD and 288/288 external checks, including an explicit Python callback counter and refusals when the runtime or lock and bypass state is ineligible. [Validation](validation-index.md#2026-11-26-v11-developer-capabilities)

### v1.1.0 release

The reviewed `dev` head `516f012ad0e4a2b598b7602c36bc4b5e4f9a7c9f` merged into `main` as `2a508e5fb5977770ae9a818496671f2b85776a2a`. The trees matched. The annotated `v1.1.0` tag points to that merge commit. The clean tagged `.tox` passed every release package suite on Windows with TouchDesigner 2025.32440. [Release PR #7](https://github.com/jlopesmo/TDflowMCP/pull/7) · [Validation](validation-index.md#2026-11-26-v11-developer-capabilities)

The public release includes `TDflowMCP-v1.1.0.tox`, the identical `TDflowMCP.tox`, `TDflowMCP.json`, `SHA256SUMS` and `INSTALL.md`. Windows and Ubuntu runners independently downloaded and verified all five assets. The earlier release tags and all 12 prior asset IDs, sizes and GitHub digests matched their pre-release values. [Release](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.1.0) · [Hosted verification](https://github.com/jlopesmo/TDflowMCP/actions/runs/45775031074)

Script CHOP, SOP and TOP lifecycle authoring remains deferred pending an owner-approved staging and callback ownership design. Approved Python runs in TouchDesigner with host access. Compatibility validation covered Windows and TouchDesigner 2025.32440 under the release-specific owner decision.

## v1.2 component authoring

The four-scenario audit ran through the published v1.1.0 package on Windows with TouchDesigner 2025.32440. Existing MCP tools built networks, ran approved Python and repaired a broken output. Custom control definition, native component output exposure and operator-type discovery were the main gaps. [Baseline matrix](../milestones/v1.2-component-authoring.md#verified-v110-baseline) · [Validation](validation-index.md#2026-11-26-v12-component-authoring)

The v1.2.0 integration build passed the four component scenarios and governance checks on the same build. It exposes local operator discovery, bounded custom control definition and recovery, and an Out TOP connector on a Base COMP. The owner connects external consumers in TouchDesigner. The scoped project produced changed pixels through that connector when controls changed, and an edited custom parameter drove approved Python logic. [Candidate evidence](validation-index.md#2026-11-26-v12-component-authoring)

After the restart, the canonical checkout and GitHub `dev` matched at `0b2ed4c27775e0366322ed4392ec71a3f51ce138`. The saved integration binary predated the final Copy Agent Prompt wording. A clean rebuild of the committed checkpoint passed all four workflows, the governance checks and the required candidate regressions, including live Claude Code and Codex. The owner reconfirmed the v1.2.0 TouchDesigner compatibility exception. [Recovery and candidate verification](validation-index.md#post-restart-v120-candidate)

The corrected PR candidate applies per-control grant checks to custom definition edits, actual page sorts and snapshot undo. The page limit counts the resulting controls. TouchDesigner 2025.32440 verified the authorization decisions and the 80-control boundary in a disposable project. [Release PR corrections](validation-index.md#v120-release-pr-corrections)

### v1.2.0 release

The owner-approved PR merged as `a2ff1958abbd76c79220c3f58ac2db0a0d9e0a94` after both findings were resolved. The annotated `v1.2.0` tag points to that commit. The clean tagged package passed acceptance on Windows with TouchDesigner 2025.32440. GitHub-hosted Windows and Ubuntu runners verified all five public assets, including the byte-identical `.tox` files. [Release PR](https://github.com/jlopesmo/TDflowMCP/pull/9) · [Public release](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.2.0) · [Release receipts](validation-index.md#v120-tagged-release)

## v1.3 visual diagnostics

The four-scenario v1.2 diagnostic baseline verified passive inspection, separately authorized repairs, and restored output in a disposable project. Correlating reported errors, ranking last CPU cook samples, and comparing separate captures were caller tasks in that build. [Baseline evidence](validation-index.md#v130-diagnostic-baseline)

The v1.3 development candidate adds opt-in network evidence and ranked CPU samples. It compares the cached RGB values of two readable owner-locked TOPs on Windows with TouchDesigner 2025.32440. The first four-scenario replay verified these paths and embedded Skill 1.9. [Candidate evidence](validation-index.md#v130-diagnostic-candidate)

The candidate passed the full packaged regression and independent review. In a disposable project, a live agent diagnosed a disconnected output, used a scoped Builder credential to restore the one wire and verified the edge. An unchanged cook count explained why the previously reported error still needed a cook to verify. An owner-side check confirmed the restored output and preserved the unrelated operator. [Validation](validation-index.md#v130-diagnostic-candidate)

### v1.3.0 release

The owner merged PR #11 as `dace425b07314354468dbac75dab470d2b8f4374`. The merge tree matched the reviewed candidate, Windows and Ubuntu CI passed, and the annotated `v1.3.0` tag points to that commit. The clean tagged package passed the full TouchDesigner acceptance suite on Windows with 2025.32440. [Release PR](https://github.com/jlopesmo/TDflowMCP/pull/11) · [Main CI](https://github.com/jlopesmo/TDflowMCP/actions/runs/45867953194)

The GitHub Release contains the versioned and byte-identical compatibility binaries, build manifest, checksums, and installation guide. GitHub-hosted Windows and Ubuntu jobs anonymously downloaded and verified all five assets. [Public release](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.3.0) · [Release receipts](validation-index.md#v130-tagged-release)

## v1.4 connected workflows and recovery

The first `1.4.0.dev1` batch extends `tdflow_undo` with an optional exact audit sequence. Recovery rechecks current named-parameter grants, records the outcome against the intent, and suspends agents if it cannot verify the result. Durable DAT and control recovery is restricted to the running component that recorded the snapshot. [Development evidence](validation-index.md#v14-recovery-development)

The external Base COMP output connection remains owner-operated. Opt-in connector inspection returned the exposed Out TOP and exact wire indexes. The disposable fixture verified the owner-made wire and control-driven output, observed unchanged cook counters during inspection, and hid the out-of-scope peer. [Development evidence](validation-index.md#v14-connected-workflow-candidate)

Inert experiment recovery verifies the same live origin and copy, a bounded constant TOP tree, and the checkpoint's recorded state. A modified checkpoint, dynamic tree, or replaced origin was refused in the disposable harness. The corrected package verified current named-parameter denials during both restoration and its undo. The allowed operations preserved the unrelated operator and grants. The release gates are tracked in [the v1.4 milestone](../milestones/v1.4-workflow-recovery.md).

The owner approved external wiring by the owner with scoped agent verification and a v1.4.0-only compatibility exception for Windows with TouchDesigner 2025.32440.

### v1.4.0 release

Release PR #15 merged as `2a7634d924e1040d21d55eb429acd6043ed7cfc3` after CI and independent review. The annotated `v1.4.0` tag points to that commit. The clean tagged package passed the full Windows and TouchDesigner 2025.32440 acceptance suite. GitHub-hosted Windows and Ubuntu runners then verified all five public downloads. [Release PR](https://github.com/jlopesmo/TDflowMCP/pull/15) · [Public release](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.4.0) · [Release receipts](validation-index.md#v140-tagged-release)

## v1.5 procedural authoring

The `1.5.0.dev1` candidate adds optional, offline operator guidance to the scoped operator catalog. It covers seven supported types on Windows with TouchDesigner 2025.32440. In the disposable workflow it identified the Math CHOP multiplier before node creation. The candidate verified CHOP-driven TOP opacity, the staged Parameter Execute DAT callback, exact audit recovery and preservation of an unrelated operator. [Development evidence](validation-index.md#v15-procedural-authoring-development)

Active Script CHOP, SOP and TOP authoring is deferred. Their Setup Parameters pulses advanced cook counters with parent cooking disabled, and the docked factory DAT could remain docked after an owner selected a different callback DAT. The candidate refused creation for all three types through the real MCP endpoint. [ADR-0018](../decisions/0018-script-operator-lifecycle.md)

### v1.5.0 release

Release PR #17 merged as `737d5c7fe2541c515c38607362c4244240ff9187` after exact-head CI, full packaged acceptance and independent code and security review. The annotated `v1.5.0` tag points to that commit. The clean tagged package passed the full acceptance suite on Windows with TouchDesigner 2025.32440 under the owner's v1.5.0-only exception. [Release PR](https://github.com/jlopesmo/TDflowMCP/pull/17) · [Owner decision](https://github.com/jlopesmo/TDflowMCP/issues/14#issuecomment-7224593407)

The five release assets are public. GitHub-hosted Windows and Ubuntu runners independently downloaded them and verified the recorded hashes, tag commit and byte equality of both binary files. [Public release](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.5.0) · [Release receipts](validation-index.md#v150-tagged-release)
