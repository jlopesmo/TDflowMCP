# First-run UX redesign, diagnostics and native/web management (2026-11-23)

**Owner direction D21** ([roadmap](../icm/roadmap.md#owner-decisions-2026-11-22)). The first hands-on acceptance of `1.0.0rc1` found the initial configuration confusing even for an experienced TouchDesigner user. Goal: drop the `.tox` into a blank project, see at once whether the environment is ready, and configure, connect, monitor and manage coding agents inside TouchDesigner. The browser stays optional and gets equivalent operations.

## Delivered
- **Diagnostics.**
  - **Automatic:** on the first frame after drop-in, project load and reinit.
  - **Manual:** Run Diagnostics, always on the first page, at any time.
  - **Safety:** they never start a listener, create the audit store or any file, install anything, or start or configure an agent.
  - **Results:** checks that need a listener or a connection show as *later* (not yet verified), never as passed.
  - **Reporting:** System Check gives the compact result. View Diagnostics (Advanced) gives the full report with fixes. Auto detection of coding agents looks names up on PATH and never runs them.
- **Four readiness states** (`readiness.py`), never confused:
  - Environment Ready;
  - MCP Ready (storage, audit, an enabled agent);
  - MCP Enabled;
  - Agent Connected (an authenticated request in the last 2 minutes).

  Enable MCP still runs the live loopback verification every time.
- **Four parameter pages** (TDflowMCP | Agents | Web | Advanced; [setup assistant §2](../architecture/setup-assistant.md#2-essential-custom-parameters)).
  - The main page runs System, Connection, Agent Handoff, Management, with separator lines.
  - Next Step is context-aware and short: `Ready. Pulse Connect Agent.`, `Pulse Copy Token, then turn on Enable MCP.`, `Waiting for Claude Code to connect.`, `Claude Code connected. Ready.`, `Agents suspended. …`.
  - Stale messages are gone: the "Pulse Open Setup" texts are replaced, and the controller's Setup message no longer hides the connection state.
- **Coding Agent** menu: Auto (Recommended), Claude Code, OpenAI Codex, Cursor, VS Code Copilot, Other MCP Client. Tell Your Coding Agent is client-specific, describes the TouchDesigner-only path, and exists before MCP is on. Grok is not listed: no compatible client and configuration method is verified.
- **Connect Agent** (TouchDesigner only):
  1. check the environment;
  2. pick the client;
  3. take the access level and scope;
  4. create the credential through the same backend as the web pages (audited first);
  5. Copy Token, the one-time clipboard handoff;
  6. the user turns on Enable MCP; it is **never enabled silently**;
  7. Connected Agents shows the real connection.

  Automatic configuration of third-party agents is not offered: TDflowMCP never writes coding-agent configuration, so the user follows copyable instructions.
- **Native management** (Agents page), through the **same** admin handler as the dashboard: select an agent, add or remove scope, enable or disable, New Token (rotation), revoke tokens, view agents, and pending approvals (view the complete request, approve or deny exactly that request).
- **Web.**
  - Open Setup and Open Dashboard open **signed in** with a single-use 30-s fragment ticket, a 5-s read-only warm-up and a fail-closed response to interception ([security model §2.5](../architecture/security-model.md#25-dashboard-and-setup-admin-sessions)).
  - Typed URLs still pair.
  - A reloaded tab keeps its session (`sessionStorage`).
  - The dashboard gained a Connection column and New token, for parity.
- **Unchanged security architecture:** MCP off by default, the owner-controlled Enable MCP, Suspend Agents, loopback-only verification, the mandatory durable audit, per-agent tokens, roles and scopes, approvals for code-running operations, fail-closed behaviour, protected internals, project memory integrity, and Code Mode restrictions.

## Visual review
`tests/td/run_td_ux_screens.py` captures only the TouchDesigner parameter window of each page at three stages (fresh, agent created, MCP enabled) into `.tmp/` (git-ignored). The review found and fixed:
- values truncated in the default 416-px window (System Check, Next Step, Agents, Build): texts shortened, action first;
- no visual grouping: separator lines added;
- the agent menu repeated the role;
- Selected Agent too long: Scope is filled from the selected agent instead.

## Independent review 18 (security)
| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | A process that read the ticket off the browser's command line and won the race could act at once (for example mint an operator token); detecting the race afterwards did not undo it | 5-s read-only warm-up for ticket-born sessions; on interception: every session ends, agents suspended, tokens and agents created since revoked or disabled, Code Mode off, all audited |
| 2 | Medium | A ticket sign-in was not recorded before it took effect | Redemption on a handler thread, recorded durably first; 503 without the audit store |
| 3 | Medium | A native approval could approve a request the user never saw (list refresh, a flood of requests) | Decisions bound to the viewed request's id and argument digest (`APPROVAL_CHANGED` otherwise); nothing decided unseen |
| 4 | Medium | The clipboard handoff left the token in Windows Clipboard History / Cloud Clipboard | Copy Token sets the exclusion formats through Win32, and refuses rather than copying plainly |
| 5 | Low | The session-resume endpoint made a stolen cookie enough | Endpoint removed; the page keeps its CSRF token in `sessionStorage` |
| 6 | Low | No rate limit on ticket attempts | 429 after 5 failed attempts a minute, without further records |
| 7 | Low | A replay long after (history, autocomplete) signed everyone out | Interception window 15 s; later replays are plain refusals |
| 8 | Low | The approval viewer kept agent arguments in the `.toe` | Cleared after the decision or 2 minutes |
| 9 | Low | The rotate record lacked token ids; a failed revocation was ignored; native actions were not labelled | Ids recorded; failures refuse the rotation; `native_action` records with `via: touchdesigner` |
| 10–12 | Notes | The token could stay longer than documented; doc inconsistencies; a ticket prefix in a test log | Strict 2-minute lifetime; docs corrected; test log fixed |

## Evidence
See the [validation index](../icm/validation-index.md):
- unit tests `test_readiness.py` and `test_browser_ticket.py`;
- 1A `ux.*`;
- 1B `native.*` and `web.*`: the TouchDesigner-only agent setup, the clipboard-only token, real connection status, scope edits, rotation, disable and enable, a native approval, one-click sign-in, warm-up, replay and interception with suspension;
- `run_td_native_tests.py`: the full first run with a **live Claude Code 2.1.289** session, then save, restart and recovery;
- all five earlier suites.

## Limits
- Native administration is built from custom parameters and viewer DATs, not a custom panel COMP. It is compact and follows TouchDesigner conventions, but long values can need a wider parameter window.
- Connected Agents is based on requests TDflowMCP has seen (2 minutes). An agent that connected but is idle drops back to "not seen".
- Clipboard History exclusion and "monitor processing" are Windows features. On other platforms Copy Token refuses, and the Setup page shows the token once instead.
- One-click sign-in residual risk as in [security model §2.5](../architecture/security-model.md#25-dashboard-and-setup-admin-sessions).
- Codex, Cursor and VS Code Copilot remain documented, not live-tested; only Claude Code is verified end to end.
