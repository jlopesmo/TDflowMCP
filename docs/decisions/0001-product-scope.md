# ADR-0001 — Product scope, pillars and sequencing

**Status:** Conditionally Accepted (owner, 2026-11-22). Mission and pillars P1–P17 are owner-approved. The sequencing below is revised per the owner, and acceptance holds subject to the validation gates.

## Context
The owner brief defined pillars P1–P8. The owner's revision on 2026-11-22 added P9–P17: self-contained `.tox` with essential parameters, Setup Assistant, compatibility checks, guided troubleshooting, Skills integration, "Tell Your Coding Agent", session continuity, an optional `tdflowProject` COMP, and a shared project-memory model. The revision also asked to bring a minimal setup experience forward, keep execution security-first, defer advanced orchestration, and keep the setup page separate from the governance dashboard.

## Decision
Pillars as listed in the [product vision](../product/vision.md). Sequencing per the [roadmap](../icm/roadmap.md):
1. **Phase 1A:** spikes, component shell, essential parameters, read-only Setup Assistant and compatibility checks. MCP is not available yet.
2. **Phase 1B:** the secure MCP core (identity, policy, audit, core tools, kill switch), paired Setup actions, and session logs.
3. **Phase 1C:** agent integration (connection instructions, Skills, version management).
4. **Phase 2A:** continuity and project memory.
5. **Phase 2B:** the governance dashboard.
6. **Phase 3:** experiments, recovery and Code Mode.
7. **Phase 4:** extensions and multi-agent work.

Enforcement (1B) must be proven before any advanced orchestration (Phases 3–4).

## Alternatives
- Feature parity first, security later: rejected, because retrofitting enforcement onto many tools is error-prone.
- Dashboard before setup: rejected by the owner.

## Consequences
Phase 1 delivers fewer tools than upstream projects, but each tool is born with a target resolver and audit. Users get a working setup page before MCP access exists.

## Validation gates
Phase 1A, 1B and 1C acceptance criteria in [phase-1](../milestones/phase-1-core-server.md). A failed gate reopens this ADR.
