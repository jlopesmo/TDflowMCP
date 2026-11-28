# ADR-0021 — Compact network inspection

**Status:** Proposed. [Issue #29](https://github.com/jlopesmo/TDflowMCP/issues/29) authorizes development and benchmark evaluation on `dev`.

## Context

The one-level description in v1.7.0 returns full operator rows, passive cook measurements and heuristic hints. Exploring nested components takes repeated calls. Connector inspection, named parameter reads and handoff resume provide distinct forms of evidence.

## Decision

Extend `tdflow_describe_network` with an opt-in compact request. Traverse readable components and return sorted pages of readable operator facts. Reuse native connector snapshots for exact wires between operators on the page. Search names, types and the verified local catalog. Use nonrecursive diagnostic flags for non-COMP operators. Keep raw diagnostic text, comments and hidden counts out of the compact response.

Only inspect explicitly named, eligible parameters on displayed operators. Parse stored expressions and bind text for exact literal parameter and CHOP channel references. Reauthorize the referenced operator and report the result as a candidate. Missing, denied and unsupported references use the same unresolved response. Leave the existing detailed request and project-memory tools available. [Parameter metadata](https://derivative.ca/UserGuide/Par_Class) · [Native connectors](https://derivative.ca/UserGuide/Connector_Class)

Dev2 adds an exact-path request for operators the caller already knows. The server checks each descendant and its ancestor chain before the native lookup, rechecks the resolved operator and omits unreadable and missing entries. This path skips sibling enumeration. Broad discovery reads diagnostic flags for the returned page, plus the explicitly selected status when filtering.

## Alternatives

A separate search tool would duplicate scope traversal. A project-wide index would require permission-bound invalidation and additional evidence about hidden-graph changes. The first batch inspects current state on each request.

## Consequences and validation

The view is bounded and can omit wires whose endpoints are on other pages. Callers can verify exact ports and parameter state with the existing tools. Count only readable operators toward the 300-operator limit and only shown edges toward the 128-edge output limit. The wall-clock deadline is checked after native enumeration and before returning the result. A native call can run past it before control returns. Runtime cost can depend on the host graph and load, including work on unreadable candidates. The main-thread dispatcher, durable audit, current token, suspension and policy checks apply. Test hidden-graph invariance within the work budget, native passivity, legacy compatibility and live client accuracy before evaluation. [Milestone](../milestones/v1.8-network-intelligence.md)

For release review, accept the residual timing and availability signal only if the ordinary scoped-read threat model can tolerate coarse inference from oversized hidden graphs or connector lists. Uniform omission applies to payloads. An authorized missing target can incur a native lookup that a denied target skips. Prefer exact paths when known and retain the existing scope and audit controls. A confidentiality requirement covering repeated timing observations needs separately validated, independently bounded authorized lookup and enumeration.
