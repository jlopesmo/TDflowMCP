# Setup Assistant, component parameters, compatibility and troubleshooting

**Status:** Design for owner-approved requirements P9–P12 and P14 ([ADR-0010](../decisions/0010-setup-assistant-and-component-parameters.md)). **The Phase 1A part is implemented** in the development build of `TDflowMCP.tox` (2026-11-22). It is verified in TouchDesigner 2025.32440 by the in-TD suite; see [Phase 1A component record](../research/2026-11-22-phase-1a-component.md). **The Phase 1B part (pairing, agent tokens, Enable MCP, Suspend Agents, agent and session information) is implemented** and verified by the Phase 1B in-TD acceptance suite. The 1C rows remain design only.

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

Two pages (owner UX correction, Phase 1C): **TDflowMCP** holds the essential controls (Version, Status, Status Detail, Open Setup, Setup URL, Enable MCP, Suspend Agents, pairing, Agents, Tell Your Agent, Copy Tell Agent, View Agent Instructions); **Advanced** holds the rest (Stop Server, Run Diagnostics, Compatibility, Port, Port Status, Project Root, Data Location, Data Location In Use, Data Status, Confirm Move, Claim As New Project, Project ID, Log Level). TouchDesigner custom parameter names are capitalised-then-lowercase. The 1A rows are **built** from [`src/component/manifest.json`](../../src/component/manifest.json) and verified in TD. Builder notes from S5:
- a menu's `.default` does not change its current value, so `.val` must be set explicitly;
- a `readOnly` parameter can still be set by script, which suits status parameters;
- a pulse fired on the same frame its Parameter Execute DAT is created is not delivered, so callbacks must be wired at least one frame before use.

