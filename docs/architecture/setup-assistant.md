# Setup Assistant, component parameters, compatibility and troubleshooting

**Status:** Design for owner-approved requirements P9–P12 and P14, **revised for the first-run UX (owner D21, 2026-11-23) and the one-click connection (owner D22, 2026-11-24, [ADR-0015](../decisions/0015-consent-based-client-connection.md)); implemented, §2** ([ADR-0010](../decisions/0010-setup-assistant-and-component-parameters.md)). **The Phase 1A part is implemented** in the development build of `TDflowMCP.tox` (2026-11-22). It is verified in TouchDesigner 2025.32440 by the in-TD suite; see [Phase 1A component record](../research/2026-11-22-phase-1a-component.md). **The Phase 1B part (pairing, agent tokens, Enable MCP, Suspend Agents, agent and session information) is implemented** and verified by the Phase 1B in-TD acceptance suite. The 1C rows remain design only.

## 1. Component states

| State | Listener | `/setup` | `/mcp` | `/ui` (dashboard) | How entered |
|---|---|---|---|---|---|
| **Off** (default after drop-in) | none | — | — | — | Initial; `Stopserver` pulse; idle timeout |
| **Starting** (1A, transient) | 127.0.0.1:`Port`, `VERIFYING` | 503 | 503 | 503 | `Opensetup` pulse; ends in Setup or Error |
| **Setup** | 127.0.0.1:`Port` | read-only (1A); paired actions (1B) | 503 `MCP_DISABLED` | 404 until Phase 2B | verification passed |
| **Stopping** (1A, transient) | closing | — | — | — | `Stopserver`, a Port change, a failed check, project exit or component deletion; ends when the OS table confirms the socket is gone |
| **Enabled** (1B) | 127.0.0.1:`Port` | available | active (policy-enforced) | Phase 2B | `Enablemcp` on, after preconditions (§4). Starts the listener if needed (no browser) |
| **Suspended** (1B) | 127.0.0.1:`Port` | available | `SUSPENDED` for all agents, including queued requests | Phase 2B | `Suspendagents` on (kill switch) |
| **Error** | none | — | — | — | `PORT_IN_USE` (another process listens on the port, on any address: never shared), `BIND_UNSUPPORTED` (Web Server DAT transport without `localaddress`: never activated), `BIND_UNVERIFIED` (closed by the gate), `CLOSE_UNCONFIRMED` (socket did not disappear: restart TouchDesigner), port conflict, storage failure. Leaving Error needs an explicit user action |

The component **never listens until the user acts** (pulse or toggle). After the user acts, the listener is activated in a non-serving `VERIFYING` state (every request gets `503`). Application requests are served only after the effective loopback-only binding is positively verified. If verification fails or is ambiguous, the listener is **closed immediately**, the state becomes `Error: BIND_UNVERIFIED`, and the only guidance channel is the component's parameters and an in-component text DAT, never a less-secure listener ([security model §8.2](security-model.md#82-bind-verification-gate)). While serving, the binding is re-verified every 10 s. In Setup state with MCP disabled, the listener auto-stops after 30 min without requests. If the component is deleted, the extension shuts the listener down at once (`onDestroyTD`). As a backstop, the transport closes itself when the main thread has stopped pumping for 20 s (heartbeat).

## 2. Essential custom parameters

**Four pages (owner UX assignment, 2026-11-23, implemented; [record](../research/2026-11-23-first-run-ux.md)):** **TDflowMCP | Agents | Web | Advanced**. TouchDesigner draws a separator line above the first parameter of each group (`startSection`). The everyday workflow, including agent administration, needs no browser. The web pages offer the same operations with more detail, through the same backend. Parameters never hold tokens or other long-lived secrets, because parameter values are saved into the `.toe`.

**Builder notes (from S5):**
- a menu's `.default` does not change its current value, so `.val` must be set explicitly;
- a `readOnly` parameter can still be set by script, which suits status parameters;
- a pulse fired on the same frame its Parameter Execute DAT is created is not delivered, so callbacks must be wired at least one frame before use.

**Page TDflowMCP (everyday use)**

