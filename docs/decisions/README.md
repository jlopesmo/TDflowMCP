# Decision records (ADRs)

Each ADR has a `**Status:**` line using one of these words:
- **Proposed**
- **Accepted**, recorded with owner and date. "Conditionally Accepted" means accepted, subject to the validation gates listed in the ADR. A failed gate reopens the ADR.
- **Superseded by ADR-XXXX**
- **Rejected**

Only Accepted ADRs bind implementation. Work that depends on a **Proposed** ADR may proceed on `dev` as an experiment, but must not merge to `main` until the ADR is accepted.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-product-scope.md) | Product scope, pillars and sequencing | Conditionally Accepted (owner 2026-11-22) |
| [0002](0002-in-process-mcp-host.md) | Host the MCP server inside TouchDesigner via an embedded loopback transport (stdlib Python HTTP by default; Web Server DAT optional); no separate Node server | Conditionally Accepted (owner 2026-11-22; revised after S6; component integration verified in Phase 1A) |
| [0003](0003-protocol-implementation.md) | Hand-rolled MCP protocol layer instead of the official Python SDK | Conditionally Accepted (owner 2026-11-22) |
| [0004](0004-code-mode-strategy.md) | Code Mode as an optional, policy-checked facade (not a sandbox) | Conditionally Accepted as direction (owner 2026-11-22; Phase 3) |
| [0005](0005-dashboard.md) | Governance dashboard (separate from setup) | Conditionally Accepted (owner 2026-11-22; Phase 2B) |
| [0006](0006-audit-store.md) | Audit/approvals in append-only, hash-chained SQLite outside the repo | Conditionally Accepted (owner 2026-11-22) |
| [0007](0007-source-first-build.md) | Source-first: text `src/` is truth, `.tox` is a built artifact; test strategy | Conditionally Accepted (owner 2026-11-22; gates S2, S3) |
| [0008](0008-upstream-reuse-policy.md) | Upstream reuse, licensing policy, historical bridge | Accepted (owner 2026-11-22) |
| [0009](0009-permission-model.md) | Deny-by-default CRUD+execute permissions on operator-path scopes with agent identity | Conditionally Accepted (owner 2026-11-22) |
| [0010](0010-setup-assistant-and-component-parameters.md) | Setup Assistant, essential parameters, compatibility checks, troubleshooting | Conditionally Accepted (owner 2026-11-22; Phase 1A gates) |
| [0011](0011-agent-integration-and-skills.md) | Agent integration, Skills, "Tell Your Coding Agent" | Proposed |
| [0012](0012-session-continuity.md) | Session logs, task checkpoints, verified handoffs | Proposed |
| [0013](0013-project-memory-model.md) | Shared project-memory model and optional `tdflowProject` COMP | Proposed |

The requirements behind ADRs 0010–0013 are owner-approved. ADR-0010's design is Conditionally Accepted; the designs of 0011–0013 are Proposed.

Template: copy an existing ADR and keep its Context / Decision / Alternatives / Consequences / Validation gates sections.
