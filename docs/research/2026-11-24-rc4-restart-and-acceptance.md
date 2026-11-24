# rc4: the restart finding, automated acceptance and corrections (2026-11-24)

**Owner direction D23** ([roadmap](../icm/roadmap.md#owner-decisions-2026-11-22)). The owner's hands-on acceptance of `1.0.0rc3` passed:
- connection through Connect Agent;
- a real Claude Code VS Code session (`tdflow_whoami`);
- operators created;
- Suspend Agents refusing (`SUSPENDED`), then resuming.

After saving, closing TouchDesigner and reopening the project, the component showed `0 agents · 0 connected`, `(no agents yet)`, `Ready: pulse Connect Agent` and `Data Status: not opened`. Version **1.0.0rc4**.

## Root cause
**A misleading interface over intact data. It was not data loss.**

On reopening, storage is deliberately not opened until a user action (Enable MCP, Connect Agent, Open Setup), so loading a project never starts a listener or writes a file ([setup assistant §1](../architecture/setup-assistant.md#1-component-states)). The interface did not distinguish "not loaded" from "empty":
- the agent count, the agent menu and the readiness line read the not-yet-built identity as zero agents;
- the connection record (private settings) is loaded with the store, so Next Step fell back to `Ready: pulse Connect Agent`.

Turning on Enable MCP would have loaded everything, and Claude Code would have reconnected with its existing credential.

**Evidence from the owner's environment.** It was read-only:
- names, counts and booleans only;
- the SQLite files were copied to the scratchpad first and read there, and the copies were deleted;
- nothing was modified, claimed, moved or reset.

What was there, for `prj_f4f1d1c4434f812c`:
- the private store and audit key;
- 5 agents and 5 unrevoked tokens, from rc2 and rc3 sessions;
- the connection record (`claude-code` → `agt_1WJYx6z8sl`, written 18:02:21 UTC) and the connect key;
- the DPAPI key file;
- the project-local audit log with 80 events, including `client_connect` intent / ok / verified and the final `mcp disabled`;
- the registry entry pointing at the incremental save `NewProject.1.toe`;
- the `tdflow` entry in `~/.claude.json`, with the proof-checking helper.

The credentials were written (18:02) before the project was saved. So the screenshot was **deferred initialization shown as an empty registry**.

**Automatic reproduction.** The new restart scenario run against the **rc3 package** (`ffbb3ea1…`, below) shows exactly the owner's screen after reopening:
- `0 agents · 0 connected`;
- `(no agents yet)`;
- `Ready: pulse Connect Agent`.

In the same run, Enable MCP alone loads the same agent, and Claude Code reconnects with the same credential.

**A test defect found on the way.** In the first runs, the recovery launch read an empty `Projectid` and saw no request, although Claude Code reported Connected. The in-TD runner reused the saved component only for the modes `memory_restart` and `native_restart`. For the new `native_restart2` and `native_recover` it loaded the `.tox` a second time, so the checks ran against a fresh, empty second component. The runner now reuses the saved component in every reopen mode, and each launch checks `ONE_COMPONENT_REOPENED`. This was not a product defect.

## Fix (UX-09)
- **Read-only startup inspection** (`_inspect_prior`, part of the automatic diagnostics). It looks in this project's private folder, named by the saved `Projectid`. When the `.toe` was not saved after the id was assigned, it uses the one registry entry for this `.toe`, as the storage decision would. It looks for:
  - the credential store;
  - a small **non-secret summary**: enabled agents and connected clients, kept by the component after the store has opened;
  - connection key files (projects connected before rc4 have no summary).

  It opens, creates and changes nothing, and starts no listener. A snapshot test proves no file or folder changes.
- **What the user sees** for a project with enabled agents that are not loaded yet:
  - Next Step **`Turn on Enable MCP.`**;
  - Agents **`1 agent · not loaded`** (or `not loaded (MCP off)`), the menu `(not loaded yet)`, Selected Agent `Not loaded: pulse Manage Agents`;
  - Tell Your Coding Agent: "already set up for this project".

  `0 agents` is never shown for storage that is not loaded.
- **When the storage decision waits for the user** (a moved or copied project): `Data: confirm (Advanced)` instead of a misleading hint.
- After Enable MCP, the generic note "MCP on. Waiting for agent" now lasts 4 s instead of 15 s, so `Agent Connected — Ready` appears as soon as the agent talks.
- **Manage Agents** loads the agents. That is a user action and opens the store, as Connect Agent does.
- Enable MCP still runs the full storage decision, the audit verification and the live loopback check. MCP stays off after every restart.

## Other corrections
- **UX-08.** TouchDesigner's pop-up dialog is 260 px wide, so the plan wrapped awkwardly.
  - It is widened to 480 px while a TDflowMCP dialog is open, then restored exactly (value, mode, expression) by the callback, on failure, and when the component shuts down.
  - Only one TDflowMCP dialog is open at a time.
  - Buttons stay short.
- **Review 20, storage (3).** An **unsaved** project that carries a saved project's identity (a pasted component, or a `.tox` made from it) is now `COPIED` and does not open that project's data. Previously this was possible when the data lived in private storage. Also, an unsaved session no longer erases the registered `.toe` (copy detection depends on it).
- **Strict ids:** `\Z` instead of `$`, so no trailing newline.
- **Kill-safe connection:**
  - A **pending** connection record is written before the credential is stored, so a kill at any later point leaves something Repair Connection and Disconnect Agent can find. The pending record is removed again on rollback.
  - Repair never reuses a disabled agent.

## Independent review 20 (storage, identity, startup, credentials)
A read-only agent reviewed the first version of the fix. It ran the unit tests and two scratch simulations.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `credentials.sqlite` exists after any storage open (a cancelled connect, after Disconnect), so "Turn on Enable MCP." could be shown for a project with no agent: the defect in reverse | The hint now needs ≥ 1 enabled agent in the non-secret summary (or, for pre-rc4 projects, a connection key file). With no agents: the normal `Ready: pulse Connect Agent` and `0 agents · not loaded` |
| 2 | Medium (fails closed) | A failed storage decision (moved or copied project) kept showing "Turn on Enable MCP." and "already set up" | `Data: confirm (Advanced)`; the hint and the Tell text are suppressed while the decision waits |
| 3 | Medium (pre-existing) | An unsaved project with a pasted identity whose data is in private storage opened that project's data and erased the registry's `.toe` | `COPIED` (unit test); the registered `.toe` is kept |
| 4 | Low | The dialog width could stay widened (a second dialog; an expression mode); restore untested | One dialog at a time; exact restore; restore on shutdown; `screens.*` checks widening, one-at-a-time and restore with the real dialog |
| 5 | Low (tests) | The kill test did not prove that nothing was lost; the duplicate check was partly tautological; directories were not in the snapshot | `recover.NO_AUDIT_EVENT_LOST_IN_THE_KILL` (event count ≥ the count before the kill); the same credential byte for byte (helper) and the same agent id as launch 1; the summary checked; directories in the snapshot |
| 6 | Low | An id assigned but not saved showed the old state | Read-only registry recovery in the inspection |
| 7 | Note | `$` accepts a trailing newline | `\Z` (unit test) |
| 8 | Note | `Projectid.eval()` | `.val` |
| 9 | Note | Manage Agents and View Agents now open the store (writes: registry, `.tdflowmcp/`) | Documented here and in the [setup assistant](../architecture/setup-assistant.md#2-essential-custom-parameters) |
| 10 | Note | Key-file detection is Windows-only | The summary covers every platform; key files are only the pre-rc4 fallback |
| 11 | Note | ICM not yet updated | Updated (this record, current state, validation index, setup assistant, security model, checklist) |

The review's analysis of kills during connect and disconnect (all fail closed) is addressed by the pending record and the disabled-agent rule above.

## Acceptance matrix (automated)
Evidence is in the [validation index](../icm/validation-index.md) (rc4 package rows). "Live" means a real Claude Code client.

| Area | Scenario | Evidence |
|---|---|---|
| A Installation and diagnostics | Blank project, automatic checks, nothing listens or is created | native `first_run.AUTOMATIC_DIAGNOSTICS`, `NOTHING_LISTENS_OR_IS_CREATED`; 1A `ux.*` |
| | Manual Run Diagnostics any time; Running… and Checks done; Last Check | native `RUN_DIAGNOSTICS_SHOWS_RUNNING`, `MANUAL_DIAGNOSTICS_COMPLETE_WITH_COUNTS`; 1A |
| | Pending never counted as passed; failures named | native `PENDING_NEVER_COUNTED_AS_PASSED`; unit `test_readiness` |
| B Claude Code connection | Auto discovery (no execution); one automatic agent recommended, others named | native `AUTO_FINDS_CLAUDE_CODE…`, `AUTO_RECOMMENDS_THE_AUTOMATIC_AGENT` |
| | Approval dialog contents; cancel changes nothing | native `CONSENT_SHOWS_THE_PLAN`, `CANCEL_CHANGES_NOTHING`, `NOTHING_CHANGED_BEFORE_APPROVAL` |
| | Existing client settings preserved; OS-protected credential; proof of key | native `ENTRY_WRITTEN_EVERYTHING_ELSE_PRESERVED`, `CREDENTIAL_IN_THE_PROTECTED_STORE`, `LISTENER_PROVES_THE_PROJECT_KEY`; unit `test_connect` |
| | No token in environment, command line, DATs, `.toe`, logs, client file | native `NO_TOKEN_IN_THE_CONFIG_OR_COMPONENT`, `restart.NO_TOKEN_IN_THE_SAVED_PROJECT`, `leak.NO_PLAINTEXT_TOKEN_IN_ANY_FILE`, `live.no_token_in_process_environment_used`, `*.no_token_in_agent_output` |
| | Real authenticated connection | **live** CLI 2.1.289 and VS Code extension engine 2.1.330 (`mcp list` ✔ Connected); `AGENT_CONNECTED_READY` with client identity |
| C Agent operation | Read, create, connect, update, undo, verify network | **live** session after restart (`RESULT … set=0.5 after_undo=1`), `work.CREATED_AND_CONNECTED`, `work.UNDO_RESTORED_THE_PARAMETER`, `work.UNDO_AUDITED`; first session `created=yes` |
| | Denied outside scope; audited | **live** `outside=denied`, `work.DENIAL_AUDITED`; 1B scoped allow/deny |
| D Security controls | Suspend blocks; resume restores | native `SUSPEND`/`RESUME`, harness `SUSPENDED_REFUSES_THE_AGENT`; 1B kill switch incl. queued request |
| | Revocation blocks; Repair restores | native `REVOKED_CREDENTIAL_REFUSED`, **live** client HTTP 401, `repair.CLAUDE_CODE_CONNECTS_AGAIN` |
| | Approvals required; changed or unseen approvals refused | 1B `native.*` approval (viewed request only, `APPROVAL_CHANGED`), approval flow unit tests, 1B approval suite |
| E Persistence | Save and reopen; full TouchDesigner restart; nothing created by reopening; identity kept | native `restart.*`/`restart2.*`/`recover.*`: `REOPENING_CREATED_OR_CHANGED_NOTHING`, `PROJECT_IDENTITY_KEPT`, `NOT_LOADED_IS_NOT_REPORTED_AS_EMPTY`, `NEXT_STEP_TURN_ON_ENABLE_MCP` |
| | Coding-agent restart; credential reused; no duplicates; audit continuous | **live** reconnect after each restart (`CLAUDE_CODE_RECONNECTS_WITHOUT_SETUP`, `SAME_CREDENTIAL_REUSED`), `NO_DUPLICATE_AGENT_OR_TOKEN`, `AUDIT_CHAIN_CONTINUOUS`, `AUDIT_GREW_AND_VERIFIES` |
| | Second restart after reconnection; abrupt termination and recovery | `restart2.*`, `restart2.killed_by_harness`, `recover.NO_AUDIT_EVENT_LOST_IN_THE_KILL`; gate crash/restart launches |
| F Browser administration | Open Setup / Dashboard without a secret; number-matching sign-in; retired ticket refused; untriggered requests wait | 1B `web.*` |
| | Session expiry; unauthorized requests refused; native/browser consistency | unit `test_sessions_expire`, gate tests; 1B `admin.*` (CSRF, Origin); native actions through the same backend (1B `native.*`) |
| G Disconnect and cleanup | Only TDflowMCP's entry removed; other servers kept; credential and tokens revoked; replaced entry restored | native `disconnect.*`, harness `OTHER_SERVER_AND_SETTINGS_KEPT`, `CLIENT_NO_LONGER_HAS_TDFLOW`, `SUMMARY_SAYS_NO_AGENT`; unit `test_replace_foreign_entry_then_restore_it_from_the_record` |

**Not automated (need the owner):**
- The VS Code chat UI itself. Automated runs use the extension's bundled engine, which reads the same configuration, not the chat panel.
- The owner's real profile.
- A Mac.

## UX status (UX-01 to UX-09)
- **UX-01, 02, 03, 05, 06, 07:** fixed in rc3 and re-verified (native, 1A, unit).
- **UX-04:** Windows verified. macOS implemented but not validated; this is stated in the instructions and the dialog.
- **UX-08:** fixed (wider dialog, exact restore).
- **UX-09:** fixed (above).
