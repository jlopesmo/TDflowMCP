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

## Conditional decision on memory permissions (owner D18, 2026-11-22)
Accepted for Phase 2A, conditionally:
1. **Memory access derives from the agent's role and operator scopes.** No separate `memory:*` token grants are introduced yet.
   - A task is visible only to agents that can read all of its scopes.
   - Writing checkpoints and handoffs needs a role with `update` plus read access to the scopes.
2. **Strict scope filtering stays mandatory.** Paths in records, claims, evidence and read-only reasons are filtered to what the caller can read. Hidden tasks answer like missing ones, and the fingerprint map is never returned.
3. **Agent-authored task status is distinct from owner-approved decisions.**
   - A checkpoint's `status` (`in_progress`/`blocked`/`done`), summaries and claims are agent statements, labelled with the agent author and `agent_text_untrusted`.
   - No memory record written by an agent is, or can be presented as, an owner-approved decision. Agents cannot write decision records in Phase 2A.
4. **Dedicated memory permissions come first.** Before agents may write authoritative project ICM, decisions, or repository-backed (Git) documentation, a dedicated memory permission (separate grants, owner-issued) must exist and be enforced server-side. The existing approval gates stay in force.

The condition is reopened if a later increment adds any agent-writable record type beyond checkpoints and handoffs.

**Applied in increment 3 (2026-11-23), repository mode.** Checkpoints and handoffs written in repository mode are repository-backed files, so item 4 applies to them. The dedicated permission is the capability `memory:repo:write`:
- only the user issues it, when creating a token on the paired Setup page, and only to a role that can update;
- it is never implied by a role or an operator scope, and it is stored with the credentials in private storage;
- the engine requires it for every write in repository mode (rule `M8`) and reads it again right before the file is created;
- reading keeps the role-and-scope model, and embedded mode is unchanged.

No new agent-writable record type was added, so the condition is not reopened. **For the owner to confirm:** that this capability is the intended form of the "dedicated memory permission" for repository-backed task records.

## Alternatives
- Two-way sync between the `.toe` and the repo: rejected, because it is exactly the conflicting-authority problem.
- Repo-only memory: excludes users without Git.
- Embedded-only memory: poor diffs and review.

## Validation gates
Phase 1B (passed 2026-11-22): raw operator tools are denied on a tagged `tdflowProject` COMP, for reads and writes (rule H4), in the in-TD suite.

Phase 2A:
- A stale write is rejected. **Passed 2026-11-22** (in TD, `MEMORY_CONFLICT`; also a write between plan and execute, unit-tested).
- A mismatched `Projectid` blocks linking. **Passed 2026-11-23** (in TD, increment 3): a folder whose `tdflow-project.json` names another `Projectid` cannot be linked, and if the file changes later, reads, writes, claims and migrations are refused (`MEMORY_LINK_MISMATCH`).
- A migration in both directions preserves records and hashes. **Passed 2026-11-23** (in TD, increment 3): embedded → repository → embedded with records written in both modes; every record arrives with the same text hash, and the ledger stays valid without a claim.
- Raw operator tools are denied on `tdflowProject` (verified in Phase 1B; re-verified on record DATs in 2A).
- **Project identity (D18; passed 2026-11-22 in TD):** a `tdflowProject` whose `Projectid` differs from the one this project's audit log created or claimed is detected and read-only until the user claims it. Repository mode's `tdflow-project.json` check passed in increment 3 (above).
- **Restart persistence (D18; passed 2026-11-22 in TD):** saved records, ledger and fingerprints survive a TouchDesigner restart. A record written after the last save is reported missing, and memory is restored by Claim Project Memory.
- Additional checks in increment 1 (passed): a single authority (two `tdflowProject` components are refused), and edited, missing or foreign records make memory read-only until the user's Claim Project Memory. Fork and rewind detection uses the audit log as ledger instead of a `Datadir` registry ([continuity §7](../architecture/continuity-and-memory.md#7-phase-2a-increment-1-what-is-implemented-2026-11-22)).
