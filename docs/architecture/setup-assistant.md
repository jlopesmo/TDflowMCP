# Setup Assistant, component parameters, compatibility and troubleshooting

**Status: Proposed design** for owner-approved requirements P9–P12 and P14 ([ADR-0010](../decisions/0010-setup-assistant-and-component-parameters.md)). Not implemented. API details marked **[verify]** must be confirmed in spike S5.

## 1. Component states

| State | Listener | `/setup` | `/mcp` | `/ui` (dashboard) | How entered |
|---|---|---|---|---|---|
| **Off** (default after drop-in) | none | — | — | — | Initial; `Stopserver` pulse; idle timeout |
| **Setup** | 127.0.0.1:`Port` | read-only (1A); paired actions (1B) | 503 `MCP_DISABLED` | 404 until Phase 2B | `Opensetup` pulse |
| **Enabled** | 127.0.0.1:`Port` | available | active (policy-enforced) | Phase 2B | `Enablemcp` on, after preconditions (§4) |
| **Suspended** | 127.0.0.1:`Port` | available | `SUSPENDED` for all agents | Phase 2B | `Suspendagents` on (kill switch) |
| **Error** | none | — | — | — | Bind not verifiable, port conflict, storage failure |

The component **never listens until the user acts** (pulse or toggle). The listener only starts after the loopback-bind self-test passes; if it fails the state is `Error: BIND_UNVERIFIED` and the only guidance channel is the component's parameters and an in-component text DAT — never a less-secure listener. In Setup state with MCP disabled, the listener auto-stops after an idle timeout (proposed 30 min).

## 2. Essential custom parameters

Page **TDflowMCP** (TouchDesigner custom parameter names are capitalised-then-lowercase):

