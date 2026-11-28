# Security and permission model

**Status:** Design. **Phase 1B implements §2.1–2.5, §3, §4 (approval ⇒ deny), §6 and §8–9** for the 16 Phase 1B tools ([Phase 1B record](../research/2026-11-22-phase-1b-mcp-core.md)); §5 (code execution) remains disabled; §7 cooperative budgets apply only as pre-checks (`max_ops_per_call`, size caps); §10–§11 memory parts are Phase 2A. [ADR-0009](../decisions/0009-permission-model.md) and [ADR-0005](../decisions/0005-dashboard.md) are Conditionally Accepted (owner, 2026-11-22). The §10–§11 additions come from Proposed ADRs [0010](../decisions/0010-setup-assistant-and-component-parameters.md)–[0013](../decisions/0013-project-memory-model.md). Accepted security invariants are listed in [invariants.md](../icm/invariants.md). Revised after the [Phase 0 independent review](../research/2026-11-22-phase-0-independent-review.md).

## 1. Threat model

| Threat | Example | Primary control |
|---|---|---|
| Other local process or web page hitting the port | DNS rebinding from a browser tab; a page served by another localhost port; malware on localhost | Loopback bind verified before any application request is served (§8.2), Host + Origin validation, bearer token on every MCP request, Origin + CSRF token on every dashboard request |
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

**Changing permissions (owner D25, [ADR-0016](../decisions/0016-capabilities-page-and-permission-editing.md)).** An agent's role, grants and dedicated permissions can be changed after creation, only through the admin backend (paired dashboard session with CSRF, or TouchDesigner's own controls):
- `POST /api/permissions/preview` computes the effective result and stores nothing.
- `POST /api/permissions/apply` must carry the preview's digest, which binds the stored state and the change (`PREVIEW_CHANGED` otherwise).
- **More access needs `confirm_escalation: true`** (`CONFIRMATION_REQUIRED` otherwise). More access means any of:
  - a higher role;
  - an added allow grant;
  - actions added to a grant;
  - a removed deny;
  - an added dedicated permission;
  - more capabilities or wider scopes in the effective matrix, now or once the agent is enabled and Code Mode is on.
- The record is durable before the change. Refusals are audited too.
- Role, grants, dedicated permissions and the cancellation of the agent's open approvals happen in **one SQLite transaction**. If the in-memory reload then fails, the agent's tokens are blocked until a reload (fail closed).
- Allow grants never exceed the new role; scopes must lie inside the project root.
- Hard denies, the parameter deny list, the creation allowlist, approval rules and Code Mode's setting are not editable.
- The older `/api/grants/add|remove` need the same confirmation from the web for an allow grant or the removal of a deny.
- The capability matrix is a **view**: the engine still decides every call per path.

### 2.3 Sessions
MCP 2026-07-28 is stateless, so a **session** in TDflowMCP is a dashboard/audit grouping: consecutive requests from one `token_id` with the same client info, ending after a configurable idle gap (proposed 15 min). Legacy `initialize` clients also report their declared client info. Sessions are an observability construct and never an authorization input.

### 2.4 Kill switch
A single "Suspend all agents" control is available as a TD component parameter (`Suspendagents`, **implemented in Phase 1B**) and as a dashboard button (Phase 2B).
- It rejects every MCP request with `SUSPENDED` until it is cleared, and the change itself is audited.
- **Queued requests are invalidated:** jobs are re-checked when they are dispatched on the main thread, so a request queued just before the switch never runs (in-TD test).
- The engine checks the flag again before executing.
- Each agent's tokens can also be revoked individually; revocation is immediate.

### 2.5 Dashboard and Setup admin sessions
The Setup Assistant (1B+) and the Governance Dashboard (2B) share one admin credential, separate from agent tokens.

**Pairing (reverse flow; no secret ever stored in a parameter):**
1. The browser requests pairing. The server creates **one** pending request (any earlier pending request is cancelled). It holds a random 128-bit request secret bound to the browser by an HttpOnly cookie, plus a short, non-secret **match code** (e.g. `K7Q-42M`) shown on the page.
2. TouchDesigner shows `Pairingrequest` = `Pending: K7Q-42M (12:01:05)`. The user compares the code and pulses `Approvepairing` or `Rejectpairing` inside TD. Only someone physically at TD can approve.
3. On approval, the cookie becomes an admin session. Pending requests expire after 2 minutes. At most 3 requests are allowed per minute, and denials are audited.

The match code only prevents approving the wrong request. Holding it grants nothing, so it is safe in a saved parameter, in autosaves and on a shared screen.

**Pages opened from TouchDesigner (owner D22, 2026-11-24, [ADR-0015](../decisions/0015-consent-based-client-connection.md) §8).**
- The rc2 one-click ticket is **retired**, and its route is no longer served.
  - Why: the ticket travelled in the URL fragment, so it was visible on the browser's command line to other processes of the user.
  - The 5-s warm-up and race detection limited the damage, but a racing process kept its session whenever the real page failed to load.
- **Now:**
  1. When the user pulses **Open Setup** or **Open Dashboard**, the browser opens `/setup/#pair` or `/ui/#pair`. The address carries no secret.
  2. The page asks to pair at once (the reverse flow above).
  3. For 90 s after that pulse, the pairing request opens a **TouchDesigner dialog**. **Number matching (review 19):** the dialog offers the real match code among two random decoys, and the user picks the code the page shows. Only that request is approved (`expect`); a wrong pick denies it. No dialog opens over another open dialog. **Layout (D25):** TouchDesigner's dialog is a fixed 260 px, so the codes are numbered lines of the text (`Button 1:  ABC-DEF`) and the buttons are `1`, `2`, `3` and `Deny`; the choices, the mapping and the denial of a wrong pick are unchanged.
  4. A pairing request that nobody launched from TouchDesigner never opens a dialog; it waits on the Web page as before.
- **Residual:** a local process could create the request before the real page does. A request is never replaced, so the real page then reports a pending request. The user sees a code in TouchDesigner that the page does not show, and is told to approve only matching codes. Nothing is granted without that approval.
- Typed URLs pair exactly as before.
- **Agents and the dashboard address (D25):** `tdflow_capabilities` and `tdflow_whoami` give agents the dashboard's address. It is the MCP endpoint's own host and port and carries no credential. They tell agents to send the user to **Open Dashboard** in TouchDesigner (so the dialog appears), never to approve a sign-in TouchDesigner did not open for them. A link an agent hands over would lead to the Web page fallback, where the residual above applies.

