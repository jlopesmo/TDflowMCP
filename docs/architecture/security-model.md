# Security and permission model

**Status:** Design, not implemented. [ADR-0009](../decisions/0009-permission-model.md) and [ADR-0005](../decisions/0005-dashboard.md) are Conditionally Accepted (owner, 2026-11-22). The §10–§11 additions come from Proposed ADRs [0010](../decisions/0010-setup-assistant-and-component-parameters.md)–[0013](../decisions/0013-project-memory-model.md). Accepted security invariants are listed in [invariants.md](../icm/invariants.md). Revised after the [Phase 0 independent review](../research/2026-11-22-phase-0-independent-review.md).

## 1. Threat model

| Threat | Example | Primary control |
|---|---|---|
| Other local process or web page hitting the port | DNS rebinding from a browser tab; a page served by another localhost port; malware on localhost | Loopback bind (verified at startup), Host + Origin validation, bearer token on every MCP request, Origin + CSRF token on every dashboard request |
| Over-privileged agent | Agent told to edit `/project1/fx` deletes `/project1/output` | Server-side scoped CRUD grants, deny by default |
| **Code execution hidden inside "data" edits** | Setting a parameter *expression* (`__import__('os')…`, `op('/project1/secret').text`), writing text into an Execute/Script/callback DAT, creating an Execute/Web Client/TCP/Script operator, pulsing a script-running parameter | These are classified as the **`execute`** action, not `update`/`create` (§3.3). Executable op types are default-denied |
| Prompt-injected agent | DAT text instructs the agent to run code | `execute` off by default; approval with the **full raw content** shown; scopes still apply to facade calls |
| Arbitrary code escapes policy | `exec` reaches `op('/')` | Raw `execute_python` is the highest-risk grant, approval-gated and **documented as policy-bypassing** |
| Approval replay / TOCTOU | Agent gets approval, then the policy changes or a renamed op now resolves elsewhere | Single-use approvals redeemed only by the repeated call. Targets re-resolved and policy **re-evaluated at execution** (§4) |
| Tampered history | Edit audit rows to hide actions | Append-only store + hash chain, with **keyed** checkpoints exported outside the store (§6). Tamper-*evident*, not tamper-proof |
| Token leakage | Token in a committed `.mcp.json`, or pasted into a DAT | Tokens shown once, stored hashed outside the project, single-quoted env-var placeholders in user/local scope (§10), secret scanning (planned, Phase 1A CI) |
| Self-modification | Agent disables checks by editing the TDflowMCP COMP, another TDflowMCP instance, or an ancestor's bypass/cooking flags | Hard denies on every TDflowMCP instance (by tag) and on ancestors' flags/rename/move (§3.4) |
| Resource exhaustion | Huge bodies, unauthenticated floods, `while True` scripts freezing TD | Size caps, auth-fail rate limiting with aggregated audit (Phase 1A gate), cooperative budgets (§7). **Runaway in-process code cannot be killed**; this is a documented residual risk |
| Setup surface abused before MCP is enabled | A web page drives Setup actions (create tokens, enable MCP) | Setup listener starts only on a user action; read-only in 1A; paired admin session + exact Origin + CSRF for every action (§2.5, §10) |
| Unauthorized environment changes | Troubleshooting "fixes" by pip-installing, editing TD prefs or rewriting client config | Diagnose-and-instruct only. Writes are limited to Skills/config files the user previews and confirms. Never installs software or edits permission settings (§10) |
| Secrets leaking through generated text | Token embedded in "Tell Your Coding Agent" or a parameter saved into the `.toe` | Generated text and parameters never contain tokens; placeholders only (§10) |
| Prompt injection through memory | A previous agent's handoff text instructs the next agent to request raw exec | Agent-authored memory is labelled with its author and presented as untrusted data; only server-verified facts are marked verified (§11) |
| Lost work | Agent action corrupts the project, then TD crashes or restarts and the undo history is gone | Agents may not save the `.toe` by default. Pre-mutation snapshots are planned (Phase 3), and the kill switch (§2.4) applies |

