# ADR-0005 — Governance dashboard

**Status:** Conditionally Accepted (owner, 2026-11-22). Revision: the scope is narrowed to the monitoring and governance dashboard (Phase 2B). The minimal setup experience is a separate surface ([ADR-0010](0010-setup-assistant-and-component-parameters.md)) delivered in Phase 1A/1B.

## Decision
- The dashboard is served by the component's loopback transport ([ADR-0002](0002-in-process-mcp-host.md)) at `http://127.0.0.1:<port>/ui/`, the same origin as `/setup/` and `/api`. It shares the gate, admin session and static-file serving with the Setup Assistant, and nothing else.
- Static HTML, vanilla ES modules and CSS in `src/dashboard/`. No build step and no CDN, so it works offline on locked-down PCs.
- Scope: live monitoring (WebSocket `/api/events`), sessions, audit search, report export, the approval queue, policy and token management, the kill switch, and continuity views (checkpoints and handoffs).
- **Admin authentication:** the pairing flow from ADR-0010. Every request needs an exact canonical Origin plus a CSRF token header ([security model §2.5](../architecture/security-model.md#25-dashboard-and-setup-admin-sessions)). Agent bearer tokens never grant dashboard access.
- The dashboard is a **view and decision surface only**. All enforcement is server-side.

## Alternatives
- A React/Vite SPA: needs npm and a build step.
- A TD-native panel UI: harder to review.
- A separate web server process.
- Merging the dashboard into setup: rejected by the owner.

## Validation gates
Phase 2B:
- Events appear within 1 s.
- An approval executes only on the identical re-call, with targets and policy re-checked.
- Report export verifies against the checkpoint.
- A request with a wrong Origin, a missing CSRF token or another-port Origin is rejected.