**Reloads.** The page keeps its CSRF token in the tab's `sessionStorage`, never in a cookie or the URL, so reloading the same tab keeps the session while it is valid. There is no endpoint that hands out the CSRF token: a stolen cookie alone stays useless. **End Browser Session** signs every browser out.

**Native administration.** The controls on the component's Agents and TDflowMCP pages call the **same** admin handler as the dashboard, with the principal `admin:touchdesigner`: someone at TouchDesigner, the trust that pairing approval already relies on.
- The same validation, audit records and locks apply. Native actions are also recorded as `native_action` with `via: touchdesigner`.
- That principal is never produced from an HTTP request: HTTP principals come only from the authenticator (`admin:<sid hash>`, `agent:<token id>`).
- **Approvals:** Approve Request and Deny Request decide only the request that View Approval showed. The backend refuses a different argument digest (`APPROVAL_CHANGED`). The viewer is cleared after the decision or after 2 minutes, so agent arguments do not stay in a DAT that is saved with the project.
- **Connect Agent (owner D22, [ADR-0015](../decisions/0015-consent-based-client-connection.md)):**
  - For a client with automatic setup (Claude Code; Codex on Windows, [ADR-0017](../decisions/0017-codex-one-click-connection.md)), the new token goes from the backend straight into the user's protected store, on a background thread. That store is a Windows DPAPI current-user file under the private root, or the macOS Keychain through `security -i` on stdin. Each client has its own slot, so its own agent identity and token.
  - The token is never in memory longer than that step, and never in the clipboard, a parameter, a DAT, a log, the audit or the client's configuration.
  - The client's headers helper (`headersHelper` for Claude Code, `http_headers_helper` for Codex) reads it for the project that the listener announces. It releases the token only after the listener proves this project's connect key: an HMAC over the helper's fresh nonce via `/.well-known/tdflowmcp-proof/<nonce>`. A port squatter therefore gets nothing (review 19).
  - **When the helper fails:** for Claude Code it prints `{}` and the connection fails. For Codex it exits with status 1 and prints nothing, because Codex was observed to send its requests without credentials after an empty object, and to send no request at all after a non-zero exit (ADR-0017).
  - The helper's output bypasses PowerShell transcription.
  - The `connect_view` viewer is cleared after 2 minutes, because it is saved with the project, and paths in it are shown in `%LOCALAPPDATA%` form.
- **Tokens for the manual fallback:** a token created there (Connect Agent for a client without automatic setup, New Token) stays in memory for at most 2 minutes and leaves it only through **Copy Token**. Copy Token writes it to the Windows clipboard with the formats that keep it out of Clipboard History and Cloud Clipboard and ask clipboard monitors to ignore it. If that is not possible, the copy is refused rather than made plainly. The clipboard is cleared after a minute if it still holds the token. The token is never written to a parameter, a DAT or a log.

