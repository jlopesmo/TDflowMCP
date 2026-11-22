# ADR-0012 — Persistent session logs, task checkpoints and verified handoffs

**Status:** Proposed (2026-11-22). The requirement (P15) is owner-approved; this design awaits acceptance.

## Decision
Adopt [continuity-and-memory.md §4–6](../architecture/continuity-and-memory.md#4-persistent-session-logs-1b):
1. **Session logs** in Phase 1B are views over the audit store, plus optional agent notes. No second log of truth.
2. **Task checkpoints** in Phase 2A attach server-verifiable facts: audit range, changed paths and a scope fingerprint.
3. **Verified handoffs** in Phase 2A strictly separate server-verified facts from agent claims. Each claim is tagged `verified`, `contradicted` or `unverified` against recorded evidence.
4. Checkpoints and handoffs are **project memory** records ([ADR-0013](0013-project-memory-model.md)). They reference audit by sequence and hash, never by copied rows.
5. Agent-authored text is untrusted when replayed to later agents.

## Alternatives
- Free-text handoff notes only: unverifiable, which defeats the purpose.
- Storing full audit in project memory: leaks arguments and bloats Git and the `.toe`.

## Validation gates
- A handoff's false claim (an edit that never happened) is tagged `contradicted`.
- Resume detects an out-of-band edit through a fingerprint change.
