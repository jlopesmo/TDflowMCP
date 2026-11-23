# ADR-0013 — Shared project-memory model and optional `tdflowProject` COMP

**Status:** Conditionally Accepted (owner, 2026-11-22; roadmap D12), subject to the validation gates below. Memory tools are Phase 2A. Phase 1B already enforces decision 5's hard deny: raw operator tools cannot touch a `tdflowProject` COMP.

## Context
Host projects need ICM, decisions, roadmap, tasks and continuity records. These can live inside the `.toe`, in a `tdflowProject` COMP, or in an external Git repository. Keeping both as editable truth creates conflicting authoritative records, which the owner explicitly forbids.

## Decision
Adopt [continuity-and-memory.md §1–3](../architecture/continuity-and-memory.md#2-authority-model-avoids-conflicting-authoritative-records):
1. Exactly **one** authoritative home per project, declared by `Memoryauthority` = `repo` | `embedded`. The other home is a stamped, read-only derivative.
2. The project identity (`Projectid`) appears in both the COMP and `tdflow-project.json`, and the two must match.
3. Records are revisioned and hashed. Stale writes return `MEMORY_CONFLICT`. Out-of-band edits are surfaced, never merged automatically.
4. Mode switching is an explicit, recorded migration.
5. `tdflowProject` is optional and separate from `TDflowMCP.tox`, and is created only by user action. Agents write to it only through memory tools, and raw operator tools are hard-denied on it.
6. Runtime security state (tokens, audit, approvals) is never project memory.
7. This repository's own ICM is the tool's development memory. It is not a host project's memory.

## Alternatives
- Two-way sync between the `.toe` and the repo: rejected, because it is exactly the conflicting-authority problem.
- Repo-only memory: excludes users without Git.
- Embedded-only memory: poor diffs and review.

## Validation gates
Phase 1B (passed 2026-11-22): raw operator tools are denied on a tagged `tdflowProject` COMP, for reads and writes (rule H4), in the in-TD suite.

Phase 2A:
- A stale write is rejected. **Passed 2026-11-22** (in TD, `MEMORY_CONFLICT`; also a write between plan and execute, unit-tested).
- A mismatched `Projectid` blocks linking. **Not yet:** repo mode is a later 2A increment.
- A migration in both directions preserves records and hashes. **Not yet:** migration is a later 2A increment.
- Raw operator tools are denied on `tdflowProject` (verified in Phase 1B; re-verified on record DATs in 2A).
- Additional checks in increment 1 (passed): a single authority (two `tdflowProject` components are refused), and edited, missing or foreign records make memory read-only until the user's Claim Project Memory. Fork and rewind detection uses the audit log as ledger instead of a `Datadir` registry ([continuity §7](../architecture/continuity-and-memory.md#7-phase-2a-increment-1-what-is-implemented-2026-11-22)).