**Request rules:** because **cookies are not isolated by port** (SameSite treats other `127.0.0.1` ports as same-site), the cookie is never relied on alone. See the [per-route gate table](#81-per-route-gate-table). The canonical origin is `http://127.0.0.1:<port>`; requests to `localhost` are redirected to it.

### 2.6 OAuth 2.1
Deferred (TDMCP uses it). It may be added behind the same identity interface if clients require it, which is an owner decision. It is the documented route to automatic, secret-free setup for Cursor and VS Code Copilot, which have no helper mechanism ([ADR-0015](../decisions/0015-consent-based-client-connection.md)). Codex no longer needs it on Windows: it has a header helper ([ADR-0017](../decisions/0017-codex-one-click-connection.md)).

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
| Compare two cached TOP outputs | `read` on both resolved TOPs. Recheck target identities, current grants, both locks, enclosing bypass flags and the verified runtime before sampling pixels. |
| Replace an expression, bind or export with a constant | `update` and `execute`, because audited undo can restore the dynamic mode |
| Set a parameter **expression**, **bind** or **export**, or change its mode to one of those | `update` **and** `execute` on the target (expressions evaluate Python) |
| Add or edit bounded scalar custom definitions | `read`, `update` and `execute` on the Base or Container COMP, per-control update checks and exact approval. Existing project callbacks can observe definitions and values. |
| Write text into a DAT that TD executes: Execute/Script/callback DATs, any DAT referenced by a `callbacks`/`script`/extension parameter, or Text DATs used as modules | `update` **and** `execute` |
| Create a plain operator | `create` on `parent/*` (+ `allowed_op_types`) |
| Create an executable or network operator (Execute/Script/Web Client/TCP/UDP/WebSocket DATs, Script CHOP/SOP/TOP, Python-extension COMPs) | `create` **and** `execute`. These types are default-denied by `allowed_op_types` |
| Pulse a parameter | `update`. Pulses that run scripts (e.g. Run/Reload on script ops) also need `execute` |
| Wire or unwire | `update` on `dst`, `read` on `src` |
| Create, delete or change a wire into an Out TOP | Normal create, delete or wire checks plus `update` on the enclosing COMP. Refuse edits when its output is externally connected. |
| Delete | `delete` on `path/**` |
| Undo an audited action | the inverse actions on the action's targets (create ↔ delete, update ↔ update) |
| Save the `.toe` | `execute` on the project root and always approval (`tdflow_save_project`, Phase 3) |
| Copy a component (experiment) | read on every source operator, create per copied operator, and execute for code carried into the copy (approval binds its hash) |
| Raw `execute_python`, Code Mode script | `execute` on `/**` (raw, not built); Code Mode: `execute` on the script's declared `scope`, always approval of the exact text, and every facade call classified like the same call made directly |

The tool registry refuses a tool whose resolver doesn't declare its classification, and each row above gets allow and deny tests in Phase 1.

### 3.4 Hard denies (non-overridable)
- Every TDflowMCP component instance (identified by tag `TDflowMCP`, not by path) and its descendants, for all actions including `read` (it holds configuration).
- On every **ancestor** of a TDflowMCP instance: delete, rename/move, and changes to `bypass`, `allowCooking`, `cooking`-related flags and extension parameters.
- `/sys`, `/local`, `/ui`, and any path outside the configured host project root (`Projectroot`, default `/project1`).
- The optional `tdflowProject` COMP, and every **ancestor**'s delete, rename/move and bypass/cooking flags, for **raw operator tools**. It changes only through memory tools, which enforce the record schema and revision rules (§11).
- **Protected-set identity is registration, not tags.** The controller records the operator ids (`op.id`) of its own instance, other genuine TDflowMCP instances it discovers, and the `tdflowProject` COMP the user created or linked, in `Datadir`. Tags are used for discovery only. Agents may not add, remove or change the reserved tags `TDflowMCP` and `tdflowProject` (this is treated as a hard-denied update), so a decoy COMP cannot claim protection or block work.
- `tdflow_project_info` returns a fixed summary. The top-level COMP names it lists are **filtered to those the agent can read**.

## 4. Approvals

**Implemented in Phase 2B increment 1** ([record](../research/2026-11-23-phase-2b-increment-1.md)). Phases 1A–2A had no approval surface, so `require_approval` resolved to **deny** (`APPROVAL_UNAVAILABLE`); that is still the answer for effects that cannot be approved yet.
- **What can be approved** (operator role for A1; owner D20 for the code-running increments):
  - A1, `tdflow_set_parameters`: expressions; parameters that name operators or files; **switching an executable operator on or off** (`active`, classified as execute since D20); on the six Execute DAT types **every parameter** (callback toggles, watched operators and parameters decide when code runs; review 14).
  - A1, `tdflow_set_dat_text`: the six supported Execute DATs and untagged Text or Table DATs. The code edit binds the previous exact source hash and operator identity. Execute DAT code writes and restores require constant `active=false`. Undo restores the durable snapshot only into the same operator and checks the written source and snapshot against the audit record.
  - A1, `tdflow_create_operator`: **the six Execute DAT types** (`executeDAT`, `chopexecuteDAT`, `datexecuteDAT`, `opexecuteDAT`, `parameterexecuteDAT`, `panelexecuteDAT`), **created switched off** (staging): code and activation are separate approvals.
  - A2, `tdflow_delete_operator`: deleting more than 50 operators.
  - Not approvable: wiring into DATs, *creating* Script operators, network and file-system operator types, raw Python. Executable types other than the six stay outside the creation allowlist for every role. Hard denies, role, grants, allowlists and limits are evaluated first, so a call they deny never becomes a request.
- A pending record stores: the agent, the canonical JSON of the arguments (sorted keys, no NaN) and its SHA-256 over tool and arguments, the resolved targets, the policy version, the reasons and a 10-minute expiry. At most 5 pending requests per agent; arguments containing a token are refused before storage.
- **Approval rows live in the private credentials database** (per-user storage, with the tokens), never in the project folder ([review 13](../research/2026-11-23-phase-2b-review.md)). Before a decision the digest of the stored arguments is recomputed. The decision is audited before it takes effect and carries a MAC over the request with the installation key. Arguments are deleted once a request is used or cancelled.
- The approval binds the target **facts** (action, path, change, parameter, operator count, operator type), compared at redemption and at execution. Arguments with invisible or text-direction characters cannot be approved.
- **Redemption is only by repeating the identical call** (same agent, tool and argument digest; coding agents cannot attach `_meta`, so an `approval_id` in `_meta.tdflow` is optional and must match if sent). Redemption and marking as used happen in one transaction (single use, also under concurrent re-calls). Execution then re-evaluates policy with only rules A1/A2 lifted and requires the same policy version. `tdflow_approval_status` is read-only.
- Undoing an approved change needs `update`, not a new approval: undo only restores the verified state from right before the agent's own change. At redemption the server checks the digest and agent, **re-resolves the targets and requires them to be identical**, and **re-evaluates policy** at the current policy version (a revoked grant cancels the approval). Any grant change cancels all of that agent's open requests, because adding and removing the same grant restores the old policy version (review 14). Approvals are single-use.
- The dashboard shows the **complete raw arguments** (full code, full expression text) and the resolved targets. An agent-written summary is never the only content.
- In v1.1, expression approvals bind the running instance and operator identities, affected parameter states and recognized literal dependency identities. This checks the editable state before the approved call. Dynamic Python references and downstream evaluation are controlled by TouchDesigner. Parameter inspection does not call `eval()` for expressions, binds or exports.
- TouchDesigner 2025.32440 cooks a Script DAT when its `.text` is read. Source inspection and restoration are restricted to authored, unwired and unlinked Text, Table and supported Execute DATs. Experiment planning hashes authored source only and skips inspecting unsupported executable outputs.
- TouchDesigner 2025.32440 can cook a Script TOP when a tool reads dimensions or image bytes, including through a downstream TOP. Under the owner-approved v1.1 decision, both paths are restricted to the verified Windows runtime with TouchDesigner 2025.32440. The authorized main-thread step checks for a locked TOP with bypass off on the TOP and its ancestors before it reads the cached output. Other runtimes refuse capture and omit resolution-derived hints. TouchDesigner exposes the lock state, and the owner sets it directly. Existing approval-gated Python has access to the host.
- Execute DAT trigger approvals bind the exact source, parameter settings, input identities and bypass state. The native viewer and browser show the complete callback source and settings without evaluating dynamic values. The private preview digest is matched at decision and bound into the approved facts. It is cleared from the approval row when the request closes. Callback source and activation are approved in separate calls. If the callback preview is refused, the owner can switch off Active on the Execute DAT in TouchDesigner.
- An approval is usable with the requesting credential. Token revocation cancels its open requests in the same private database transaction. The engine reloads the in-memory token and checks policy and suspension again immediately before dispatch, after resolving and hashing the target state.
- Custom definitions bind the COMP identity, current named control definitions, page order and runtime to the approval. Snapshots are durable before dispatch and reject credential-shaped text. Recovery checks the target, current state, runtime and native undo stack before restoring definitions and order. This restores the interface state. Existing project callbacks can have effects beyond that state. If recovery cannot be verified, the engine suspends further agent calls.
- The undo request can bind the exact audited change using `expected_audit_seq`. The engine serializes selection, restoration and the durable outcome so concurrent undo calls cannot choose the same change. It re-checks parameter grants for the affected names. DAT and custom control recovery require a snapshot recorded by the running component and compare it with the audited state. An unverified mutation outcome suspends agents. Recovery does not write the project file. [v1.4 milestone](../milestones/v1.4-workflow-recovery.md)
- Connector inspection returns at most 24 ports per direction and 24 peer entries per port. The server filters peers and exposed Out operators through read policy. A partial row shows incomplete evidence without naming inaccessible operators. The result records topology only. The owner makes any external Base COMP output wire.
- An inert experiment is limited to a Base or Container COMP with no external connector wires and at most 24 supported TOP children. It refuses custom controls, dynamic menus, and non-default dynamic or reference parameters. Restoration checks lineage, live identities, matching operator shape and the checkpoint's recorded settings and wiring. It requires read access on both trees, delete on the replaced operators, and update on each changing parameter and input. Another runtime, a replaced origin, or a changed checkpoint requires owner inspection. This checks the directly inspected topology. Owner-authored references elsewhere in the project remain subject to the general experiment limitations. [Existing experiment limitations](../research/2026-11-23-phase-3-experiments.md)
- Checkpoint promotion records every inverse mutation effect, including named parameters, in its durable outcome. Undo reauthorizes that full effect record against current grants and protected paths. The bounded audit display list cannot substitute for it. An older or incomplete effect record causes recovery to be refused.
- Catalog guidance shares the existing scoped read path. It uses packaged text derived from disposable operator metadata. It reads runtime type availability without creating operators, evaluating parameters or inspecting their output. The v1.5 Script lifecycle prototype did not meet the staging gate. The existing Script creation denial remains in effect. [Proposed lifecycle decision](../decisions/0018-script-operator-lifecycle.md)
- The Issue #24 research tested exact inert transfer and recovery in disposable fixtures. TouchDesigner still executes activated callbacks with the receiving process's host authority. A Windows low-integrity helper demonstrated narrow filesystem-dependent enforcement. Native Script creation and activation remain outside the production allowance. [Threat model and evidence](../milestones/v1.7-script-access.md#issue-24-boundary-and-recovery-follow-up)
- Issue #26's separately approved offline route accepts only bounded data. It uses the established project id, paired owner session and CSRF, current agent/token identity, temporary draft-specific grants and current ordinary read policy. The main-thread step reads only protected path metadata. No candidate source reaches the adapter or Code Mode. Private SQLite records carry keyed integrity checks. Project and per-author caps bound retained drafts. Only the owner can discard an exact revision, with explicit confirmation. Durable audit records exclude source, diff, title and hashes. Owner grant and revoke records retain the affected agent, scope, permission names and expiry. If an outcome write fails after a private write, the engine pauses and withholds success. This is local integrity against accidental changes and does not protect against an independently compromised host. [Offline decision](../decisions/0020-offline-script-drafts.md)
- Network diagrams snapshot an exact COMP ID, filter authorized direct children, and include a wire only when both endpoints are shown. The fit, node identifiers and image use only that filtered snapshot. They omit hidden counts, nested contents, comments, DAT text and viewer pixels. The tool reads native positions and connectors on the main thread. A separate handler renders plain data, and current token, suspension and policy checks apply before delivery. No image or diagram rows are saved to audit. Snapshot work, image size and per-component frequency are bounded. [ADR-0019](../decisions/0019-network-visual-inspection.md)
- Compact network inspection on the v1.8 candidate checks the exact root and each visited operator. It traverses readable components, emits wires only between shown operators and omits hidden counts and raw diagnostics. Parsed literal references require read access on both operators. Code-bearing, password and operator/file-style parameters are excluded. Missing and denied targets both yield an unresolved reference. Current token, suspension and policy checks apply again before delivery, after the durable audit outcome. Row and wire limits count readable evidence only. The main-thread deadline can stop oversized native enumeration with a generic refusal and can still reflect host load and work on unreadable candidates. [Proposed ADR](../decisions/0021-compact-network-inspection.md)
- Compact exact-path inspection authorizes the requested ancestor chain before native lookup and skips sibling enumeration. Denied and authorized missing paths can take different amounts of time. Broad discovery and native wire inspection can expose coarse effects of hidden graph complexity through timing or work-limit refusals. Native main-thread calls can overrun the cooperative deadline. Both inspection forms use current policy, the same audit path and the same delivery rechecks. The residual requires owner acceptance for v1.8.0. [Proposed ADR](../decisions/0021-compact-network-inspection.md#residual-timing-and-availability-side-channel)
- The grant parameter denylist applies to every named custom definition and every control on a page when a call sorts it. Appending controls in their natural order checks the named definitions. The per-control checks count as one bounded page update in the operation limit. Approval binds those targets. Undo rechecks the recorded control names against current update grants and refuses a custom edit with a missing authorization record.

## 5. Code execution honesty

In-process Python cannot be sandboxed, and RestrictedPython's own docs disclaim being a sandbox.
- `tdflow_execute_python` (raw) is **disabled by default**. When enabled it needs `execute` on `/**`, always requires approval, and is labelled **"bypasses scopes"**.
- Code Mode ([ADR-0004](../decisions/0004-code-mode-strategy.md)) passes scripts only a `tdflow` facade whose calls are policy-checked. The facade returns **plain data** (dicts, strings, numbers), never TD objects, because Python introspection (`__class__`, `__globals__`, frames) can escape any wrapper. Even so, the restricted namespace is a **mistake-reducer, not a security boundary**, and Code Mode always requires approval of the exact script text. It is off until the user turns it on (dashboard setting). The engine enforces the scope, the tool allowlist, the budgets and the kill switch per facade call; static refusals (no `import`, `try`, `with`, classes, `_` attributes, attribute assignment) are guard rails ([review 15](../research/2026-11-23-phase-3-code-mode.md#independent-review-15)).

## 6. Audit

- **Two events per executed call:** an `intent` event written *before* execution and an `outcome` event after. If the audit store is unavailable, mutating calls fail closed with `AUDIT_UNAVAILABLE`.
- Fields: `{seq, ts, kind, agent_id, token_id, session_id, client_info, method, tool, args_hmac, args_redacted, targets, decision, rule_id, policy_version, approval_id, outcome, error_code, duration_ms, undo_label, prev_hash, hash}`.
- `args_hmac` is an HMAC with a per-installation key, so low-entropy secrets cannot be brute-forced from a plain digest. `args_redacted` removes `Authorization` headers and token-shaped strings.
- `hash = sha256(prev_hash || jcs(event))`. A local attacker with file access could recompute an unkeyed chain, so periodic **checkpoints** (`seq`, `hash`, HMAC with a key kept outside the DB) are exported and verified on report generation. The resulting claim is **tamper-evident relative to the last exported checkpoint**.
- Unauthenticated failures are rate-limited and **aggregated** (one `auth_fail_burst` event per window), so floods cannot force a SQLite write per request on TD's main thread.
- **Audit worker (ADR-0006 revision, implemented):** one bounded worker thread owns every SQLite connection and runs storage operations in FIFO order (event order = submission order; `BEGIN IMMEDIATE` re-reads the chain head, so even two writers cannot fork it). `synchronous=FULL` + WAL is kept.
  - **TD's main thread never waits for disk I/O.** It only *submits* audit work and polls the result in `OnFrame`; the worker refuses blocking calls from the main thread (`RuntimeError`).
  - MCP and admin requests are orchestrated on HTTP handler threads, which never touch TD: each TD step runs on the main thread through `RouteContext.call_main` (plan → **durable intent** → execute → **durable outcome**). The execute step re-resolves the targets (`PRECONDITION_CHANGED` if anything changed) and re-checks the kill switch and the token right before running.
  - Fail closed: queue overflow (`QUEUE_FULL`), timeouts, worker failure or lost durability make the call fail with nothing executed (intent stage) or `OUTCOME_UNRECORDED`/`OUTCOME_UNKNOWN` (outcome stage), which **suspends all agents**. A failed store is never restarted automatically; agents resume only with MCP off and a freshly opened, re-verified store.
  - Administrative actions that need a record (pairing approval, Enable MCP, resuming agents, token and Skills writes) take effect only after the worker confirmed the record durably.
- **Where audit data lives (owner decision 2026-11-22):** the audit database (events, sessions, DAT snapshots) defaults to a git-ignored `.tdflowmcp/<toe name>/` folder next to the saved `.toe`. **Credentials (agents, token digests, grants), the audit HMAC key and the keyed checkpoints stay in private per-user storage** (`%LOCALAPPDATA%\TDflowMCP\projects\<project id>\`), so a copied project folder carries no secret and checkpoints anchor outside the project. Each audit database is bound to its project id; a database belonging to another project is refused, never adopted. Unsaved, read-only, network and cloud-synced locations fall back to private storage. Moves, renames, copies and location changes need an explicit user choice (Confirm Move: verified copy or verified adoption; Claim As New Project: new identity) ([ADR-0006](../decisions/0006-audit-store.md)).

## 7. Resource limits (cooperative)

TD's main thread cannot pre-empt running Python. Limits are therefore:
- **Hard pre-checks:** body size (413), response size cap, pagination, and `max_ops_per_call` checked before execution.
- **Cooperative checks:** op counters and elapsed-time checks inside facade/tool loops abort between steps.
- **Post-hoc reporting:** durations above the budget are flagged in audit and the dashboard.
- **Residual risk:** a tight loop in raw exec freezes TD until it ends. A Code Mode script runs on a handler thread, and a line tracer stops pure-Python loops; a single long builtin call (a huge allocation or `sum(range(...))`) holds the interpreter until it returns. This is why `execute` is approval-gated.

## 8. Network posture

- Bind `127.0.0.1` only, enforced by the bind-verification gate (§8.2), whichever transport is used ([ADR-0002](../decisions/0002-in-process-mcp-host.md)). Remote/LAN access is out of scope for Phases 1–3 and needs an ADR (TLS plus mTLS).

### 8.1 Per-route gate table

**Before any route rule:**
- The path (query stripped) must be canonical. `..`, `.`, `//`, `\`, percent-encoding, non-ASCII and control characters → `400 BAD_PATH`, and the router uses only the gate's validated path.
- Conflicting duplicates of security-relevant headers (Host, Origin, Content-Length, Transfer-Encoding, Sec-Fetch-Site, Content-Type, X-TDflow-*) → `400`.
- `Content-Length` must be digits only and ≤ the cap.
- A body sent with `Transfer-Encoding` is accepted only when the actual received length is checked against the cap (and against `Content-Length`, if present).
- Implemented as pure logic in `src/tdflowmcp/gate.py`. It is integrated with the embedded transport (`src/tdflowmcp/net/pyhttp.py`) and was exercised inside TouchDesigner 2025.32440 in spike S6; the component shell does not exist yet.

| Route | Method | Requirements (all must hold) |
|---|---|---|
| `/mcp` | POST | Valid Host. Origin absent (non-browser) **or** equal to the canonical origin, else 403 (spec MUST). Valid agent bearer token. State Enabled |
| `/setup/`, `/ui/` static files | GET | Valid Host. If `Sec-Fetch-Site` is present it must be `same-origin` or `none` |
| `/setup/api/*`, `/api/*` reads | GET | Valid Host + custom header `X-TDflow-Request: 1`. A cross-origin page cannot send a custom header without a CORS preflight, and the server never grants preflights. `Sec-Fetch-Site`, if present, must be `same-origin`. Reads after pairing also need the admin session |
| `/setup/api/*`, `/api/*` mutations | POST/PUT/DELETE | Valid Host + **Origin exactly canonical (a missing Origin is rejected)** + admin session cookie + `X-TDflow-CSRF` matching the session |
| `/api/events` | WebSocket upgrade | As for mutations: exact Origin + session + CSRF token in the first message |
| `/.well-known/tdflowmcp-proof/<nonce>` | GET | Valid Host; the nonce is exactly 32 lowercase hex characters. Unauthenticated by design; answered on a handler thread without TouchDesigner. Returns `{proof}` = HMAC-SHA256(project connect key, `tdflowmcp-proof|<project id>|<port>|<nonce>`), or 404 when no connection exists in this project. It grants nothing: it proves to Claude Code's headersHelper that this listener holds the key ([ADR-0015](../decisions/0015-consent-based-client-connection.md)) |
| `/.well-known/tdflowmcp` | GET | Valid Host. Returns `{product, version}`, plus `connect` (the project's random, non-secret storage id `prj_…`, also its Projectid parameter) once the store is open. The headers helper (Claude Code, Codex) uses it to pick that project's credential ([ADR-0015](../decisions/0015-consent-based-client-connection.md), [ADR-0017](../decisions/0017-codex-one-click-connection.md)). No project name, path or instance details |

Responses never include `Access-Control-Allow-*` headers.

### 8.2 Bind-verification gate

0. **Pre-activation checks.** Failing any of them means the listener is **never activated** (no socket is opened):
   - **Capability, per transport.**
     - **Embedded Python transport (default):** the bind host must be the literal `127.0.0.1` (anything else → `BIND_REFUSED` / `net.capability` fail), and exclusive binding must be available on Windows (`SO_EXCLUSIVEADDRUSE`, never `SO_REUSEADDR`).
     - **Web Server DAT (optional):** the DAT must have a `localaddress` parameter; otherwise → `Error: BIND_UNSUPPORTED`. Spike S5 found that TouchDesigner 2025.32440 lacks `localaddress` and binds `0.0.0.0`, so the DAT transport is never used there.
   - **Port ownership:** if the OS table shows **any other process** listening on `<port>`, on any address, the state becomes `Error: PORT_IN_USE`. On Windows another process may hold `0.0.0.0:<port>` next to our `127.0.0.1:<port>`, and then loopback traffic meant for it would reach us (S6), so the port is never shared.
1. **Activate the listener in `VERIFYING` state.** A listening socket cannot be verified before it exists. While `VERIFYING`, the request gate answers **every** request with `503 TDFLOW_VERIFYING` and runs no application route: no `/setup`, `/mcp`, `/api`, `/ui` or `/.well-known` content, and no TD access. The operating system may accept TCP connections during this window, but no application data is ever served.
2. **Verify the effective binding** before any application request is served. Positive evidence of a loopback-only binding is required:
   - the OS listening-socket table, **filtered to this TouchDesigner process's PID**, shows only loopback addresses (`127.0.0.1` and/or `::1`) for `<port>`, with no `0.0.0.0`, `::`, IPv4-mapped or other non-loopback entry;
   - a connection attempt to each non-loopback local address on `<port>` does not connect. A probe can only **fail** the verdict: S5 showed a timeout happens even when nothing listens, so it proves nothing.
   - the embedded transport also reports its own `getsockname()`; a non-loopback value fails, but it never replaces the OS table.
   - no other process listens on `<port>` (`port_shared` → `PORT_IN_USE`).
   - Evidence collection is pure socket/OS-table work and runs off the main thread; only the verdict is applied on the main thread.
   - With the Web Server DAT, the socket appears asynchronously, seconds after `active` is set (S5: 2.0–3.5 s). The embedded transport's socket exists as soon as `start()` returns (S6: verdict after ≈ 0.31–0.35 s). Only a bounded "no listener for this process yet" result is re-checked on later frames; any other ambiguity closes the listener.
3. **Pass → `SERVING`.** The gate starts routing application requests.
4. **Fail or ambiguous → close immediately, then confirm.** If the socket table is unavailable, any non-loopback entry appears, or a non-loopback connection succeeds, the listener is closed **in the same frame or callback that reached the verdict** (Web Server DAT: `active=False`; embedded transport: `begin_close()`, which never blocks the frame). The OS releases the socket asynchronously (S5: `onServerStop` about 0.06 s later; S6: closure confirmed in 33–66 ms), so closure is confirmed on a later frame from the socket table. The state becomes `CLOSING` and then, once a later-frame table check shows no socket, `CLOSED` with `Error: BIND_UNVERIFIED`. If the socket does not disappear within a bounded number of checks, the state becomes `STUCK` (`CLOSE_UNCONFIRMED`) and the user is told to restart TouchDesigner. Guidance goes only to parameters and an internal DAT. There is no retry with weaker checks, no override, and **no automatic restart**: `CLOSED` is terminal until the user explicitly acts again (`Opensetup`), and `CLOSING`/`STUCK` never re-open.
5. Re-verify after any change to `Port`, the bind host, `localaddress` or `active`, and after the listener restarts. A user-requested stop (`CLOSING` → `CLOSED` with no error code) and a transport failure (server thread died → `TRANSPORT_FAILED`) both go through the same close confirmation.

The evidence sources were established on 2025.32440 by spikes S5 (Web Server DAT) and S6 (embedded transport) ([S5 findings](../research/2026-11-22-s5-compatibility-2025.32440.md), [S6 findings](../research/2026-11-22-s6-embedded-python-transport.md)). S1 covers the Web Server DAT on newer builds and is optional.

### 8.3 Embedded transport: threading and limits

Implemented in `src/tdflowmcp/net/pyhttp.py` (stdlib only: `http.server`, `socketserver`, `threading`, `queue`, `socket`). Measured in [S6](../research/2026-11-22-s6-embedded-python-transport.md).

- **Thread split:**
  - Handler threads parse HTTP, run the request gate (§8.1), authenticate, check the kill switch, and serve the static Setup snapshot.
  - Everything else becomes a job on a **bounded queue**. Only `pump()` dispatches jobs, and it runs on TouchDesigner's main thread: it raises on any other thread and handles at most 8 jobs within ≈ 4 ms per frame.
  - Handler threads never import `td` (boundary test) and never call the dispatcher. The static snapshot is bytes copied from DATs on the main thread.
  - **Route handlers (Phase 1C audit worker):** `/mcp` and the paired admin API run as handler-thread route handlers. They may wait for the audit worker but never touch TD; a TD step is a job with a function, run by `pump()` on the main thread (`call_main`). A step that was certainly not run raises "not run"; one dispatched without an answer in time is "may have run" and suspends agents. Status, tell-agent and pairing requests still go to the main-thread dispatcher.
- **Bounded everything:**
  - ≤ 16 concurrent connections: beyond that, an immediate `503 TDFLOW_BUSY` without a thread.
  - Listen backlog 64, queue 32 jobs: when full, `503 TDFLOW_BUSY`.
  - 5 s socket I/O timeout, and connections older than 30 s are cut.
  - One response per connection (`Connection: close`). No chunked request bodies.
- **Timeouts:**
  - A job not dispatched within the job timeout is **cancelled and never runs** (`504 TDFLOW_TIMEOUT`).
  - A job dispatched but not finished in time gets `504 TDFLOW_TIMEOUT_AFTER_DISPATCH`: its outcome is unknown to the client and must be read from audit by request id.
- **Responses:**
  - Every response carries `Cache-Control: no-store`, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, CORP `same-origin` and a CSP (`default-src 'self'; frame-ancestors 'none'`), so the Setup page must not use inline script.
  - Dispatcher headers named `Access-Control-*`, `Content-Length`, `Transfer-Encoding`, `Connection` or `Server`, or containing CR/LF, are dropped.
  - Errors never include exception text.
- **MCP off in Phase 1A:** components construct the transport with `mcp_enabled=False`. `/mcp` then returns `503 MCP_DISABLED` right after the gate, before authentication, body read or dispatch.
- **Orphan watchdog:** if the main thread stops pumping for 20 s (component deleted without `onDestroyTD`, or extension lost), the serve thread closes the listener by itself. A long main-thread stall therefore also closes it, which is fail-closed.
  - **Exception: a minimized window ([S9](../research/2026-11-23-s9-minimized-window.md)).** TouchDesigner 2025.32440 runs no frames while minimized, so nobody pumps although the owner is alive. The watchdog asks the operating system whether this process's own TouchDesigner window is minimized (`net/oswindow.py`, no `td`, Windows only). Only a definite "yes" for the registered, live instance keeps the listener; "no", unknown, an error, a shut-down or replaced instance or another platform closes it as before. After a restore the owner gets one more 20 s to pump. **While minimized, frame-driven checks are paused** (the periodic bind re-verification, the idle stop, the `Suspendagents` parameter): nothing that needs TouchDesigner can run, but requests that need no frames are still answered ([review 12](../research/2026-11-23-s9-minimized-window.md#independent-review-12)).
- **Not cooking:** once the owner has not pumped for 2 s, a request that needs the main thread is refused at once with `503 TDFLOW_NOT_COOKING` and is never queued, so it can never run later. MCP tool calls get a tool error with the reason, audited as a denial. `tools/list`, the identity tools and static assets need no frames. The `Suspendagents` parameter cannot be flipped while TouchDesigner is minimized; nothing can execute in that state either.
- **Kill switch:** `suspended` rejects every `/mcp` request with `503 SUSPENDED` before dispatch, while `/setup/` stays available. The transport itself can be stopped with `begin_close()`, which fails queued jobs with `TDFLOW_CLOSING`.
- **Audit:** the transport keeps a bounded in-memory record per request (id, method, validated path, route, status, code, duration, principal), with no headers, bodies or tokens. The main thread drains it into the audit store (Phase 1B).
- **Residual limits:**
  - Latency floor ≈ 1–2 frames inside TouchDesigner (S6: handler threads get the GIL about once per frame).
  - A main-thread stall delays all dispatches.
  - No WebSocket and no TLS.

- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (defence in depth against DNS rebinding; not itself a spec requirement).
- TDflowMCP core makes no **non-loopback** outbound network calls. The only outbound request is the loopback port-ownership probe (`GET http://127.0.0.1:<port>/.well-known/tdflowmcp`) during conflict detection. It has a timeout of at most 200 ms, runs off the main thread or in non-blocking steps, and its answer is **untrusted**: it is shown only as "responds like TDflowMCP vX" and never drives any automatic action.

## 9. Concurrency between agents (Phase 1 rule)

TD steps of all calls are serialized on TD's main thread (audit writes on the audit worker, in submission order). Concurrent agents can still interleave edits, so `tdflow_undo` only undoes an agent's action while it is on top of TD's undo stack, and otherwise returns `UNDO_CONFLICT`. Scope leases for multi-agent work are planned for Phase 4.

## 10. Setup Assistant and integration safety

- The Setup listener starts only on a user action (`Opensetup` or `Enablemcp`). It serves application requests only after the bind-verification gate (§8.2) passes, and it is closed immediately if verification fails or is ambiguous. Guidance is then shown only in parameters and an internal DAT.
- Phase 1A Setup is read-only: status, checks, troubleshooting text and "Tell Your Coding Agent". From 1B, actions (create or disable tokens, choose scope presets, enable MCP, Skills writes) require a paired admin session (§2.5).
- **No unauthorized changes:** TDflowMCP never installs packages or software, never changes TouchDesigner preferences or Python search paths, never edits firewall or OS settings, never writes client permission or approval settings, and never kills processes. File writes are limited to three cases, each user-confirmed from a preview and audited:
  - **Coding-agent connection (owner D22, [ADR-0015](../decisions/0015-consent-based-client-connection.md)):**
    - Only after the user approved the exact plan in a TouchDesigner dialog (or a second Connect Agent pulse within two minutes).
    - Only for clients marked automatic. Claude Code: one user-scope `mcpServers.tdflow` entry with a fixed `headersHelper`. Codex on Windows ([ADR-0017](../decisions/0017-codex-one-click-connection.md)): one `[mcp_servers.tdflow]` table with a fixed `http_headers_helper` in `~/.codex/config.toml`.
    - Other servers and settings are preserved; a backup goes to private storage first.
    - The Codex file is TOML and is edited as text, so that comments and layout survive. The edited text must parse and must equal the file as it was except for TDflowMCP's own table; otherwise nothing is written (`CONFIG_UNSUPPORTED`, `CONFIG_INVALID`).
    - A `tdflow` table counts as TDflowMCP's only if its URL and helper are the generated ones and it names no other credential source or transport, because Codex gives an explicit bearer token and OAuth precedence over the helper.
    - The write is atomic and read back; a changed plan is refused (`CONFIG_CHANGED`).
    - Undone by Disconnect, which removes only an entry that is still TDflowMCP's and restores one it replaced.
    - The credential goes only to the OS-protected store.

    Silent modification is never done, and other clients keep an instruction-only manual fallback.
  - **Skills files**, in assisted-write mode, with a hash manifest, only into the registry-listed skills directories ([agent integration §3](agent-integration.md#3-client-aware-installation-consent-first));
  - **project-memory record files** (2A increment 3), only after the user migrated project memory to a folder inside the project's own folder tree, and only `tdflow-project.json` and `tdflow/memory/` in that folder (§11).
- **Startup inspection (rc4, UX-09):**
  - On load, the automatic diagnostics only check for the existence of this project's private credential store and connection key files, and read a small non-secret `summary.json` (enabled-agent count, connected clients). All are named by the saved `Projectid`, validated with `\Z`, or by the single registry entry for this `.toe`.
  - The inspection opens no database, creates no file, starts no listener and changes no identity.
  - It only chooses honest text (`Turn on Enable MCP.`, `1 agent · not loaded`). Every security decision still happens when the store opens.
  - Review 20: an unsaved project carrying another saved project's identity is `COPIED`.
- **TouchDesigner's shared dialog (UX-08):** TDflowMCP does not modify it. It shows a compact plan pre-wrapped to its width, and the full plan is in the `connect_view` viewer. One TDflowMCP dialog is open at a time.
- **Processes:** the core never starts a process. The TD adapter starts only the fixed macOS `/usr/bin/security` Keychain commands, and only after the user's approval; secrets go to them on stdin. Coding-agent programs that discovery finds are never run.
- **Secrets:** custom parameters are saved into the `.toe`, so they never hold tokens. Pairing uses non-secret match codes (§2.5). Tokens are displayed once, in the Setup page only.
- **Token placeholders and shells:** generated commands keep `${TDFLOW_TOKEN}` literal by using **single quotes**, which are literal in both POSIX shells and PowerShell. A double-quoted form would let bash or zsh expand the real token into the client config, and in PowerShell it would expand an empty variable. Generated commands use user/local scope, never project scope (which writes a committable `.mcp.json`). Whether each client expands `${VAR}` in stored headers at runtime is **[verify in 1C]**. A 1C test asserts that no client config file written by following the instructions contains the token.
- **Generated text hygiene ("Tell Your Coding Agent", parameters):** only allowlisted fields are included (versions, state, check ids/results, fixed remediation text, endpoint URL). Environment-derived strings (operator names, probe answers, error messages) are excluded or truncated and fenced as data. Paths are shown as `.tdflowmcp\<name>` or `%LOCALAPPDATA%\TDflowMCP\projects\<id>\data`, never expanded with a username. Connection instructions always reference the token as the literal `${TDFLOW_TOKEN}` (single-quoted in bash, zsh and PowerShell commands). The resume section is **reference-only by default** (handoff id, verified-fact counts). Agent-authored summaries are returned only through `tdflow_handoff_resume`, fenced and labelled.
  - **Copy Agent Prompt (D24):** the same rule.
    - Inputs: the client display name and TouchDesigner build (plain-label pattern, else omitted), the version, a fixed-pattern error code and two memory counts.
    - It never states the agent's role or scopes; it tells the agent to read them from `tdflow_whoami`, which the server enforces.
    - It is "ready" only when MCP is on and either the selected client is set up here with an unrevoked credential and a matching port, or that client's own agent made a request. A manual client also needs an enabled agent. Otherwise it says that no connection exists yet.
    - It goes to the clipboard only on the user's pulse and is stored in no parameter or DAT.
- Client detection is read-only: names on PATH (`shutil.which`) and extension or configuration folder names. It never reports paths and never runs anything. A client's configuration file is read only to compute a connection plan, after the user pulsed Connect Agent.

## 11. Project memory and continuity safety

- Exactly one authoritative memory home ([ADR-0013](../decisions/0013-project-memory-model.md)). Writes carry `based_on_rev`, and stale writes return `MEMORY_CONFLICT`. A `Projectid` mismatch blocks linking.
- **Access, as implemented in 2A** (**conditionally accepted by the owner, D18**, [ADR-0013](../decisions/0013-project-memory-model.md#conditional-decision-on-memory-permissions-owner-d18-2026-11-22); the earlier proposal of separate `memory:<record-type>` pseudo-scopes is deferred): memory access derives from operator scopes and role. **Before agents may write authoritative project ICM, decisions or Git-backed docs, dedicated, owner-issued memory permissions must exist and be enforced.** Agent-authored task status is never an owner-approved decision.
  - A task is visible only to agents that can read all of its scopes. Resuming a hidden task answers like a missing one (M6), and writing to it is refused (M4). Paths inside records (changes, claim paths and claim evidence) are filtered again to what the caller can read, and the fingerprint map is never returned.
  - The token and the scope grants are re-checked on the main thread right before a record is written.
  - Writing needs a role with `update` (M1) and read access to the scopes.
  - Record DATs are never operator targets: raw tools stay H4-denied on `tdflowProject`, and only the memory path in the engine writes there. Each write follows the intent → main-thread write (re-checking the kill switch, token and unchanged record set) → outcome order.
  - There is no delete tool. Separate memory grants are deferred to token management (2B).
- **Fork, rewind and outside edits (implemented):** the audit log is the ledger.
  - Every record is announced in a durable intent (record id and text hash) before it is written.
  - The user's **Claim Project Memory** is an audited event listing the accepted hashes.
  - Records that are edited outside TDflowMCP, missing (an older save, or never saved), foreign (copied from another project or component) or invalid make memory read-only for agents (`MEMORY_READ_ONLY`, M5) until the user claims. More than one `tdflowProject` under the root is refused.
  - This replaces the proposed `Datadir` authority registry ([continuity §7](continuity-and-memory.md#7-phase-2a-increment-1-what-is-implemented-2026-11-22)).
  - **Project identity:** the audit log binds the project to one memory `Projectid` (latest create or claim). Another `Projectid` means read-only until the user claims it.
- **Repository mode (implemented in 2A increment 3, [continuity §8](continuity-and-memory.md#8-phase-2a-increment-3-repository-mode-2026-11-23)):** the records are files in a folder the user linked.
  - **Dedicated permission (owner condition D18):** an agent writes there only with the capability `memory:repo:write`. Only the user issues it, on the paired Setup page, to a role that can update. It is never implied by a role or a scope, and it is re-read right before the file is created (rule `M8`). Reading keeps the role-and-scope model.
  - **Link check:** `tdflow-project.json` must name this memory's `Projectid` as the authority before any read, write, claim or migration (`MEMORY_LINK_MISMATCH`). A claim cannot override a mismatch.
  - **Path safety:** the link is always relative to the `.toe` folder (that folder, below it, or up to three levels straight above it). Absolute paths, sibling folders, `.git`, `.tdflowmcp`, and a symbolic link or junction on **any** component (checked on the real path, before and after a write) are refused. Only `tdflow-project.json` and `tdflow/memory/` are written; records are never overwritten or deleted.
  - **Untrusted folder contents ([review 11](../research/2026-11-23-phase-2a-increment-3-review.md)):** a file the audit log did not announce is `foreign`, an edited one `changed_outside`, an unreadable, oversized, linked, malformed or not encodable one `invalid` (reading never raises). None is used until the user's Claim Project Memory, and invalid ones are never claimable. Server-written fields are validated strictly on read, so crafted text cannot reach server-labelled parts of an answer; resume compares nothing against a record that is not `accepted`.
  - **One revision per task:** the record-set check and the file creation run under one lock, with the link re-checked inside it.
  - **Files are readable outside TDflowMCP:** anything with file access to the folder can read every task record, whatever its TDflowMCP scopes. The migration preview says so. Per-parameter digests in records are keyed with the installation key.
  - **Threads:** files are read and written on the HTTP handler thread; the main thread only re-checks the kill switch, token, grants and the component's link.
  - **Migration** is a user action on the paired Setup page: a preview with a digest, then exactly that preview; refused while the ledger is not clean; audited before and after.
- **Live-state claims** (`exists`, `absent`, `value`) are checked only on operators the caller can read; others are `unverified` with no detail. Only constant, non-read-only parameter values are compared (nothing is evaluated).
- Memory and handoffs never contain tokens, raw audit arguments or user-profile paths. They reference audit by sequence number and hash. **Implemented:** token-shaped strings in agent text are replaced with `<redacted>` before a record is stored.
- Agent-authored content is returned labelled `author: agent:<id>` and is never presented as an instruction from the user or the server. Only server-derived facts are labelled `verified`.
