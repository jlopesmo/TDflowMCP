# Phase 0 — Foundation

**Status:** In progress (branch `dev`). Scope per owner brief 2026-11-22.

## Scope
Research and comparison; architecture proposal; ICM documentation; repository structure and rules; doc validation; initial commit pushed to a development branch.

**Explicitly out of scope:** implementing the MCP server, launching services or TouchDesigner, altering real TD projects, merging to `main`.

## Acceptance criteria

| # | Criterion | Evidence location |
|---|---|---|
| A1 | Research covers TDMCP, Embody/Envoy, Cloudflare Code Mode, other TD MCP projects, original `mcp_bridge.tox` (or records absence) with sources and evidence labels | [research](../research/2026-11-22-landscape-and-comparison.md) |
| A2 | Comparison matrix across capability, architecture, licensing, security, performance, deployment, version control, compatibility | research §2–4 |
| A3 | Recommended architecture with component diagram; technology stack with alternatives | [system design](../architecture/system-design.md), [stack](../architecture/technology-stack.md) |
| A4 | Security and permission model | [security model](../architecture/security-model.md) |
| A5 | ADRs with explicit status; accepted vs proposed distinguishable | [decisions](../decisions/README.md) |
| A6 | ICM set (README, context, invariants, current-state, roadmap, project-map, validation-index) consistent and linked | [ICM](../icm/README.md) |
| A7 | Agent instructions mandate ICM maintenance with code changes | [AGENTS.md](../../AGENTS.md) |
| A8 | `python tools/check_docs.py` and its unit tests pass | [validation-index](../icm/validation-index.md) |
| A9 | Independent review performed; findings addressed or recorded | [review record](../research/2026-11-22-phase-0-independent-review.md) |
| A10 | Prioritized Phase 1 plan | [phase-1](phase-1-core-server.md) |
| A11 | Committed and pushed to `dev`; SHA recorded | [current-state](../icm/current-state.md) |
