# Decision records (ADRs)

Each ADR has a `**Status:**` line: **Proposed**, **Accepted** (with owner and date), **Superseded by ADR-XXXX**, or **Rejected**. Only Accepted ADRs bind implementation. Proposed ADRs are the recommended default for Phase 1 *planning*, but code that depends on them must not merge to `main` before acceptance.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-product-scope.md) | Product scope and pillars | Mission Accepted; scope details Proposed |
| [0002](0002-in-process-mcp-host.md) | Host the MCP server inside TouchDesigner via Web Server DAT (no separate Node server) | Proposed |
| [0003](0003-protocol-implementation.md) | Hand-rolled MCP protocol layer instead of the official Python SDK | Proposed |
| [0004](0004-code-mode-strategy.md) | Code Mode as an optional, policy-checked facade (not a sandbox) | Proposed |
| [0005](0005-dashboard.md) | Dashboard: static vanilla UI served by the component, admin pairing | Proposed |
| [0006](0006-audit-store.md) | Audit/approvals in append-only, hash-chained SQLite outside the repo | Proposed |
| [0007](0007-source-first-build.md) | Source-first: text `src/` is truth, `.tox` is a built artifact; test strategy | Proposed |
| [0008](0008-upstream-reuse-policy.md) | Upstream reuse and licensing policy | Accepted (owner brief 2026-11-22) |
| [0009](0009-permission-model.md) | Deny-by-default CRUD+execute permissions on operator-path scopes with agent identity | Proposed |

Template: copy an existing ADR and keep its Context / Decision / Alternatives / Consequences / Validation sections.