**Out of scope (documented):** a malicious *human* operator of the machine; code already run with raw `execute_python` (full process access).

## 2. Identity

### 2.1 Agents
`{id, display_name, role, enabled, created_at, last_seen}`. Authenticated by a bearer token of the form `tdf_<token_id>.<secret>`. The `secret` is ≥ 256 bits from `secrets.token_urlsafe`, shown once, and stored as `sha256(salt || secret)` keyed by `token_id`, so lookup is O(1) and the secret is compared with `hmac.compare_digest`.

### 2.2 Roles
Named grant bundles. Proposed built-ins: `observer` (read), `builder` (read + create/update/delete in assigned scopes, **no execute**) and `operator` (builder + approval-gated execute). Custom roles are allowed.

### 2.3 Sessions
MCP 2026-07-28 is stateless, so a **session** in TDflowMCP is a dashboard/audit grouping: consecutive requests from one `token_id` with the same client info, ending after a configurable idle gap (proposed 15 min). Legacy `initialize` clients also report their declared client info. Sessions are an observability construct and never an authorization input.

### 2.4 Kill switch
A single "Suspend all agents" control is available as a TD component parameter (`Suspendagents`, Phase 1B) and as a dashboard button (Phase 2B). It rejects every MCP request with `SUSPENDED` until it is cleared, and the change itself is audited. Each agent can also be disabled individually.

### 2.5 Dashboard and Setup admin sessions
The Setup Assistant (1B+) and the Governance Dashboard (2B) share one admin credential, separate from agent tokens.

**Pairing (reverse flow; no secret ever stored in a parameter):**
1. The browser requests pairing. The server creates **one** pending request (any earlier pending request is cancelled). It holds a random 128-bit request secret bound to the browser by an HttpOnly cookie, plus a short, non-secret **match code** (e.g. `K7Q-42M`) shown on the page.
2. TouchDesigner shows `Pairingrequest` = `Pending: K7Q-42M (12:01:05)`. The user compares the code and pulses `Approvepairing` or `Rejectpairing` inside TD. Only someone physically at TD can approve.
3. On approval, the cookie becomes an admin session. Pending requests expire after 2 minutes. At most 3 requests are allowed per minute, and denials are audited.

The match code only prevents approving the wrong request. Holding it grants nothing, so it is safe in a saved parameter, in autosaves and on a shared screen.

