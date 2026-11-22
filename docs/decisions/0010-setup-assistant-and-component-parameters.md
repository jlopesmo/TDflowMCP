# ADR-0010 — Setup Assistant, essential parameters, compatibility checks and troubleshooting

**Status:** Conditionally Accepted (owner, 2026-11-22; recorded as roadmap decision D9) for Phase 1A, subject to the validation gates below. The requirements (P9–P12, P14 setup/repair) are owner-approved.

## Context
The owner requires a self-contained drag-and-drop `.tox` with essential custom parameters, a browser Setup Assistant usable before MCP access is enabled, compatibility checks (TD build, bundled Python, dependencies, ports, coding agents), and guided troubleshooting **without unauthorized installations or configuration changes**. The owner also asked that this minimal setup experience arrive earlier than, and separately from, the governance dashboard.

## Decision
Adopt [setup-assistant.md](../architecture/setup-assistant.md):
1. Explicit component states (Off → Setup → Enabled / Suspended / Error). The component never listens until the user acts. The listener starts `VERIFYING` and serves no application request until the effective loopback-only binding is verified. Failure closes it immediately ([bind-verification gate](../architecture/security-model.md#82-bind-verification-gate)).
2. The essential parameter set, phased 1A/1B. Parameters never hold long-lived secrets.
3. `/setup/` is served by the same loopback transport and gate as `/mcp` (the embedded Python transport by default, [ADR-0002](0002-in-process-mcp-host.md)). Its HTML/CSS/JS live in Text DATs inside the `.tox` and are served from an immutable snapshot; inline script is not allowed (CSP). It is read-only in 1A; paired, CSRF-protected actions arrive in 1B. It is separate from the `/ui/` governance dashboard (Phase 2B, [ADR-0005](0005-dashboard.md)).
4. Compatibility checks report pass/warn/fail/unknown with evidence. **Oldest tested and supported build: 2025.32440** (owner D10, 2026-11-22; previously a provisional 2025.33230+ target). Older builds get `warn`, and may enable MCP only when every security self-test passes. No override exists.
5. Troubleshooting is diagnosis plus instructions. The only automatic actions are TDflowMCP-internal and reversible. Everything else needs explicit, previewed user confirmation, and installs and client-permission edits are never performed.
6. Default port **9480** with conflict detection. The port is never changed automatically.

## Alternatives
- A TD-native panel UI for setup: more TD-specific work, and harder to share with the dashboard.
- Setup inside the full dashboard: rejected by the owner's sequencing.
- Auto-fixing environment problems, e.g. pip installs: rejected by the owner's requirement.

## Validation gates
- **S5 (executed 2026-11-22 on 2025.32440, [findings](../research/2026-11-22-s5-compatibility-2025.32440.md)):**
  - **Pass:** custom-parameter APIs, the `ui.clipboard` text round-trip, `onStart` firing on project load, bundled Python 3.11.10 and libraries, and the bind gate's fail-closed behaviour.
  - **Warn:** the `projectpresave` hook exists, but firing was not exercised. Menu `.default` and same-frame pulse nuances (see findings).
  - **Unknown:** the end-to-end browser launch (`ui.viewFile` exists, but no verified listener can exist on that build).
  - **Fail (security), Web Server DAT only:** that transport cannot bind loopback-only on 2025.32440 and is never used there.
- **S6 (executed 2026-11-22 on 2025.32440, [findings](../research/2026-11-22-s6-embedded-python-transport.md)):** the embedded transport served the Setup page from Text DATs.
  - Loopback-only access was verified, with no off-main-thread TD access.
  - Headless Chrome ran the page's JS, and its `fetch` of `/setup/api/status` passed the gate.
  - HTTP header, body and latency behaviour are now measured. **The web Setup Assistant is available on 2025.32440.**
- **Open:** the browser launch from TouchDesigner itself (`Opensetup`), and Phase 1A acceptance ([phase-1](../milestones/phase-1-core-server.md)).