| Parameter | Type | Default | Phase | Behaviour |
|---|---|---|---|---|
| `Version` | Str, read-only | build version | 1A | Component version + tool-surface version |
| `Status` | Str, read-only | `Off` | 1A | Current state + error code (`Off`, `Starting`, `Setup`, `Stopping`, `Error: <code>`) |
| `Statusdetail` | Str, read-only | — | 1A; **shortened in 1C** | One plain-language item, at most 160 characters: the newer of the latest note and the state message (listener errors always win), including **browser-launch and clipboard failures** (with the URL). Details live on the Setup page |
| `Opensetup` | Pulse | — | 1A | Run checks, start the Setup listener if allowed, and only after the bind gate passes open `http://127.0.0.1:<Port>/setup/` in the default browser. Uses Python's `webbrowser` on a background thread, which never touches TD. Verified end-to-end on 2025.32440. Pulsing again while serving re-opens the page |
| `Stopserver` | Pulse | — | 1A | Stop listener (forces `Enablemcp` off in 1B). From `Error`, it acknowledges the error and returns to `Off` |
| `Setupurl` | Str, read-only | empty | 1A | The Setup address while serving |
| `Port` | Int | **9480** (1024–65535) | 1A | Listener port. Never changed automatically. Changing it while active stops the listener; the new port is used on the next `Opensetup` |
| `Portstatus` | Str, read-only | `not checked` | 1A | `this component` / `in use by another process` / `in use (responds like TDflowMCP vX; unverified)` |
| `Rundiagnostics` | Pulse | — | 1A | Re-run compatibility checks. Shown as "Run Diagnostics"; previously proposed as `Runchecks` (renamed by the owner, 2026-11-22) |
| `Compatsummary` | Str, read-only | — | 1A | e.g. `9 pass · 1 warn · 0 fail` |
| `Projectroot` | Str | `/project1` | 1A (unused until 1B) | Host project root for scopes (agents can never target outside it) |
| `Datadir` | Str, read-only | `(not opened)` | 1A; **1C storage** | "Data Location In Use": `Project folder: .tdflowmcp\<name> (git-ignored)` or `Private storage: %LOCALAPPDATA%\TDflowMCP\projects\<id>\data` (never an absolute path, since parameters are saved in the `.toe`) |
| `Datalocation` | Menu | `project` | 1C (**implemented**) | `project` (`.tdflowmcp/` next to the `.toe`; unsuitable folders fall back to private storage) or `private`. Changing it turns MCP off and needs Confirm Move |
| `Datastatus` | Str, read-only | `not opened` | 1C (**implemented**) | Storage decision (`New`, `Ok`, `Fallback (<reason>)`, `Unsaved`), audit verification, or what needs confirmation (moved, copied, relocated, older data) |
| `Confirmmove` / `Claimproject` | Pulse | — | 1C (**implemented**) | Confirm Move: verify and adopt the audit log that moved with the project, or make a verified copy from the previous location (never moves or deletes the source). Claim As New Project: new identity and an empty audit log; existing data untouched; agents need new tokens |
| `Createprojectmemory` | Pulse | — | 2A (**implemented**) | Create the optional `tdflowProject` Base COMP under Project Root (tagged, read-only `Projectid` `tfp_…`, `Memoryauthority` = embedded, README). Refused if one exists or the audit store is not open; audited (`memory_create`). Save the project to keep it |
| `Claimprojectmemory` | Pulse | — | 2A (**implemented**) | Accept the `tdflowProject`'s current records as authoritative after edits outside TDflowMCP, an older save, deletions or a copy. Refused while a record-named DAT is not a valid record. Takes effect once the audited `memory_claim` event is durable |
| `Projectid` | Str, read-only | empty | 1C (**implemented**) | TDflowMCP identity of the project (`prj_` + 16 hex), saved in the `.toe`; not a secret |
| `Viewinstructions` | Pulse | — | 1C (**implemented**) | Open the full Tell Your Coding Agent text (the `tellagent` DAT) in a TouchDesigner viewer |
| `Tellagent` | Str, read-only | — | 1A | One-line state of the agent instructions (e.g. `Ready: connect · v0.1`) |
| `Copytellagent` | Pulse | — | 1A | Copy the full "Tell Your Coding Agent" text to the clipboard with `ui.clipboard`. Success is verified by reading it back; failure is reported in `Statusdetail`. The text is also shown on the Setup page (with its own Copy button) and in the internal `tellagent` DAT |
| `Enablemcp` | Toggle | **Off** | 1B (**implemented**) | Enable `/mcp`; refused, and switched back off, unless preconditions (§4) pass. Status Detail names the blocking checks. Never re-enabled silently after a reload |
| `Suspendagents` | Toggle | Off | 1B (**implemented**) | Kill switch; audited |
| `Pairingrequest` | Str, read-only | empty | 1B (**implemented**) | Non-secret match code of the single pending browser pairing request, e.g. `Pending: K7Q-42M (12:01:05)` ([security model §2.5](security-model.md#25-dashboard-and-setup-admin-sessions)) |
| `Approvepairing` / `Rejectpairing` | Pulse | — | 1B (**implemented**) | Approve or reject the pending request **from inside TD**. Approval turns that browser's cookie into an admin session |
| `Agentsummary` | Str, read-only | — | 1B (**implemented**) | Number of agents and paired admin sessions |
| `Loglevel` | Menu | `info` | 1A | Diagnostic log level |

Parameters never hold tokens or other long-lived secrets, because parameter values are saved into the `.toe`.

## 3. Compatibility checks

Each check yields `{id, result: pass|warn|fail|unknown, evidence, remediation_id}`. `unknown` is reported honestly (never upgraded to pass).

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
| `agent.clients` | Coding agents the user selected; optional `present`/`absent` detection of CLIs on PATH via `shutil.which` (no execution, no paths reported) | ≥1 supported client |
| `agent.skills` | Installed Skills manifest vs bundled version (1C). **Implemented as the Setup page's Skills table** (installed vs bundled, mismatch flagged), not as a compatibility check | match |
| `sec.selftest` | Gate rejects foreign/other-port Origin; bind verified; tokens hashed (1B) | all pass |

## 4. Preconditions to enable MCP (1B)

`td.build` not fail · `net.bind` pass · `net.port` free/self · `fs.datadir` pass · `audit.integrity` pass · `sec.selftest` pass · at least one enabled agent token exists; then a fresh verification, the `mcp enabled` record and a checkpoint must be confirmed durable before `/mcp` opens. Older builds may enable MCP **only** if all security self-tests pass — there is no override.

## 5. Guided troubleshooting

`src/setup/troubleshooting.json` maps each check failure to: cause, how to confirm, step-by-step fix, and links to TouchDesigner documentation. Allowed automatic actions are limited to TDflowMCP-internal, reversible ones: re-run checks, and propose a different `Port` for the user to confirm. The listener is **never restarted automatically**, least of all after a failed bind verification; only the user's own `Opensetup` pulse may try again. TDflowMCP **never** installs or upgrades packages, changes TouchDesigner preferences or search paths, edits firewall/OS settings, writes coding-agent configuration of any kind, or kills processes. The only file writes are Skills files and, after the user migrated project memory to a folder of the project, its record files. Both require an explicit, paired, previewed confirmation ([agent integration §3](agent-integration.md#3-client-aware-installation-consent-first), [continuity §8](continuity-and-memory.md#8-phase-2a-increment-3-repository-mode-2026-11-23)).

## 6. "Tell Your Coding Agent"

Generated from current server state — never hand-edited — in four sections:

1. **Setup**: what TDflowMCP is, the component version, current state and which checks fail.
2. **Connection** (1C): endpoint URL, a client- and shell-specific command whose `${TDFLOW_TOKEN}` placeholder stays literal (single quotes, user/local scope only; see [security model §10](security-model.md#10-setup-assistant-and-integration-safety)), and the expected Skills version.
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
- 2A increment 3 (**implemented**; paired admin session):
  - **Project memory:** `GET /setup/api/memory` (where memory lives, record and task counts, the ledger state, the link state; an admin read that also needs the CSRF token), `POST /setup/api/memory/plan` (a preview with a digest; writes nothing) and `POST /setup/api/memory/migrate` (runs only for that exact digest). Three moves: `repo`, `embedded`, and `relink` for repository mode whose folder link was lost. The page shows the plan, including the other files written, and a Confirm button, as for Skills. The memory status refreshes about every 10 s.
  - **Token form:** a checkbox issues the capability `memory:repo:write` (builder role only, and refused when the token's actions exclude `update`); the agent list shows it.

**Paused state (S9, 2026-11-23).** While TouchDesigner runs no frames (a minimized window), `GET /setup/api/status` answers `503 TDFLOW_NOT_COOKING` at once; the page then says that TouchDesigner is not running frames and asks the user to restore the window. The static page itself is still served.

`Claimprojectmemory` in repository mode reads the record files on a background thread and refuses a folder whose `tdflow-project.json` names another `Projectid`. The `tdflowProject` component (not the TDflowMCP component) carries the read-only `Memoryauthority` and `Repopath` parameters; only a migration changes them.

It is deliberately separate from the Phase 2B Governance Dashboard (`/ui/`), sharing only the gate, admin session and static-file serving.
