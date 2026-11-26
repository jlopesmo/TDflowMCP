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

## Amendment (owner D25, 2026-11-25): changing an agent's role
An agent's role can change after creation, through preview and apply ([ADR-0016](0016-capabilities-page-and-permission-editing.md)): allow grants with the old role's full action set get the new role's set, narrowed grants keep only actions the new role has, deny grants never change, and allow grants never exceed the role. The change and the cancellation of the agent's open approvals are one transaction; more access needs explicit confirmation. Evaluation (§3.2 of the security model) is unchanged.

## v1.1 implementation note

The existing `update` and `execute` checks cover replacing an expression, bind or export when undo can restore it. Recognized literal operator references require `read` on the resolved path. Code authoring approvals additionally bind runtime identity and source or parameter state. Callback editing requires inactive staging for the existing six Execute DAT types. Script operator authoring remains outside the supported set. The [v1.1 record](../milestones/v1.1-developer-capabilities.md) tracks evidence.

**Owner approval, 2026-11-26:** TOP image capture and resolution reads are permitted only for owner-locked outputs, subject to passive-read validation on TouchDesigner 2025.32440. The guard requires the verified Windows and TouchDesigner build combination. A bypassed TOP or enclosing component makes the output ineligible. The existing `read` grant and hard denies continue to apply. Lock is set by the owner in TouchDesigner. The guard checks the current flag state.

The v1.3 aggregate comparison applies this decision to both targets before any cached sample is read. Per-target authorizations, stale-target refusal, the shared readback budget and durable audit protection continue to apply. Image samples and aggregate results are absent from security audit records. [Tool surface](../architecture/tool-surface.md).

### v1.2 component authoring

The owner authorized bounded custom control definitions through the existing policy and exact approval model. Defining or editing controls requires `read`, `update` and `execute` on the Base or Container COMP because a built network can react to changes. Hard denies cover protected ancestors. The allowed styles carry constant definitions. Existing expressions, bindings, dynamic menus and control groups are outside this edit path. Connector changes add `update` on the affected enclosing component. The first Out TOP of a Base COMP can expose a native connector. The owner connects that output to external consumers. See the [v1.2 milestone](../milestones/v1.2-component-authoring.md).
