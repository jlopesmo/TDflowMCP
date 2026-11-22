# ADR-0009 — Permission model

**Status:** Proposed (2026-11-22).

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