| Parameter | Type | Default | Phase | Behaviour |
|---|---|---|---|---|
| `Version` | Str, read-only | build version | 1A | Component version + tool-surface version |
| `Status` | Str, read-only | `Off` | 1A | Current state + error code |
| `Opensetup` | Pulse | — | 1A | Run checks, start Setup listener if needed, open `http://127.0.0.1:<Port>/setup/` in the default browser **[verify: `ui.viewFile`/`webbrowser` from TD]** |
| `Stopserver` | Pulse | — | 1A | Stop listener (forces `Enablemcp` off) |
| `Port` | Int | **9480** | 1A | Listener port; changes take effect on restart; never changed automatically |
| `Portstatus` | Str, read-only | — | 1A | `free` / `in use (responds like TDflowMCP vX; unverified)` / `in use by another process` |
| `Runchecks` | Pulse | — | 1A | Re-run compatibility checks |
| `Compatsummary` | Str, read-only | — | 1A | e.g. `9 pass · 1 warn · 0 fail` |
| `Projectroot` | Str | `/project1` | 1A | Host project root for scopes (agents can never target outside it) |
| `Datadir` | Str, read-only | — | 1A | Location of runtime state, displayed unexpanded as `%LOCALAPPDATA%\TDflowMCP\<key>` (no username saved into the `.toe`) |
| `Tellagent` | Str, read-only | — | 1A | One-line state of the agent instructions (e.g. `Ready: connect · v0.1`) |
| `Copytellagent` | Pulse | — | 1A | Copy full "Tell Your Coding Agent" text to the clipboard **[verify clipboard API]**; also shown on the Setup page and in an internal DAT |
| `Enablemcp` | Toggle | **Off** | 1B | Enable `/mcp`; refused unless preconditions (§4) pass |
| `Suspendagents` | Toggle | Off | 1B | Kill switch |
| `Pairingrequest` | Str, read-only | empty | 1B | Non-secret match code of the single pending browser pairing request, e.g. `Pending: K7Q-42M (12:01:05)` ([security model §2.5](security-model.md#25-dashboard-and-setup-admin-sessions)) |
| `Approvepairing` / `Rejectpairing` | Pulse | — | 1B | Approve or reject the pending request **from inside TD**. Approval turns that browser's cookie into an admin session |
| `Loglevel` | Menu | `info` | 1A | Diagnostic log level |

Parameters never hold tokens or other long-lived secrets, because parameter values are saved into the `.toe`.

## 3. Compatibility checks

Each check yields `{id, result: pass|warn|fail|unknown, evidence, remediation_id}`. `unknown` is reported honestly (never upgraded to pass).

| ID | Check | Pass rule (provisional) |
|---|---|---|
| `td.build` | `app.build` / `app.version` | ≥ **2025.33230** → pass; older → `warn: untested build` (MCP enablement then requires every security self-test to pass). Also flags a `.tox` saved by a newer build than the running one |
| `py.version` | `sys.version_info` | 3.11.x |
| `py.deps` | `jsonschema`, `sqlite3`, `ssl`, `hmac`, `secrets` importable; versions recorded | all present |
| `net.bind` | Loopback-only bind self-test | verified |
| `net.port` | Port 9480 (or `Port`) free / owned by this instance; identify other TDflowMCP instances via `GET /.well-known/tdflowmcp` on loopback | free or self |
| `fs.datadir` | Runtime data dir writable, outside project and repo | writable |
| `td.instances` | Number of TDflowMCP components in the project | exactly one enabled |
| `agent.clients` | Coding agents the user selected; optional `present`/`absent` detection of CLIs on PATH via `shutil.which` (no execution, no paths reported) | ≥1 supported client |
| `agent.skills` | Installed Skills manifest vs bundled version (1C) | match |
| `sec.selftest` | Gate rejects foreign/other-port Origin; bind verified; tokens hashed (1B) | all pass |

## 4. Preconditions to enable MCP (1B)

`td.build` not fail · `net.bind` pass · `net.port` free/self · `fs.datadir` pass · `sec.selftest` pass · at least one enabled agent token exists. Older builds may enable MCP **only** if all security self-tests pass — there is no override.

## 5. Guided troubleshooting

`src/setup/troubleshooting.json` maps each check failure to: cause, how to confirm, step-by-step fix, and links to TouchDesigner documentation. Allowed automatic actions are limited to TDflowMCP-internal, reversible ones: re-run checks, restart the listener, and propose a different `Port` for the user to confirm. TDflowMCP **never** installs or upgrades packages, changes TouchDesigner preferences or search paths, edits firewall/OS settings, writes coding-agent configuration of any kind, or kills processes. The only file writes are Skills files, and they require an explicit, paired, previewed confirmation ([agent integration §3](agent-integration.md#3-client-aware-installation-consent-first)).

## 6. "Tell Your Coding Agent"

Generated from current server state — never hand-edited — in four sections:

1. **Setup**: what TDflowMCP is, the component version, current state and which checks fail.
2. **Connection** (1C): endpoint URL, a client- and shell-specific command whose `${TDFLOW_TOKEN}` placeholder stays literal (single quotes, user/local scope only; see [security model §10](security-model.md#10-setup-assistant-and-integration-safety)), and the expected Skills version.
3. **Repair**: for each failing check, the instructions from troubleshooting — addressed to the agent, with the rule "do not install software or change configuration without asking the user".
4. **Resume** (2A): **reference-only by default**: the latest handoff id, its verification counts and a pointer to call `tdflow_handoff_resume`. Agent-authored summaries are never inlined here.

Only allowlisted fields are included. Never included: tokens, environment-derived free text (operator names, probe answers, raw errors), audit contents beyond counts, or expanded user-profile paths.

## 7. Setup Assistant (web)

`/setup/` — a single static page (vanilla HTML/JS, no build, no CDN) plus JSON endpoints:
- 1A (read-only): `GET /setup/api/status` (state + checks), `GET /setup/api/tellagent`, troubleshooting view. Every read requires the `X-TDflow-Request: 1` header and a valid Host ([gate table](security-model.md#81-per-route-gate-table)).
- 1B (paired admin session, exact Origin + CSRF, [security model §2.5](security-model.md#25-dashboard-and-setup-admin-sessions)): pairing request, create/disable agent tokens (each token shown once), choose scopes from a preset, enable MCP.
- 1C: client selection, connection instructions, Skills preview/install/update.

It is deliberately separate from the Phase 2B Governance Dashboard (`/ui/`), sharing only the gate, admin session and static-file serving.
