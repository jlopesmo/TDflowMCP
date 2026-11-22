# ADR-0010 — Setup Assistant, essential parameters, compatibility checks and troubleshooting

**Status:** Proposed (2026-11-22). The requirements (P9–P12, P14 setup/repair) are owner-approved; this design awaits acceptance.

## Context
The owner requires a self-contained drag-and-drop `.tox` with essential custom parameters, a browser Setup Assistant usable before MCP access is enabled, compatibility checks (TD build, bundled Python, dependencies, ports, coding agents), and guided troubleshooting **without unauthorized installations or configuration changes**. The owner also asked that this minimal setup experience arrive earlier than, and separately from, the governance dashboard.

## Decision
Adopt [setup-assistant.md](../architecture/setup-assistant.md):
1. Explicit component states (Off → Setup → Enabled / Suspended / Error). The component never listens until the user acts, and only after the loopback-bind self-test passes.
2. The essential parameter set, phased 1A/1B. Parameters never hold long-lived secrets.
3. `/setup/` is served by the same Web Server DAT and gate as `/mcp`. It is read-only in 1A; paired, CSRF-protected actions arrive in 1B. It is separate from the `/ui/` governance dashboard (Phase 2B, [ADR-0005](0005-dashboard.md)).
4. Compatibility checks report pass/warn/fail/unknown with evidence. Provisional target build: **2025.33230+**. Older builds may run Setup and may enable MCP only when every security self-test passes. No override exists.
5. Troubleshooting is diagnosis plus instructions. The only automatic actions are TDflowMCP-internal and reversible. Everything else needs explicit, previewed user confirmation, and installs and client-permission edits are never performed.
6. Default port **9480** with conflict detection. The port is never changed automatically.

## Alternatives
- A TD-native panel UI for setup: more TD-specific work, and harder to share with the dashboard.
- Setup inside the full dashboard: rejected by the owner's sequencing.
- Auto-fixing environment problems, e.g. pip installs: rejected by the owner's requirement.

## Validation gates
Spike S5 (older-build feasibility, parameters, clipboard and browser-open APIs, pre-save hook). Phase 1A acceptance ([phase-1](../milestones/phase-1-core-server.md)).
