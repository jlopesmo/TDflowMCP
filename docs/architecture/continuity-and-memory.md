# Continuity and shared project memory

**Status:** design for owner-approved requirements P15–P17 ([ADR-0012](../decisions/0012-session-continuity.md), [ADR-0013](../decisions/0013-project-memory-model.md), both Conditionally Accepted). Session logs (§4) are implemented since 1B. **Phase 2A increment 1 is implemented: embedded-mode project memory, task checkpoints, verified handoffs and resume ([§7](#7-phase-2a-increment-1-what-is-implemented-2026-11-22)).** Where §7 differs from §2–§6, §7 describes what exists; repo mode and migration remain design.

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

## 7. Phase 2A increment 1: what is implemented (2026-11-22)

**Implemented and tested** in [`src/tdflowmcp/memory.py`](../../src/tdflowmcp/memory.py), the engine's memory path ([`engine.py`](../../src/tdflowmcp/engine.py)) and the TD adapter ([`tdops.py`](../../src/td_adapter/tdops.py)), with evidence in the [validation index](../icm/validation-index.md). **Embedded mode only**: repo mode, `tdflow-project.json`, the `Projectid` link check and mode migration are later 2A increments.

**`tdflowProject` COMP.** The user creates it with **Create Project Memory** (TDflowMCP Advanced page; refused if one exists; audited `memory_create`). It is a Base COMP under Project Root with the `tdflowProject` tag, a read-only `Projectid` (`tfp_` + 16 hex, distinct from TDflowMCP's storage `Projectid`), `Memoryauthority` = `embedded`, and a README DAT. Raw operator tools stay H4-denied on it. Two tagged COMPs under the root are refused (`MEMORY_UNAVAILABLE`, single authority).

**Records.** One Text DAT per record, named `<type>_<task_id>_r<rev>`, holding canonical JSON `{record_id, type, task_id, rev, based_on_rev, author, created_at, body, sha256}`. One revision counter per task, shared by checkpoints and handoffs. A write must name the latest revision (`based_on_rev`), otherwise `MEMORY_CONFLICT`. The write re-checks on the main thread that the record set is unchanged since planning. Token-shaped strings in agent text are removed before storing (records are saved in the `.toe`).

**Fork, rewind and outside edits: the audit log is the ledger** (this replaces the `Datadir` authority registry of §2):
- Every record TDflowMCP writes is announced first in a durable `intent` (record id and text hash), then confirmed by an `ok` outcome.
- **Claim Project Memory** (TDflowMCP Advanced page) is an audited `admin` event listing the record hashes the user accepts.
- Each memory call compares the COMP's records with this ledger. *changed outside* = a known record with different text; *missing* = a written record that is gone (an older save was opened, the project was not saved, or the DAT was deleted); *foreign* = a record this project's audit never wrote (a copied component, or a project copy claimed as new); *invalid* = not a record.
- Any of these makes memory **read-only for agents** (`MEMORY_READ_ONLY`, rule M5) until the user claims. Reads keep working and report the state.
- Because a copied project gets a new audit log (storage `COPIED` → Claim As New Project), its embedded memory is *foreign* there until the user claims it, so two copies never silently share one authority.
- **Project identity verification (increment 2, D18):**
  - The audit log binds the project to one memory `Projectid`: the latest `memory_create` or `memory_claim` event.
  - A `tdflowProject` with another `Projectid` makes memory read-only (`identity_mismatch`) until the user claims it. It may have been replaced, pasted from another project or had its id edited.
  - Claiming another memory forgets the previous one's records, so swapping the old component back needs a claim too.
- **Restart persistence and restoration (increment 2, D18, verified in TD):**
  - Records are ordinary DATs saved with the `.toe`. After a real TouchDesigner restart from the saved file, the records, `Projectid`, ledger state and fingerprints are intact. Resume reports no out-of-band change (`reliable: true`).
  - A record written after the last save is reported `missing` (memory read-only, with the reason). After Claim Project Memory, writing continues from the restored revision.

**Checkpoints** (`tdflow_task_checkpoint`): the server attaches the audit range since the task's previous record (or since the caller's first audited event in this session; with none yet, the window starts now), the successful audited changes in the task's scopes that the caller can read, the TD build, the server version, and a **fingerprint**. The fingerprint is one 16-hex digest per operator: type; constant values and expression/bind text of non-read-only parameters; inputs; bypass/display/render; and the text of input-less Text and Table DATs. Nothing is evaluated or cooked, and positions are excluded. The main-thread walk visits at most 2000 operators, and the cap is 300 fingerprinted operators (`truncated` beyond). At most 5000 audited mutations are read per window; a larger window sets `window_complete: false`, and audit-based claims are then `unverified`.

**Handoffs** (`tdflow_handoff_create`): the summary plus up to 50 claims, each tagged by the server:
- `changed`, `created`, `deleted`, `unchanged` (path): checked against **the caller's own** audited changes since the task's previous handoff (`changed`/`unchanged` include paths below). `unchanged` means "not changed by you through TDflowMCP", not "unchanged by anyone";
- `exists`, `absent` (path) and `value` (path, param, value): checked against the live project. Paths the caller cannot read are `unverified`, without revealing anything;
- `note`: `unverified`.

**Resume** (`tdflow_handoff_resume`): the latest handoff and checkpoint, audited changes since the latest record, and a fingerprint comparison.
- Records are author-labelled, with `agent_text_untrusted: true`, and carry their `ledger` status: `accepted`, `changed_outside` or `foreign`. Only `accepted` records are known to the audit log. The `sha256` seal is unkeyed: it detects careless edits, not forgery.
- Claim paths and their evidence are filtered to what the caller can read. The fingerprint map stays server-side.
- `changed_outside_tdflowmcp` is true when an operator's digest changed (or it was added or removed) and no audited change at that path or an ancestor explains it. `reliable` is false when either fingerprint was capped or the audit window was not read whole.
- Known false positive: an operator whose input source was deleted, or changed by a path outside the explanation rule.

**Access (conditionally accepted by the owner, D18; see [ADR-0013](../decisions/0013-project-memory-model.md#conditional-decision-on-memory-permissions-owner-d18-2026-11-22)):** memory access derives from operator scopes instead of separate `memory:*` grants. Agent-authored status is distinct from owner-approved decisions. Dedicated memory permissions are required before agents can write authoritative ICM, decisions or Git-backed docs.
- Any agent with grants can list and resume tasks whose scopes it can fully read. Other tasks are hidden: they are counted in `hidden_tasks`, and resuming one answers like a missing task (M6). Writing to one is refused (M4). Read-only reasons list record names only for visible tasks, with counts for the rest.
- Writing needs a role with `update` (builder; observers get M1) and read access to the task's scopes.
- Task status and every presented record are labelled `agent_reported` (`status_source`, `authority`). Agents cannot write decision records (D18).
- Separate memory grants are deferred to token management in the 2B dashboard.
- There is no delete tool.

**Tell Your Coding Agent** gains a **Resume earlier work** section: fixed text plus two numbers (tasks, tasks not marked done), and the instruction to call `tdflow_task_list` and then `tdflow_handoff_resume` before changing anything. Task ids are chosen by agents, so they are not shown there (review 9): the agent gets them, scope-filtered and ledger-checked, from the tools.

**Main-thread work per call:**
- one snapshot step (records, plus a walk bounded at 1000 operators);
- then the fingerprint **in chunks of 50 operators, one main-thread step each**. Each chunk re-checks the kill switch and the token. The fingerprint therefore spans several frames: it is not atomic, and audited changes after `audit_to` explain anything that lands in between;
- for writes, one write step. The write step compares the record set by name and text hash without parsing JSON. Parsed records are cached by text hash.

**Measured** on 2025.32440 (candidate `68e1f1f9…`, [increment 2](../research/2026-11-23-phase-2a-increment-2.md)):
- memory calls: p50 ≈ 113 ms, max ≈ 230 ms;
- a checkpoint or resume over a 2000-operator scope (1000 walked, 300 fingerprinted): ≈ 185 ms, with a frame max of **7.1 ms**. Before chunking, a 300-operator fingerprint was one step of ≈ 19–28 ms.

**Not yet:**
- repo mode, `tdflow-project.json` and the `Projectid` link check;
- migration between modes;
- tasks, decisions and ICM record types (`tdflow_memory_get/put`);
- record archiving;
- (TouchDesigner restart persistence: **verified in increment 2**.)
