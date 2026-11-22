# Security and permission model

**Status: Proposed** ([ADR-0005](../decisions/0005-dashboard.md), [ADR-0009](../decisions/0009-permission-model.md)). Not implemented. Accepted security invariants are listed in [invariants.md](../icm/invariants.md). Revised after the [Phase 0 independent review](../research/2026-11-22-phase-0-independent-review.md).

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
| Token leakage | Token in a committed `.mcp.json`, or pasted into a DAT | Tokens shown once, stored hashed outside the project, env-var client configuration recommended, secret scanning (planned, Phase 1 CI) |
| Self-modification | Agent disables checks by editing the TDflowMCP COMP, another TDflowMCP instance, or an ancestor's bypass/cooking flags | Hard denies on every TDflowMCP instance (by tag) and on ancestors' flags/rename/move (§3.4) |
| Resource exhaustion | Huge bodies, unauthenticated floods, `while True` scripts freezing TD | Size caps, auth-fail rate limiting with aggregated audit (Phase 1), cooperative budgets (§7). **Runaway in-process code cannot be killed**; this is a documented residual risk |
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
A single "Suspend all agents" control is available as a TD component parameter (Phase 1) and as a dashboard button (Phase 2b). It rejects every MCP request with `SUSPENDED` until it is cleared, and the change itself is audited. Each agent can also be disabled individually.

### 2.5 Dashboard admin
A separate credential. A one-time pairing code shown inside TD is exchanged for an HttpOnly session cookie. Because **cookies are not isolated by port** and SameSite treats other `127.0.0.1` ports as same-site, the cookie is **not** relied on alone. Every `/api` request and the WebSocket upgrade require (a) an `Origin` exactly equal to the canonical dashboard origin `http://127.0.0.1:<port>` (a missing Origin is rejected), and (b) an `X-TDflow-CSRF` header matching the session's token. `localhost` is not a canonical origin; requests to `localhost` are redirected to it.

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
- `/sys`, `/local`, `/ui`, and any path outside the configured host project root (default `/project1`; configurable).
- `tdflow_project_info` returns a fixed summary only and does not need read on `/`.

## 4. Approvals

- Triggered by a grant constraint or a global rule. In **Phase 1** there is no approval surface, so `require_approval` resolves to **deny** with code `APPROVAL_UNAVAILABLE`. The approval flow ships in Phase 2b.
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

- Bind `127.0.0.1` only. At startup the component verifies the effective bind (by trying a connection on a non-loopback local interface where one exists, and by checking the DAT's reported address) and **refuses to start** if loopback-only cannot be confirmed. Remote/LAN access is out of scope for Phases 1–3 and needs an ADR (TLS via the Web Server DAT `secure` parameter, plus mTLS).
- `/mcp`: a missing Origin is accepted (non-browser clients); a present Origin must equal the canonical dashboard origin, otherwise 403 (MCP spec MUST). `/api` and `/ui` mutations: exact Origin required (§2.5).
- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (defence in depth against DNS rebinding; not itself a spec requirement).
- TDflowMCP core makes no outbound network calls.

## 9. Concurrency between agents (Phase 1 rule)

Calls are serialized on TD's main thread. Concurrent agents can still interleave edits, so `tdflow_undo` only undoes an agent's action while it is on top of TD's undo stack, and otherwise returns `UNDO_CONFLICT`. Scope leases for multi-agent work are planned for Phase 4.
