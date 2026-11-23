# ADR-0012 — Persistent session logs, task checkpoints and verified handoffs

**Status:** Conditionally Accepted (owner, 2026-11-22; roadmap D12), subject to the validation gates below. Phase 1B implements the session-log part (decision 1); checkpoints and handoffs are Phase 2A.

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
- **Phase 1B (passed 2026-11-22, [Phase 1B record](../research/2026-11-22-phase-1b-mcp-core.md)):**
  - Session logs are views over the audit store: sessions are grouped from audit events, agent notes are audited events, and nothing is a second log of truth.
  - Sessions persist across a TouchDesigner restart.
- **Phase 2A (both passed 2026-11-22 in TD 2025.32440, `run_td_memory_tests.py`; see the [validation index](../icm/validation-index.md)):**
- A handoff's false claim (an edit that never happened) is tagged `contradicted`.
- Resume detects an out-of-band edit through a fingerprint change.
- Implementation note: checkpoints and handoffs are embedded-mode records; the audit log is their ledger ([continuity §7](../architecture/continuity-and-memory.md#7-phase-2a-increment-1-what-is-implemented-2026-11-22)).
