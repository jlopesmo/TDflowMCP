# ADR-0009 — Permission model

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to the Phase 1B enforcement gates. Revision: hard denies also cover the optional `tdflowProject` COMP for raw operator tools ([ADR-0013](0013-project-memory-model.md)).

## Context
None of the reviewed projects enforce scoped permissions server-side. TDMCP relies on OAuth consent and read-only annotations, and Envoy relies on client-side permission settings ([research](../research/2026-11-22-landscape-and-comparison.md)). The owner requires server-side CRUD enforcement, operator scopes and agent identity.

## Decision
Adopt the model in [security-model.md](../architecture/security-model.md):
- Per-agent bearer tokens map to an agent identity and role.
- Actions `read`/`create`/`update`/`delete`/`execute` apply to glob scopes of normalized, absolute operator paths.
- Deny by default. An explicit deny wins. For mutations every target must pass (no partial execution); recursive reads filter out unpermitted subtrees.
- Effects that make TD evaluate Python (expressions, executable DAT text, executable op types) require `execute` ([classification](../architecture/security-model.md#33-action-classification-of-tool-effects-security-critical)).
- Hard denies that cannot be overridden: every TDflowMCP instance (by tag) and its descendants; flag, rename, move and delete changes on their ancestors; `/sys`, `/local`, `/ui`; anything outside the host project root ([§3.4](../architecture/security-model.md#34-hard-denies-non-overridable)).
- Approval constraints on grants, plus global approval rules.
- Every tool must declare its required actions and a target resolver, and a tool without them cannot be registered (enforced by a registry unit test).
- The policy engine is pure Python with no `td` import, so it can be exhaustively unit-tested.

## Alternatives
Tool-level allowlists only (too coarse; cannot express "edit only /project1/fx"); MCP client-side permissions (not enforceable); OAuth scopes (coarse and complex; may be layered on later).

## Consequences
Target resolution is security-critical. Each resolver needs dedicated tests covering path traversal, shortcuts, global OP shortcuts, clones/replicants and operators created during the call.

## Validation gates
- **Phase 1B acceptance item 1: passed** in the in-TD suite on the packaged `.tox`. It covers scoped allow/deny, expression-as-execute, and protection of instances, ancestors and `tdflowProject`. **Phase 1A acceptance item 4: passed.**
- **Revision (review 6):** creation uses an **allowlist** of operator types in Phase 1. Reference, file and Python-style parameters, wires into DATs, and DAT text without the user's `tdflowdata` tag are classified as `execute`. Clone and shortcut parameters are denied.
- The policy engine's branch-coverage target is **not measured** (no coverage tool available; none installed).
- Any bypass found in review blocks Phase 1C. **Review 6 found three High classification gaps; all are fixed and re-tested** ([review 6](../research/2026-11-22-phase-1b-review.md)).
