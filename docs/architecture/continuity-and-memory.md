# Continuity and shared project memory

**Status: Proposed design** for owner-approved requirements P15–P17 ([ADR-0012](../decisions/0012-session-continuity.md), [ADR-0013](../decisions/0013-project-memory-model.md)). Not implemented.

## 1. Three kinds of state — and where each lives

| Kind | Examples | Home | In Git? | In `.toe`? |
|---|---|---|---|---|
| **Runtime security state** | tokens, policy secrets, audit events, approvals | `Datadir` (per-user local dir) | never | never |
| **Session logs** | per-session timelines, agent notes | derived views over audit in `Datadir`; exportable summaries | summaries only (via project memory) | summaries only |
| **Project memory** | project ICM (context, invariants, current state, roadmap), decisions, tasks, checkpoints, handoffs | **exactly one authoritative home**: project Git repo **or** embedded `tdflowProject` COMP | if repo mode | if embedded mode |

Project memory references audit by `(audit_seq range, chain hash)`; it never copies raw audit rows, tokens or arguments.

## 2. Authority model (avoids conflicting authoritative records)

- The optional `tdflowProject` Base COMP declares `Memoryauthority` = `repo` or `embedded`, and a `Projectid` (UUID).
- **`repo` mode:** files under a repository folder (e.g. `<repo>/docs/icm/`, `<repo>/docs/decisions/`, `<repo>/tdflow/tasks/`) are authoritative. The COMP stores only the pointer (`Repopath`, relative to the `.toe` where possible), `Projectid`, and a **read-only derived cache** with `{record_id, rev, sha256}` marked `derived`. The repo holds `tdflow-project.json` with the same `Projectid`; mismatch → refuse to read/write (wrong repo linked).
- **`embedded` mode:** DATs inside the COMP are authoritative. Exports to files are stamped `DERIVED — authority: embedded tdflowProject (Projectid …, rev N)` and are never re-imported automatically.
- **Switching modes** is an explicit migration (dry run → confirm → copy → mark old home derived), recorded as a migration record in both homes.
- **Fork and rewind detection:** an embedded authority can be cloned by Save As, incremental saves (`proj.5.toe`) or copying the COMP into another `.toe`, all with the same `Projectid`. A repo checkout of an older commit moves `rev` backwards. `Datadir` stores, per `Projectid`, the registered authority instance (COMP `op.id` plus `.toe` identity) and each record's highest seen `rev`. A mismatch puts memory into read-only `FORKED` or `REWOUND` state until the user performs an explicit, audited **Claim authority**. That keeps invariant 10 (one authoritative copy) enforceable.
- **Project key for `Datadir`:** `Projectid` when a `tdflowProject` exists. Otherwise it is `sha256(normalized project folder + project name)`, truncated to 16 hex characters. A Save As without `tdflowProject` therefore starts a new runtime history, which the Setup Assistant reports.
- **Records** carry `{record_id, type, rev, sha256, based_on_rev, author (agent id or human), created_at}`. A write whose `based_on_rev` is stale is rejected with `MEMORY_CONFLICT`; no automatic merge. Human edits made outside TDflowMCP (in the repo or in the DAT) are detected by hash mismatch and surfaced as "changed outside TDflowMCP", requiring the user to accept the new revision.
- TDflowMCP does not run Git commands (commit/push) in Phase 2A; it writes files and reports what changed.
- **Distinct from this repository's ICM:** TDflowMCP's own `docs/icm/` governs the tool's development. `tdflowProject` governs a *host project's* development.

## 3. `tdflowProject` COMP

- Optional, separate from `TDflowMCP.tox` (so upgrading the tool never overwrites project memory); created only by explicit user action (drag in `tdflowProject.tox`, or Setup Assistant "Create" with confirmation).
- Agents modify it **only** through memory tools (`tdflow_memory_*`, checkpoint/handoff tools). Raw operator tools are hard-denied on it (like TDflowMCP instances), so memory schema and revision rules can't be bypassed.
- Memory text authored by agents is stored and returned **labelled with its author**, and is treated as untrusted data (prompt-injection risk) when included in "Tell Your Coding Agent" or resume output.

## 4. Persistent session logs (1B)

A session (see [security model §2.3](security-model.md#23-sessions)) is a grouping over audit events. Session logs are queryable timelines (tools called, decisions, changed paths, durations) plus optional `tdflow_session_note` entries. They persist with the audit store; the Setup Assistant shows a minimal recent-sessions list in 1B; full search is in the 2B dashboard.

## 5. Task checkpoints (2A)

`tdflow_task_checkpoint(task_id, title, status, summary, next_steps)` — the server attaches facts it can verify: audit seq range since the last checkpoint, changed operator paths, TD build, component version, and a **structural fingerprint** (hash of operator paths/types/parameter values) of the task's scopes.

## 6. Verified handoffs (2A)

`tdflow_handoff_create(task_id, summary, claims[])` produces a record with two clearly separated parts:
- **Server-verified facts:** derived from audit and fingerprints (what changed, by whom, when; checks/tests whose results the server recorded).
- **Agent claims:** each claim is tagged `verified` (matched to evidence, with the evidence reference), `contradicted`, or `unverified`.

`tdflow_handoff_resume(task_id)` returns the latest handoff, audit activity since it, and whether the scope fingerprint changed (i.e. edits made outside TDflowMCP since the handoff). "Verified" never means "the agent's prose is true" — only that listed claims matched recorded evidence.