**Request rules:** because **cookies are not isolated by port** (SameSite treats other `127.0.0.1` ports as same-site), the cookie is never relied on alone. See the [per-route gate table](#81-per-route-gate-table). The canonical origin is `http://127.0.0.1:<port>`; requests to `localhost` are redirected to it.

### 2.6 OAuth 2.1
Deferred (TDMCP uses it). It may be added behind the same identity interface if clients require it, which is an owner decision.

## 3. Permissions

### 3.1 Actions and scopes
**Actions:** `read`, `create`, `update`, `delete`, `execute`.

**Scopes:** glob patterns on absolute operator paths, where `*` matches one segment and `**` any depth. Before matching, every target is resolved to the concrete operator's `.path`: relative paths and `..` are resolved, and shortcuts and global OP shortcuts are followed. Unresolvable targets are denied. Matching is case-sensitive on the resolved path.

### 3.2 Grants and evaluation
A grant is `{subject: agent|role, scope, actions, effect: allow|deny, constraints}`. Constraints: `require_approval`, `max_ops_per_call`, `allowed_op_types`, `param_denylist`, `expires_at`.

1. Apply hard denies (§3.4). They cannot be overridden.
2. Collect the grants that match the subject (the agent plus its role).
3. Evaluate each `(action, target)`: an explicit deny wins, and no matching allow means deny.
4. An allow carrying `require_approval`, or a matching global rule (e.g. any `execute`, or `delete` of more than N ops), returns `require_approval`.
5. **Mutations:** every target must pass, otherwise the whole call is denied (no partial execution). **Recursive reads** (`path/**`) *filter* hard-denied and unpermitted subtrees out of the result and report `filtered: n`, rather than failing.

### 3.3 Action classification of tool effects (security-critical)

| Effect | Action(s) |
|---|---|
| Read operator, parameter values or DAT text | `read` |
| Set a parameter **constant** value | `update` (+ `param_denylist`) |
| Set a parameter **expression**, **bind** or **export**, or change its mode to one of those | `update` **and** `execute` on the target (expressions evaluate Python) |
| Write text into a DAT that TD executes: Execute/Script/callback DATs, any DAT referenced by a `callbacks`/`script`/extension parameter, or Text DATs used as modules | `update` **and** `execute` |
| Create a plain operator | `create` on `parent/*` (+ `allowed_op_types`) |
| Create an executable or network operator (Execute/Script/Web Client/TCP/UDP/WebSocket DATs, Script CHOP/SOP/TOP, Python-extension COMPs) | `create` **and** `execute`. These types are default-denied by `allowed_op_types` |
| Pulse a parameter | `update`. Pulses that run scripts (e.g. Run/Reload on script ops) also need `execute` |
| Wire or unwire | `update` on `dst`, `read` on `src` |
| Delete | `delete` on `path/**` |
| Undo an audited action | the inverse actions on the action's targets (create ↔ delete, update ↔ update) |
| Save the `.toe` | `execute` on `/**` and always approval. **Not offered in Phase 1** |
| Raw `execute_python`, Code Mode script | `execute` on `/**` (raw) or per facade call (Code Mode) |

The tool registry refuses a tool whose resolver doesn't declare its classification, and each row above gets allow and deny tests in Phase 1.

### 3.4 Hard denies (non-overridable)
- Every TDflowMCP component instance (identified by tag `TDflowMCP`, not by path) and its descendants, for all actions including `read` (it holds configuration).
- On every **ancestor** of a TDflowMCP instance: delete, rename/move, and changes to `bypass`, `allowCooking`, `cooking`-related flags and extension parameters.
- `/sys`, `/local`, `/ui`, and any path outside the configured host project root (`Projectroot`, default `/project1`).
- The optional `tdflowProject` COMP, and every **ancestor**'s delete, rename/move and bypass/cooking flags, for **raw operator tools**. It changes only through memory tools, which enforce the record schema and revision rules (§11).
- **Protected-set identity is registration, not tags.** The controller records the operator ids (`op.id`) of its own instance, other genuine TDflowMCP instances it discovers, and the `tdflowProject` COMP the user created or linked, in `Datadir`. Tags are used for discovery only. Agents may not add, remove or change the reserved tags `TDflowMCP` and `tdflowProject` (this is treated as a hard-denied update), so a decoy COMP cannot claim protection or block work.
- `tdflow_project_info` returns a fixed summary. The top-level COMP names it lists are **filtered to those the agent can read**.

## 4. Approvals

- Triggered by a grant constraint or a global rule. Phases 1A–2A have no approval surface, so `require_approval` resolves to **deny** with code `APPROVAL_UNAVAILABLE`. The approval flow ships in Phase 2B.
- A pending record stores: the agent, the RFC 8785 (JCS) canonical JSON of the arguments and its SHA-256, the resolved targets, the policy version, and the expiry (proposed 10 min).
- **Redemption is only by repeating the identical call** with `_meta.tdflow.approval_id`. `tdflow_approval_status` is read-only. At redemption the server checks the digest and agent, **re-resolves the targets and requires them to be identical**, and **re-evaluates policy** at the current policy version (a revoked grant cancels the approval). Approvals are single-use.
- The dashboard shows the **complete raw arguments** (full code, full expression text) and the resolved targets. An agent-written summary is never the only content.

## 5. Code execution honesty

In-process Python cannot be sandboxed, and RestrictedPython's own docs disclaim being a sandbox.
- `tdflow_execute_python` (raw) is **disabled by default**. When enabled it needs `execute` on `/**`, always requires approval, and is labelled **"bypasses scopes"**.
- Code Mode ([ADR-0004](../decisions/0004-code-mode-strategy.md)) passes scripts only a `tdflow` facade whose calls are policy-checked. The facade returns **plain data** (dicts, strings, numbers), never TD objects, because Python introspection (`__class__`, `__globals__`, frames) can escape any wrapper. Even so, the restricted namespace is a **mistake-reducer, not a security boundary**, and Code Mode requires approval by default.

## 6. Audit

- **Two events per executed call:** an `intent` event written *before* execution and an `outcome` event after. If the audit store is unavailable, mutating calls fail closed with `AUDIT_UNAVAILABLE`.
- Fields: `{seq, ts, kind, agent_id, token_id, session_id, client_info, method, tool, args_hmac, args_redacted, targets, decision, rule_id, policy_version, approval_id, outcome, error_code, duration_ms, undo_label, prev_hash, hash}`.
- `args_hmac` is an HMAC with a per-installation key, so low-entropy secrets cannot be brute-forced from a plain digest. `args_redacted` removes `Authorization` headers and token-shaped strings.
- `hash = sha256(prev_hash || jcs(event))`. A local attacker with file access could recompute an unkeyed chain, so periodic **checkpoints** (`seq`, `hash`, HMAC with a key kept outside the DB) are exported and verified on report generation. The resulting claim is **tamper-evident relative to the last exported checkpoint**.
- Unauthenticated failures are rate-limited and **aggregated** (one `auth_fail_burst` event per window), so floods cannot force a SQLite write per request on TD's main thread.

## 7. Resource limits (cooperative)

TD's main thread cannot pre-empt running Python. Limits are therefore:
- **Hard pre-checks:** body size (413), response size cap, pagination, and `max_ops_per_call` checked before execution.
- **Cooperative checks:** op counters and elapsed-time checks inside facade/tool loops abort between steps.
- **Post-hoc reporting:** durations above the budget are flagged in audit and the dashboard.
- **Residual risk:** a tight loop in raw exec or a Code Mode script freezes TD until it ends. This is why `execute` is approval-gated.

## 8. Network posture

- Bind `127.0.0.1` only. At startup the component verifies the effective bind and **refuses to start** unless loopback-only is positively confirmed. Firewalls and Windows routing can make a self-connection test pass or fail wrongly, so an **ambiguous result counts as failure** (fail closed). Spike S1 defines the evidence that counts: the DAT's reported address, a socket-level check of the listening address, and the non-loopback connection attempt. Remote/LAN access is out of scope for Phases 1–3 and needs an ADR (TLS via the Web Server DAT `secure` parameter, plus mTLS).

### 8.1 Per-route gate table

| Route | Method | Requirements (all must hold) |
|---|---|---|
| `/mcp` | POST | Valid Host. Origin absent (non-browser) **or** equal to the canonical origin, else 403 (spec MUST). Valid agent bearer token. State Enabled |
| `/setup/`, `/ui/` static files | GET | Valid Host. If `Sec-Fetch-Site` is present it must be `same-origin` or `none` |
| `/setup/api/*`, `/api/*` reads | GET | Valid Host + custom header `X-TDflow-Request: 1`. A cross-origin page cannot send a custom header without a CORS preflight, and the server never grants preflights. `Sec-Fetch-Site`, if present, must be `same-origin`. Reads after pairing also need the admin session |
| `/setup/api/*`, `/api/*` mutations | POST/PUT/DELETE | Valid Host + **Origin exactly canonical (a missing Origin is rejected)** + admin session cookie + `X-TDflow-CSRF` matching the session |
| `/api/events` | WebSocket upgrade | As for mutations: exact Origin + session + CSRF token in the first message |
| `/.well-known/tdflowmcp` | GET | Valid Host. Returns only `{product, version}`: no project name, path or instance details |

Responses never include `Access-Control-Allow-*` headers.

- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (defence in depth against DNS rebinding; not itself a spec requirement).
- TDflowMCP core makes no **non-loopback** outbound network calls. The only outbound request is the loopback port-ownership probe (`GET http://127.0.0.1:<port>/.well-known/tdflowmcp`) during conflict detection. It has a timeout of at most 200 ms, runs off the main thread or in non-blocking steps, and its answer is **untrusted**: it is shown only as "responds like TDflowMCP vX" and never drives any automatic action.

## 9. Concurrency between agents (Phase 1 rule)

Calls are serialized on TD's main thread. Concurrent agents can still interleave edits, so `tdflow_undo` only undoes an agent's action while it is on top of TD's undo stack, and otherwise returns `UNDO_CONFLICT`. Scope leases for multi-agent work are planned for Phase 4.

## 10. Setup Assistant and integration safety

- The Setup listener starts only on a user action (`Opensetup` or `Enablemcp`) and only after the loopback-bind self-test passes. If the bind cannot be verified, nothing listens; guidance is shown in parameters and an internal DAT.
- Phase 1A Setup is read-only: status, checks, troubleshooting text and "Tell Your Coding Agent". From 1B, actions (create or disable tokens, choose scope presets, enable MCP, Skills writes) require a paired admin session (§2.5).
- **No unauthorized changes:** TDflowMCP never installs packages or software, never changes TouchDesigner preferences or Python search paths, never edits firewall or OS settings, never writes coding-agent configuration of any kind (MCP server entries, permission or approval settings), and never kills processes. MCP client connection is **instruction-only**: the user runs the generated command or pastes the snippet. The only file writes are **Skills files**, in assisted-write mode. They require a preview, explicit confirmation, a hash manifest and an audit event, and may target only the registry-listed skills directories ([agent integration §3](agent-integration.md#3-client-aware-installation-consent-first)).
- **Secrets:** custom parameters are saved into the `.toe`, so they never hold tokens. Pairing uses non-secret match codes (§2.5). Tokens are displayed once, in the Setup page only.
- **Token placeholders and shells:** generated commands keep `${TDFLOW_TOKEN}` literal by using **single quotes**, which are literal in both POSIX shells and PowerShell. A double-quoted form would let bash or zsh expand the real token into the client config, and in PowerShell it would expand an empty variable. Generated commands use user/local scope, never project scope (which writes a committable `.mcp.json`). Whether each client expands `${VAR}` in stored headers at runtime is **[verify in 1C]**. A 1C test asserts that no client config file written by following the instructions contains the token.
- **Generated text hygiene ("Tell Your Coding Agent", parameters):** only allowlisted fields are included (versions, state, check ids/results, fixed remediation text, endpoint URL). Environment-derived strings (operator names, probe answers, error messages) are excluded or truncated and fenced as data. Paths are shown in `%LOCALAPPDATA%\TDflowMCP\<key>` form, never expanded with a username. The resume section is **reference-only by default** (handoff id, verified-fact counts). Agent-authored summaries are returned only through `tdflow_handoff_resume`, fenced and labelled.
- Client detection reports only `present`/`absent` from `shutil.which`, never paths, plus user selection. TDflowMCP does not read client config files unless the user points it at one (read-only).

## 11. Project memory and continuity safety

- Exactly one authoritative memory home ([ADR-0013](../decisions/0013-project-memory-model.md)). Writes carry `based_on_rev`, and stale writes return `MEMORY_CONFLICT`. A `Projectid` mismatch blocks linking.
- Memory tools act on dedicated pseudo-scopes `memory:<record-type>` (e.g. `memory:handoff`) with actions `read`, `create`, `update` and `delete`. Memory access is therefore granted separately from operator scopes. `delete` is denied by default; records are archived instead.
- **Fork and rollback detection:** Save As, incremental saves or copying the COMP can clone an embedded authority, and a repo checkout can move `rev` backwards. `Datadir` keeps, per `Projectid`, the registered authority instance (COMP `op.id` + `.toe` identity) and each record's highest seen `rev`. A mismatch puts memory into `FORKED` or `REWOUND` read-only state until the user performs an explicit **Claim authority** (audited) ([continuity §2](continuity-and-memory.md#2-authority-model-avoids-conflicting-authoritative-records)).
- Memory and handoffs never contain tokens, raw audit arguments or user-profile paths. They reference audit by sequence number and hash.
- Agent-authored content is returned labelled `author: agent:<id>` and is never presented as an instruction from the user or the server. Only server-derived facts are labelled `verified`.