| Group | Parameter | Type | Behaviour |
|---|---|---|---|
| System | `Version` | Str, read-only | Component version (tool surface) |
| | `Compatsummary` "System Check" | Str, read-only | Latest diagnostics in about 26 characters (the default window's width): `Ready · 8 ok · 3 pending`, `Not ready: 1 fail, 1 warn`. Zero warn/fail counts are left out; View Diagnostics lists passed, warnings, failed and pending. *Pending* means a check needs a listener or a connection; it is never counted as passed. Shows `Running diagnostics…` while a manual run is under way (UX-01, owner D22) |
| | `Rundiagnostics` | Pulse | Runs the checks **at any time** (also before an agent exists, after an error, while MCP is on). Never starts a listener, never creates the store, never installs or changes anything. Confirms completion in Next Step (`Checks done hh:mm:ss`) |
| | `Lastcheck` "Last Check" | Str, read-only | Time of the last run and whether it was automatic or manual (D22) |
| | `Status` | Str, read-only | `Off`, `Starting`, `Setup`, `Stopping`, `Enabled`, `Suspended`, `Error: <code>` |
| | `Statusdetail` "Next Step" | Str, read-only | What is happening and what to do next ([§2.1](#21-readiness-and-next-step)) |
| Connection | `Codingagent` "Coding Agent" | Menu | `Auto (Recommended)` (default), Claude Code, OpenAI Codex, Cursor, VS Code Copilot, Other MCP Client. Which agent Connect Agent connects and whose instructions are shown; Auto discovers read-only and asks when it finds several. Never the protocol or permissions. Saved in the project (not a secret) |
| | `Agentaccess` "Agent Access" | Menu | Read Only (observer), Builder (default), Operator |
| | `Allowedscope` "Allowed Scope" | Str | Scope for a new agent, default `/project1/**` (must lie inside Project Root) |
| | `Connectagent` | Pulse | The guided, consent-based connection ([§2.2](#22-connect-agent)) |
| | `Enablemcp` | Toggle | Off by default; turns MCP on only after the preconditions (§4) **and a fresh live loopback verification**. Never re-enabled silently after a reload. Connect Agent turns it on only when the plan the user approved says so |
| | `Suspendagents` | Toggle | Kill switch; audited |
| Agent Handoff | `Tellagent` "Tell Your Coding Agent" | Str, read-only | State of the instructions (`Ready · v…`, `Repair: …`) |
| | `Copytellagent` "Copy Instructions" / `Viewinstructions` "View Full Instructions" | Pulse | Clipboard / viewer of the client-specific instructions ([§6](#6-tell-your-coding-agent)) |
| Management | `Connectedagents` | Str, read-only | Agents whose **authenticated** requests arrived in the last 2 minutes, with the client name (`Claude Code (5 s ago)`); `none yet (MCP is on)` / `none (MCP is off)`. A registered agent is not a connected agent |
| | `Manageagents` | Pulse | Opens the parameter window on the Agents page; rc4: if this project's agents are not loaded yet, loads them (opens the store, a user action like Connect Agent; no listener) |
| | `Opendashboard` | Pulse | Opens the dashboard (starts the listener if needed); TouchDesigner then asks to approve the sign-in, showing the page's code ([security model §2.5](security-model.md#25-dashboard-and-setup-admin-sessions)) |

**Page Agents (native administration; the same backend as the dashboard)**

| Parameter | Type | Behaviour |
|---|---|---|
| `Detectedclients` "Installed Agents" | Str, read-only | What discovery found, and whether setup is automatic: `Claude Code (CLI, VS Code extension; automatic) · OpenAI Codex CLI (CLI; manual)` |
| `Agentsummary` "Agents" | Str, read-only | `2 agents · 1 connected`. **rc4 (UX-09):** before the store is loaded (after reopening a project), `1 agent · not loaded` (from the non-secret summary) or `not loaded (MCP off)`, never `0 agents`; the agent menu shows `(not loaded yet)` |
| `Agentselect` "Agent" | Menu (filled at runtime) | Every agent by name (`(disabled)` when disabled); selecting one fills Scope with its first scope |
| `Agentinfo` "Selected Agent" | Str, read-only | `On · Builder · 1 token · 1 scope · seen 5 s ago` |
| `Agentscope` "Scope", `Addscope`, `Removescope` | Str, Pulse | Add an allow grant with the agent's role actions on Scope / remove the grant on Scope |
| `Enableagent`, `Disableagent` | Pulse | Enable / disable the agent (its tokens stop working) |
| `Repairconnection` "Repair Connection", `Disconnectagent` "Disconnect" | Pulse | For an agent Connect Agent set up: a new credential and a re-checked entry (plan shown first) / remove the entry (or restore the one it replaced), delete the credential, revoke the tokens, disable the agent (asks first) |
| `Rotatetoken` "New Token", `Copytoken`, `Revoketokens` | Pulse | Manual fallback: revoke the agent's tokens and create a new one / copy it once to the clipboard (within 2 minutes; kept out of Windows Clipboard History and Cloud Clipboard; cleared after a minute) / revoke them all |
| `Viewagents` | Pulse | A viewer listing every agent (never tokens) |
| `Approvalsummary` "Pending Approvals" | Str, read-only | `1 waiting · oldest: Agent E · tdflow_set_parameters` |
| `Viewapproval`, `Approverequest`, `Denyrequest` | Pulse | Show the oldest waiting request with its complete arguments in a viewer, then approve or deny **that** request (bound to its id and argument digest; nothing can be decided unseen). The viewer is cleared after the decision or 2 minutes |

**Page Web**

| Parameter | Type | Behaviour |
|---|---|---|
| `Opensetup`, `Webdashboard` "Open Dashboard" | Pulse | Open the Setup page / dashboard in the default browser; approve the sign-in in the TouchDesigner dialog (same code as the page) |
| `Websession` "Browser Session" | Str, read-only | Signed-in browser sessions and the time of the last sign-in |
| `Setupurl` | Str, read-only | The Setup address while the listener runs |
| `Pairingrequest`, `Approvepairing`, `Rejectpairing` | Str / Pulse | Pairing for a browser that opened a typed URL (§7) |
| `Endbrowsersession` | Pulse | Sign every browser out |

**Page Advanced** — unchanged controls, plus `Buildinfo` "Build" (TD build, Python, Skill version), `Viewdiagnostics` "View Diagnostics" (the full report with fixes in a viewer) and `Viewconnection` "View Connection Details" (configuration file, MCP entry, helper, credential store, backups; no secrets; D22). Unchanged: `Stopserver`, `Port`, `Portstatus`, `Projectroot`, `Datalocation`, `Datadir`, `Datastatus`, `Confirmmove`, `Claimproject`, `Createprojectmemory`, `Claimprojectmemory`, `Projectid`, `Loglevel` (behaviour as before: [Port](#3-compatibility-checks) never changes automatically; Confirm Move / Claim As New Project for moved projects; project memory pulses).

**Runtime viewer DATs** (`diagnostics`, `agents_view`, `approval_view`, `connect_view`, like `tellagent`) hold no secrets. They are excluded from the embedded-hash check because their text changes at runtime.

### 2.1 Readiness and Next Step
`src/tdflowmcp/readiness.py` keeps four states apart:
- **Environment Ready:** TD build, Python, modules, loopback capability, port, a single instance.
- **MCP Ready:** storage open, audit verified, at least one enabled agent. The live loopback verification still runs at every Enable.
- **MCP Enabled.**
- **Agent Connected:** an authenticated request seen in the last 120 s.

Next Step shows, in this order:
1. a fresh note (for example a copy result);
2. a controller message for 30 s after a transition (not in Setup);
3. otherwise the readiness line, for example `Ready: pulse Connect Agent`, a Connect Agent stage (`Approve in the dialog.`, `Configuring Claude Code…`, `Start a Claude Code chat.`, `Connected · idle 5 min`, `No key: Repair Connection`, `Turn on Enable MCP.`), `Agent Connected — Ready`, `Agents suspended.` or `Not ready: port`.

**After reopening a project (rc4, UX-09; [record](../research/2026-11-24-rc4-restart-and-acceptance.md)).**
- Storage stays closed until the user acts.
- The automatic diagnostics inspect this project's private folder **read-only**: the credential store, a non-secret `summary.json` (enabled agents, connected clients, kept by the component once the store has opened) and connection key files. Nothing is opened, created or changed.
- With at least one enabled agent, Next Step says `Turn on Enable MCP.`.
- With a moved or copied project whose decision waits, it says `Data: confirm (Advanced)`.

A Connect Agent step counts as newer than an older note (ties go to the step), so progress stays visible. Steps in progress or failed take precedence over `Agent Connected — Ready`. **Every line fits 26 characters,** the value width of TouchDesigner's default 416-px parameter window (visual review, D22; unit-tested). Details go to the viewers.

Errors always show. Texts are short, because the default parameter window is about 400 px wide (visual review).

### 2.2 Connect Agent
Owner D22, [ADR-0015](../decisions/0015-consent-based-client-connection.md); implemented in `src/td_adapter/connection.py` and `src/tdflowmcp/connect.py`.
1. **Checks.** Run the checks; stop with the reason if the environment is not ready.
2. **Agent.** Coding Agent: the selected client, or (Auto) the discovered ones. With several, a TouchDesigner dialog asks which to connect.
3. **Fallback.** A client without automatic setup (Codex, Cursor, Copilot, Other) gets an agent and a token for **Copy Token** (Agents page) and the short manual instructions.
4. **Plan.** For Claude Code, TouchDesigner shows the plan in a dialog, with details in `connect_view`. The plan names:
   - the agent and the interfaces found;
   - `~/.claude.json` (user scope) and the exact change, plus the other servers that stay;
   - the backup;
   - access level and scope;
   - the protected credential store;
   - whether **Enable MCP** will be turned on;
   - how to undo.
5. **Approval.** Approve, or Cancel (nothing changes). Without a dialog, a second pulse within 2 minutes approves. The plan expires after 2 minutes.
6. **Apply** (background thread):
   - audit intent;
   - create the agent and token, or rotate the existing agent's token (Repair);
   - store the credential (DPAPI / Keychain);
   - write the entry (backup, atomic, read back; `CONFIG_CHANGED` if the approved change no longer applies);
   - record the connection in private settings;
   - audit `ok`.
   A failure revokes the new credential.
7. **Enable MCP**, only if the approved plan said so; the usual live loopback verification runs.
8. **Wait** for the agent's first authenticated request after this point (`Start a Claude Code chat.`; after 90 s without a request `No request: /mcp reconnect`). Then `Agent Connected — Ready`, with the client's own name and version under Connected Agents; audit `verified`.
9. **After a restart:** Next Step says `Turn on Enable MCP.`, and Connect Agent then only asks to turn on MCP.

## 3. Compatibility checks

Each check yields `{id, result: pass|warn|fail|unknown, evidence, remediation_id}`. `unknown` is reported honestly (never upgraded to pass) and shown as *later* / *not yet verified*.

**When checks run (UX assignment, implemented):** automatically on the first frame after the `.tox` is dropped in, a project containing it is opened, or the component is reinitialized; and whenever the user pulses Run Diagnostics, Open Setup, Open Dashboard, Connect Agent or Enable MCP. The automatic run never starts a listener, never creates the audit store or any file, never installs anything and never starts or configures an agent. `agent.clients` now uses the selected or detected clients (names looked up on PATH with `shutil.which`, never executed).

| ID | Check | Pass rule (provisional) |
|---|---|---|
| `td.build` | `app.build` / `app.version` | ≥ **2025.32440** (oldest tested build, owner D10) → pass; older → `warn: untested build` (MCP enablement then requires every security self-test to pass). Also flags a `.tox` saved by a newer build than the running one |
| `py.version` | `sys.version_info` | 3.11.x |
| `py.deps` | `jsonschema`, `sqlite3`, `ssl`, `hmac`, `secrets`, `http.server`, `socketserver`, `threading`, `queue`, `socket` importable; versions recorded | all present |
| `net.capability` | Pre-activation, per transport ([§8.2 step 0](security-model.md#82-bind-verification-gate)). Embedded (default): bind host is the literal `127.0.0.1` and exclusive binding is available. Web Server DAT (optional): the DAT exposes `localaddress` | pass → the listener may be activated; fail → never activated. On 2025.32440: embedded **pass** (S6); Web Server DAT **fail** (S5) |
| `net.bind` | Bind-verification gate: OS listening-socket table + non-loopback connection attempts ([§8.2](security-model.md#82-bind-verification-gate)) | positively verified loopback-only; else listener closed |
| `net.port` | Port 9480 (or `Port`) free / owned by this instance; identify other TDflowMCP instances via `GET /.well-known/tdflowmcp` on loopback | free or self |
| `fs.datadir` | Storage decision and writability (1C): fail when the project data needs the user's confirmation (moved, copied, relocated, older data) or the store could not open; pass with the reason when data falls back to private storage | writable, identity confirmed |
| `audit.integrity` | The audit chain and keyed checkpoints verify (checked when the store opens and again before Enable MCP) | verifies |
| `td.instances` | Number of TDflowMCP components in the project | exactly one |
| `agent.clients` | The Coding Agent selection, or (Auto) the clients detected on PATH via `shutil.which` (no execution, no paths reported) | a selected client is primary/verified (others: warn, with the generic instructions) |
| `agent.skills` | Installed Skills manifest vs bundled version (1C). **Implemented as the Setup page's Skills table** (installed vs bundled, mismatch flagged), not as a compatibility check | match |
| `sec.selftest` | Gate rejects foreign/other-port Origin; bind verified; tokens hashed (1B) | all pass |

## 4. Preconditions to enable MCP (1B)

`td.build` not fail · `net.bind` pass · `net.port` free/self · `fs.datadir` pass · `audit.integrity` pass · `sec.selftest` pass · at least one enabled agent token exists; then a fresh verification, the `mcp enabled` record and a checkpoint must be confirmed durable before `/mcp` opens. Older builds may enable MCP **only** if all security self-tests pass — there is no override.

## 5. Guided troubleshooting

`src/setup/troubleshooting.json` maps each check failure to: cause, how to confirm, step-by-step fix, and links to TouchDesigner documentation. Allowed automatic actions are limited to TDflowMCP-internal, reversible ones: re-run checks, and propose a different `Port` for the user to confirm. The listener is **never restarted automatically**, least of all after a failed bind verification; only the user's own `Opensetup` pulse may try again. TDflowMCP **never** installs or upgrades packages, changes TouchDesigner preferences or search paths, edits firewall/OS settings, writes coding-agent configuration silently, or kills processes. The only file writes are:
- the Claude Code connection entry, after the user approved the plan in TouchDesigner ([§2.2](#22-connect-agent));
- Skills files;
- after the user migrated project memory to a folder of the project, its record files.

Each requires an explicit, previewed confirmation ([agent integration §3](agent-integration.md#3-client-aware-installation-consent-first), [continuity §8](continuity-and-memory.md#8-phase-2a-increment-3-repository-mode-2026-11-23)).

## 6. "Tell Your Coding Agent"

Generated from current server state — never hand-edited — in four sections. **Client- and platform-aware (D21, D22):**
- The Connect section covers only the selected client, or with Auto the detected ones, or the generic "Other MCP client".
- **Claude Code:** the automatic path (Connect Agent → Approve → a new chat), with no token step, and whether it is already set up for this project.
- **Other clients:** a 4-step manual fallback in this platform's shell, stating why it is manual.
- No "open the Setup page" step.
- Authorized configuration (the user's approval in TouchDesigner) is distinguished from silent modification, which agents must not do.

It is present before MCP is on:

1. **Setup**: what TDflowMCP is, the component version, current state and which checks fail.
2. **Connection** (1C, revised D22): the automatic steps for Claude Code, or for a manual client the endpoint URL and a client- and shell-specific command whose `${TDFLOW_TOKEN}` placeholder stays literal (single quotes, user/local scope only; see [security model §10](security-model.md#10-setup-assistant-and-integration-safety)), and the expected Skills version.
3. **Repair**: for each failing check, the instructions from troubleshooting — addressed to the agent, with the rule "do not install software or change configuration without asking the user".
4. **Resume earlier work** (2A, **implemented**): fixed text plus the number of tasks and of tasks not marked done, and the instruction to call `tdflow_task_list`, then `tdflow_handoff_resume`, before changing anything. Nothing agent-chosen appears, not even task ids (review 9). The section refreshes when a record is written.

Only allowlisted fields are included. Never included: tokens, environment-derived free text (operator names, probe answers, raw errors), audit contents beyond counts, or expanded user-profile paths.

## 7. Setup Assistant (web)

`/setup/` — a single static page (vanilla HTML/JS, no build, no CDN) plus JSON endpoints:
- 1A (read-only, **implemented**): `GET /setup/api/status` (state, checks with remediation, Tell Your Coding Agent text) and `GET /setup/api/tellagent` (plain text). The page renders status, checks, troubleshooting and the agent text, and polls every 2 s.
  - Every read requires the `X-TDflow-Request: 1` header and a valid Host ([gate table](security-model.md#81-per-route-gate-table)).
  - Assets are Text DATs inside the `.tox`, served from an immutable snapshot under a CSP with no inline script.
  - All server data is rendered with `textContent`.
  - The Copy button uses `navigator.clipboard` and falls back to selecting the text with an explanation.
  - No write endpoint exists.
- 1B (**implemented**; paired admin session, exact Origin + CSRF, [security model §2.5](security-model.md#25-dashboard-and-setup-admin-sessions)):
  - Pairing: `POST /setup/api/pair` and `GET /setup/api/pair/status`. The browser shows the match code; approval happens only inside TD.
  - Agents: `GET /setup/api/agents` lists agents, recent sessions and audit integrity.
  - Tokens: `POST /setup/api/tokens` creates one. Role observer or builder; one scope inside `Projectroot`; an optional subset of actions. Each token is shown once.
  - `POST /setup/api/tokens/revoke` and `POST /setup/api/logout`.
  - Enabling MCP stays a TouchDesigner parameter (`Enablemcp`); it is not a web action.
- 1C: client selection, connection instructions, Skills preview/install/update.
- **First-run UX (D21, implemented):** a page opened from TouchDesigner (Open Setup / Open Dashboard) signs in by itself with a single-use fragment ticket (`POST /setup/api/pair/ticket`); a reloaded tab keeps its session (the CSRF token is in the tab's `sessionStorage`; no endpoint hands it out). Pairing is only needed for a typed URL. The dashboard's Agents tab shows each agent's connection (the same source as Connected Agents) and a New token action (`/api/agents/rotate`), as on the component's Agents page.
- 2A increment 3 (**implemented**; paired admin session):
  - **Project memory:** `GET /setup/api/memory` (where memory lives, record and task counts, the ledger state, the link state; an admin read that also needs the CSRF token), `POST /setup/api/memory/plan` (a preview with a digest; writes nothing) and `POST /setup/api/memory/migrate` (runs only for that exact digest). Three moves: `repo`, `embedded`, and `relink` for repository mode whose folder link was lost. The page shows the plan, including the other files written, and a Confirm button, as for Skills. The memory status refreshes about every 10 s.
  - **Token form:** a checkbox issues the capability `memory:repo:write` (builder role only, and refused when the token's actions exclude `update`); the agent list shows it.

**Paused state (S9, 2026-11-23).** While TouchDesigner runs no frames (a minimized window), `GET /setup/api/status` answers `503 TDFLOW_NOT_COOKING` at once; the page then says that TouchDesigner is not running frames and asks the user to restore the window. The static page itself is still served.

`Claimprojectmemory` in repository mode reads the record files on a background thread and refuses a folder whose `tdflow-project.json` names another `Projectid`. The `tdflowProject` component (not the TDflowMCP component) carries the read-only `Memoryauthority` and `Repopath` parameters; only a migration changes them.

It is deliberately separate from the Phase 2B Governance Dashboard (`/ui/`), sharing only the gate, admin session and static-file serving.
