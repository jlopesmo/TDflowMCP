# ADR-0005 — Local browser dashboard

**Status:** Proposed (2026-11-22). Delivery: read-only in Phase 2a; approvals and policy editing in Phase 2b.

## Decision
- Served by the component's Web Server DAT at `http://127.0.0.1:<port>/ui/`, same origin as `/api`.
- Static HTML, vanilla ES modules and CSS in `src/dashboard/`. No build step and no CDN, so it works offline on locked-down PCs.
- Live updates over WebSocket (`/api/events`). REST for sessions, agents, tokens, policy, audit queries, approvals and report export.
- **Admin authentication:** a one-time pairing code displayed inside TD is exchanged for an HttpOnly, SameSite=Strict cookie. The cookie is CSRF-protected by an Origin check plus a per-session CSRF token header. Agent bearer tokens never grant dashboard access.
- The dashboard is a **view and decision surface only**. All enforcement stays server-side, so a modified UI cannot bypass policy.

## Alternatives
React/Vite SPA (needs npm and a build); TD-native panel UI (not browser-based, harder to review); a separate web server process. See [technology stack](../architecture/technology-stack.md).

## Consequences
UI work is plain JS, so components stay small. Revisit with an ADR if complexity grows.
