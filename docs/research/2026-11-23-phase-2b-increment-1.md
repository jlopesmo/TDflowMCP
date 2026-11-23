# Phase 2B increment 1: approvals and the governance dashboard (2026-11-23)

**Owner direction D19 (2026-11-23):** after Phase 2A, build the Phase 2B governance dashboard ([ADR-0005](../decisions/0005-dashboard.md)). This increment delivers the approval flow and the first dashboard. The design rules come from [security model §4](../architecture/security-model.md#4-approvals) and [system design §4](../architecture/system-design.md#4-approvals-without-blocking-touchdesigner).

## Delivered

### Approvals
- **What can be approved in this increment.** Rule A1 (the call makes TouchDesigner run code) for `tdflow_set_parameters`: expressions, and parameters that name operators or files. Rule A2 (deleting more than 50 operators) for `tdflow_delete_operator`. Every other A1 case (text written into a DAT TouchDesigner may run, for example) stays denied with `APPROVAL_UNAVAILABLE`, and executable operator types stay outside the creation allowlist. Hard denies, role, grants, allowlists and limits are evaluated first; a call they deny never becomes a request.
- **Who can ask.** A1 needs `execute`, which only the new **operator** role has (builder plus approval-gated execute). Operator tokens are created on the dashboard or the Setup page. A2 applies to any role that can delete.
- **Nothing waits.** The call is refused at once with `APPROVAL_REQUIRED` and a pending request is stored: the agent, the tool, the **complete arguments** and their SHA-256, the resolved targets, the policy version, the reasons, and a 10-minute expiry. TouchDesigner's main thread never waits for a human.
- **The user decides on the dashboard**, which shows the complete raw arguments and the operators the server resolved. Approval rows are kept in the private credentials database (per-user storage), not in the project folder; the decision is audited before it takes effect and carries a MAC made with the installation key.
- **Execution only on the identical re-call.** The same agent repeats the same tool with the same arguments (same digest). Coding agents cannot attach custom `_meta`, so the approval is found by digest; an `approval_id` in `_meta.tdflow`, if sent, must match. At redemption the server re-resolves the targets (they must be identical), compares the policy version, and marks the approval used in the same transaction, so two identical re-calls can never both run. At execution, policy is evaluated again with only the two approval rules lifted.
- **Single use, expiry, limits.** A redeemed, denied, expired or cancelled request never runs. An agent can have at most 5 pending requests. Arguments containing a TDflowMCP token are refused before anything is stored.
- **`tdflow_approval_status`** (new, read-only) shows the state of the caller's own request. It never executes anything.
- **Expressions can now be set**, through `tdflow_set_parameters` `expressions`, only after approval. Undo restores the previous mode and value and is verified. Undoing an approved change needs `update`, not a new approval: undo only restores the verified state from right before the agent's own change.
- Without an approval surface (`policy.approvals` off) the behaviour of Phases 1–2A is unchanged (`APPROVAL_UNAVAILABLE`).

### Governance dashboard (`/ui/`)
- Static page (vanilla HTML, CSS and JS in `src/dashboard/`, embedded in the `.tox`, no build step, no CDN), served on the same loopback origin as `/setup/`. Server data is rendered with `textContent` only.
- **Pairing** is the same reverse flow as the Setup page (approved inside TouchDesigner). Every `/api` request needs the admin session and the CSRF token; mutations also need the exact Origin.
- **Overview:** state, MCP endpoint, project root, agents, approvals waiting, audit-log verification, data location, and whether TouchDesigner is running frames.
- **Approvals:** the queue with complete arguments, resolved targets and reasons; Approve or Deny with an optional note.
- **Activity:** audit search (agent, tool, result, text) with a live tail polled every second (ADR-0005 gate: events within 1 s). Search fields are validated and passed to SQLite as parameters; searches are POSTs with JSON bodies.
- **Sessions**, **agents and tokens** (create, including operator and the repository-memory permission; revoke; disable), **project memory** (status).
- **Reports:** a session or event range as Markdown or JSON, with the log's verification: the report states whether every event in it lies inside the part of the audit log whose hash chain and keyed checkpoints verify (ADR-0005 gate). Each export is audited with the report's digest.
- **Kill switch:** suspends all agents from the dashboard **without needing frames** (it works while TouchDesigner is minimized; [S9](2026-11-23-s9-minimized-window.md)). Resuming is only requested from the dashboard and happens on the main thread once recorded, like the parameter.
- **Settings (owner D14):** the security audit is shown as always on and cannot be changed; an **optional diagnostic log** (`diagnostic.jsonl` next to the audit database; component state changes only, no arguments, no tokens) can be turned on; a change of data location can be requested, and is completed with Confirm Move as before.

Skill **1.3** explains the approval flow to agents.

## Independent review 13
[Review 13](2026-11-23-phase-2b-review.md): no High finding; two Medium findings shown by proof-of-concept code (the approval rows were in the project-local database) and the rest Low or notes. All addressed: approvals now live in private storage, bind the target facts, refuse invisible characters, and the kill switch has no races and works with the audit store down.

## Evidence
See the [validation index](../icm/validation-index.md): unit tests `test_approvals.py`, `test_approval_flow.py`, `test_dashboard.py`; the 1B in-TD checks `approval.*` and `dashboard.*` (a real expression approved on the dashboard, set by the identical re-call, verified in TouchDesigner, undone).

## Not done yet (2B)
- Approval of the other code-running effects (text in code DATs, executable operator types): each needs its own execution path, tests and review before it is opened.
- Per-token policy editing (custom scopes per grant, `allowed_op_types`, `param_denylist`) on the dashboard.
- A WebSocket event stream (`/api/events`): the page polls every second instead.
- Dashboard UI in TouchDesigner itself, notifications, `tdflow_describe_network` and the other 2B tool candidates.
