# ADR-0005 — Governance dashboard

**Status:** Conditionally Accepted (owner, 2026-11-22). Revision: the scope is narrowed to the monitoring and governance dashboard (Phase 2B). The minimal setup experience is a separate surface ([ADR-0010](0010-setup-assistant-and-component-parameters.md)) delivered in Phase 1A/1B.

## Decision
- The dashboard is served by the component's loopback transport ([ADR-0002](0002-in-process-mcp-host.md)) at `http://127.0.0.1:<port>/ui/`, the same origin as `/setup/` and `/api`. It shares the gate, admin session and static-file serving with the Setup Assistant, and nothing else.
- Static HTML, vanilla ES modules and CSS in `src/dashboard/`. No build step and no CDN, so it works offline on locked-down PCs.
- Scope: live monitoring (WebSocket `/api/events`), sessions, audit search, report export, the approval queue, policy and token management, the kill switch, and continuity views (checkpoints and handoffs).
- **Admin authentication:** the pairing flow from ADR-0010. Every request needs an exact canonical Origin plus a CSRF token header ([security model §2.5](../architecture/security-model.md#25-dashboard-and-setup-admin-sessions)). Agent bearer tokens never grant dashboard access.
- The dashboard is a **view and decision surface only**. All enforcement is server-side.
- **Owner direction D14 (2026-11-22):** the dashboard is where users configure data locations and **optional** session/diagnostic logging. The **mandatory security audit** (intent and outcome for every MCP call, fail closed) cannot be disabled while MCP is on.

## Alternatives
- A React/Vite SPA: needs npm and a build step.
- A TD-native panel UI: harder to review.
- A separate web server process.
- Merging the dashboard into setup: rejected by the owner.

## Validation gates
Phase 2B (results for increment 1 in the [record](../research/2026-11-23-phase-2b-increment-1.md) and the [validation index](../icm/validation-index.md)):
- Events appear within 1 s. **Implemented** as a 1 s poll of the audit search (no WebSocket yet); shown in TD (`dashboard.live_tail`).
- An approval executes only on the identical re-call, with targets and policy re-checked. **Passed** in unit tests and in TD (`approval.*`).
- Report export verifies against the checkpoint. **Passed** in unit tests (covered, not covered after a later event, tampering detected); a report is built in TD.
- A request with a wrong Origin, a missing CSRF token or another-port Origin is rejected. **Passed** (gate unit tests; `approval.decide_needs_csrf` in TD).

## Amendment (owner D25, 2026-11-25): Capabilities page
A **Capabilities** tab shows a capability matrix (TDflowMCP support, each role, the selected agent) and edits roles, scopes and dedicated permissions through preview, explicit confirmation of more access and the one permission backend ([ADR-0016](0016-capabilities-page-and-permission-editing.md)). The page stays a view and decision surface: the engine enforces every rule. Also fixed: the `hidden` attribute was overridden by author `display` rules, so the "not running frames" warning and both Suspend and Resume always showed.
