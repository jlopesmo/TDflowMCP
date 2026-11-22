# ADR-0013 — Shared project-memory model and optional `tdflowProject` COMP

**Status:** Proposed (2026-11-22). The requirements (P16, P17) are owner-approved; this model awaits acceptance. Implementation is Phase 2A.

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
Phase 2A:
- A stale write is rejected.
- A mismatched `Projectid` blocks linking.
- A migration in both directions preserves records and hashes.
- Raw operator tools are denied on `tdflowProject`.
