# Phase 0 — Foundation

**Status:** Revision in progress on branch `dev`. The scope comes from the owner brief and the owner's revision (2026-11-22).

## Scope
- Research and comparison.
- Architecture.
- ICM documentation.
- Repository rules.
- Doc validation and CI.
- The owner's revision: new features, resequencing, applied decisions, and creating `main` from the verified foundation.

**Out of scope:**
- Implementing the MCP server or the production `.tox`.
- Launching services or TouchDesigner.
- Altering user projects.

## Acceptance criteria

| # | Criterion | Evidence location |
|---|---|---|
| A1 | Research covers TDMCP, Embody/Envoy, Cloudflare Code Mode, other TD MCP projects, and the original bridge (supplied and reviewed; [bridge review](../research/2026-11-22-mcp-bridge-review.md)) | [research](../research/2026-11-22-landscape-and-comparison.md) |
| A2 | The comparison matrix covers capability, architecture, licensing, security, performance, deployment, version control and compatibility | research §4 |
| A3 | Architecture with component diagram, and a technology stack with alternatives | [system design](../architecture/system-design.md), [stack](../architecture/technology-stack.md) |
| A4 | Security and permission model | [security model](../architecture/security-model.md) |
| A5 | ADRs with explicit statuses, so accepted, conditional and proposed decisions are distinguishable; the index is checked by tooling | [decisions](../decisions/README.md) |
| A6 | ICM set consistent and linked | [ICM](../icm/README.md) |
| A7 | Agent instructions mandate ICM maintenance | [AGENTS.md](../../AGENTS.md) |
| A8 | `tools/check_docs.py` and its unit tests pass locally | [validation-index](../icm/validation-index.md) |
| A9 | Initial independent review performed and addressed | [review 1](../research/2026-11-22-phase-0-independent-review.md) |
| A10 | Prioritized Phase 1 plan | [phase-1](phase-1-core-server.md) |
| A11 | Committed and pushed to `dev` | [current-state](../icm/current-state.md) |
| **R1** | Owner-approved features P9–P17 are in the vision, architecture and ADRs 0010–0013 | [vision](../product/vision.md) |
| **R2** | Resequenced so the minimal Setup comes in 1A, before the 2B dashboard, with security-first 1B and advanced orchestration deferred | [roadmap](../icm/roadmap.md) |
| **R3** | Owner decisions D1–D8 recorded and applied | [roadmap](../icm/roadmap.md#owner-decisions-2026-11-22) |
| **R4** | Second independent review performed and addressed | [review 2](../research/2026-11-22-phase-0-revision-review.md) |
| **R5** | CI evidence recorded as actually observed, never assumed | [validation-index](../icm/validation-index.md) |
| **R6** | `main` created from the verified, corrected foundation; development continues on `dev` | [current-state](../icm/current-state.md) |
