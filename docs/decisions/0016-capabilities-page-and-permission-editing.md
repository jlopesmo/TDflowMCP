# ADR-0016 — Capabilities page, permission editing and agent self-report

**Status:** Conditionally Accepted (owner D25, 2026-11-25), subject to the validation gates below. It amends [ADR-0005](0005-dashboard.md) (a new dashboard page) and [ADR-0009](0009-permission-model.md) (an agent's role can now change after creation). Every rule of ADR-0009 and the [security model](../architecture/security-model.md) still holds.

## Context
The owner directed (D25) that the owner can:
- see what TDflowMCP supports and what each agent can do;
- adjust supported permissions in the dashboard;
- ask an agent what it can do and be pointed to that page.

Before this, permissions were visible only as raw grants (scope + actions) on the Agents tab. The only path to more or fewer actions was creating a new token with another role. Agents could read their role and scopes (`tdflow_whoami`), but had no answer to "what can you do?" that separated their own permissions from TDflowMCP's features.

## Decision
1. **One catalog, derived from the policy.** `src/tdflowmcp/capabilities.py` lists capabilities in groups. Each entry names:
   - the MCP tools that provide it;
   - the policy actions it needs;
   - whether each use needs approval;
   - any dedicated permission or setting it needs.

   From that, and the *stored* role, grants and dedicated permissions of an agent, the module derives five statuses:
   - **Available**;
   - **Approval Required**;
   - **Denied** (role, scopes, a setting or a disabled agent: something the owner can change);
   - **Not Implemented**;
   - **Protected**: hard denies H1–H7, the parameter deny list and the creation allowlist, which no permission changes.

   Scopes are computed per action across all grants, as `Policy._grant_decision` does. The matrix is a view: the engine still decides every call per path.
2. **One backend for changes.**
   - `POST /api/permissions/preview` returns the effective result of a change without storing anything: the per-capability differences and the scopes after the change. A change is a role, added or removed grants, and dedicated permissions.
   - `POST /api/permissions/apply` applies exactly the previewed change. It must carry the preview's digest, which binds the stored state and the change.
   - A change that gives more access needs `confirm_escalation: true`. More access is judged three ways:
     - from the effective matrix now;
     - from the same matrix as if the agent were enabled, Code Mode on and every tool active;
     - from the raw change: higher role, added allow grant, added actions, removed deny, added dedicated permission.
   - An audit record is written durably first, and refusals are audited too. The change and the cancellation of the agent's open approvals happen in one SQLite transaction. If the reload fails after the commit, the agent is blocked in memory (fail closed).
   - The older `/api/grants/*` endpoints need the same explicit confirmation when called from the web (TouchDesigner's own pulses are the confirmation there).
3. **Role changes keep scopes.**
   - Allow grants that held the old role's full action set get the new role's set.
   - Narrowed grants keep only actions the new role has; grants left with no action are removed.
   - Deny grants never change.
   - A dedicated permission the new role cannot hold is dropped.
   - A trailing role label in the agent's name ("Claude Code (Builder)") is updated.
4. **Agent self-report.**
   - A read-only tool, `tdflow_capabilities`, returns the caller's own effective capabilities, grouped by status. A statement keeps them apart from what TDflowMCP supports in general.
   - It also returns the dashboard address and how to open it. The user pulses **Open Dashboard** in TouchDesigner, so the Browser sign-in dialog appears. Agents never send the user to approve a sign-in TouchDesigner did not open.
   - `tdflow_whoami` names the tool and the address.
   - The address is the MCP endpoint's own host and port and carries no credential: the page needs a browser pairing approved in TouchDesigner.
5. **No unsupported toggles.** Protected and not-implemented capabilities have no editable field. A change naming anything other than role, grants or dedicated permissions is refused (`UNSUPPORTED_CHANGE`). Code Mode stays a project setting (Settings tab).

## Alternatives
- **Edit the matrix cells directly** (tick "Available"). Rejected: a cell is the result of a role, scopes and grants; editing results would need a second permission model.
- **A per-capability permission list.** Rejected for 1.0 (feature freeze): it would replace ADR-0009's action-on-scope model. The catalog maps capabilities onto that model instead.
- **Put the agent's capabilities in `tdflow_whoami` only.** Rejected: whoami is the start-of-session call and should stay short; a dedicated tool keeps "what can you do?" accurate and separate.

## Consequences
- The owner sees permissions as capabilities and changes them with a preview. Escalation needs an explicit confirmation, and every change is audited.
- An agent's role can now change without a new token. Its policy version changes, and its open approvals are cancelled.
- The catalog must stay in step with the tool surface. A unit test fails if an active tool is missing from it, and another checks every status against `Policy.evaluate`.

## Validation gates
- G1: every matrix status matches `Policy.evaluate` for representative targets (unit, `tests/unit/test_capabilities.py`), and every active tool is in the catalog.
- G2: preview stores nothing; apply refuses a stale digest, an unconfirmed escalation, unsupported fields and scopes outside the root; open approvals are cancelled; every change is audited (TouchDesigner 1B suite, `caps.*`).
- G3: a change made in the dashboard shows on TouchDesigner's Agents page, and a change made there shows in the dashboard (1B suite).
- G4: a real Claude Code session asked "what can you do?" calls `tdflow_capabilities`, reports its role and limits, points to the page and changes nothing (native suite).
- G5: the page renders without clipped statuses or horizontal scrolling at common desktop sizes, with keyboard access (`tests/web/run_web_screens.py`).
- G6: independent security and usability reviews; their findings are resolved ([research record](../research/2026-11-25-capabilities-and-final-polish.md)).

Observed results: [validation index](../icm/validation-index.md).
