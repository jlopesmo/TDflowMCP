# ADR-0021 — Compact network inspection

**Status:** Proposed, ready for owner decision. [Issue #29](https://github.com/jlopesmo/TDflowMCP/issues/29) covers development and release preparation on `dev`.

## Context

The one-level description in v1.7.0 returns full operator rows, passive cook measurements and heuristic hints. Exploring nested components takes repeated calls. Connector inspection, named parameter reads and handoff resume provide distinct forms of evidence.

## Decision

Extend `tdflow_describe_network` with an opt-in compact request. Traverse readable components and return sorted pages of readable operator facts. Reuse native connector snapshots for exact wires between operators on the page. Search names, types and the verified local catalog. Use nonrecursive diagnostic flags for non-COMP operators. Keep raw diagnostic text, comments and hidden counts out of the compact response.

Only inspect explicitly named, eligible parameters on displayed operators. Parse stored expressions and bind text for exact literal parameter and CHOP channel references. Reauthorize the referenced operator and report the result as a candidate. Missing, denied and unsupported references use the same unresolved response. Leave the existing detailed request and project-memory tools available. [Parameter metadata](https://derivative.ca/UserGuide/Par_Class) · [Native connectors](https://derivative.ca/UserGuide/Connector_Class)

An exact-path request takes operators the caller already knows. The server checks each descendant and its ancestor chain before the native lookup, rechecks the resolved operator and omits unreadable and missing entries. This path skips sibling enumeration. Broad discovery reads diagnostic flags for the returned page, plus the explicitly selected status when filtering.

## Alternatives

A separate search tool would duplicate scope traversal. A project-wide index would require permission-bound invalidation and additional evidence about hidden-graph changes. The first batch inspects current state on each request.

## Consequences and validation

The view can omit wires whose endpoints are on other pages. Callers can verify exact ports and parameter state with the existing tools. The traversal counts only readable operators toward the 300-operator limit. A page can show up to 128 wires between its returned operators. The main-thread dispatcher, durable audit, current token, suspension and policy checks apply. Validation covers hidden-graph invariance within the work budget, native passivity, legacy compatibility and live client accuracy. [Milestone](../milestones/v1.8-network-intelligence.md)

## Residual timing and availability side channel

- **Broad inspection.** The 75 ms deadline is cooperative. Native child enumeration and individual lookup, diagnostic and connector operations run on the TouchDesigner main thread and can overrun the deadline before returning control. Host load and hidden graph complexity can change latency or produce `NETWORK_INSPECTION_LIMIT` for the same readable selection. Repeated observations can reveal coarse differences in hidden network complexity. A slow native call can also delay other main-thread work.
- **Exact paths.** Known-path requests skip sibling enumeration. A denied path stops at the policy check, while an authorized missing path can incur a native lookup. Both are omitted from a successful payload. Repeated samples can distinguish the processing paths by timing or by a deadline refusal. Keep native access restricted to authorized paths.
- **Observed evidence.** In the dev2 Windows and TouchDesigner 2025.32440 fixture, adding 2,400 denied siblings preserved the exact-path readable result in 5/5 samples. Broad inspection succeeded in 5/5 before the change and refused in 5/5 afterwards. Denied and authorized missing paths returned identical empty results in this fixture. The test established the response boundary and recorded timings. It did not establish timing indistinguishability. [Pressure checks](../icm/validation-index.md#v180dev2-evaluation)

## Owner decision requested

Accept the compact inspection design and the two medium residual observations for v1.8.0 evaluation only if the intended scoped-read threat model tolerates coarse timing inference and occasional work-limit refusals. Recommend exact paths for known operators and retain the existing scope, audit and suspension controls. Keep the release on hold if the threat model requires timing confidentiality or isolation from native main-thread overruns. That higher bar requires separately validated, independently bounded authorized lookup and enumeration. Record the owner's decision with its date and link before promoting this ADR to Accepted.
