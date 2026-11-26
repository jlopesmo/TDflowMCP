# Continuity and shared project memory

**Status:** design for owner-approved requirements P15–P17 ([ADR-0012](../decisions/0012-session-continuity.md), [ADR-0013](../decisions/0013-project-memory-model.md), both Conditionally Accepted). Session logs (§4) are implemented since 1B. **Phase 2A increment 1 is implemented: embedded-mode project memory, task checkpoints, verified handoffs and resume ([§7](#7-phase-2a-increment-1-what-is-implemented-2026-11-22)). Increment 3 adds repository mode, the `Projectid` link check and migration ([§8](#8-phase-2a-increment-3-repository-mode-2026-11-23)).** Where §7 and §8 differ from §2–§6, they describe what exists.

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
- tasks, decisions and ICM record types (`tdflow_memory_get/put`);
- record archiving;
- (TouchDesigner restart persistence: **verified in increment 2**; repository mode, the link check and migration: **verified in increment 3**, [§8](#8-phase-2a-increment-3-repository-mode-2026-11-23).)

## 8. Phase 2A increment 3: repository mode (2026-11-23)

**Implemented and tested** in [`src/tdflowmcp/memrepo.py`](../../src/tdflowmcp/memrepo.py) (file I/O, never on the main thread), the engine's memory path, the TD adapter and the Setup page, with evidence in the [increment 3 record](../research/2026-11-23-phase-2a-increment-3.md). Where this section differs from §2, this section describes what exists.

**Layout.** With `Memoryauthority` = `repo`, the authority is a folder the user linked:
- `<folder>/tdflow-project.json`: `{schema, project_id, authority, created_at, updated_at, note}`;
- `<folder>/tdflow/memory/<record_id>.json`: one record per file, the same canonical JSON as a record DAT;
- `<folder>/tdflow/memory/MIGRATIONS.jsonl`: one informational line per migration.

The component keeps the read-only `Repopath`, the `Projectid`, a `derived_index` DAT (counts and a digest, stamped DERIVED) and a `MIGRATIONS` note. It holds no record DATs.

**The linked folder.** `Repopath` is always relative to the `.toe` folder: `.`, a folder below it, or up to three levels straight above it. Absolute paths, drive letters, sibling folders, and anything inside `.git` or `.tdflowmcp` are refused. No symbolic link or junction may be on any component between the `.toe` folder and the linked folder (the real path must equal the real path of the `.toe` folder plus the link; review 11), nor on `tdflow/`, `tdflow/memory/` or a touched file. After a write, the file's real path is checked again. TDflowMCP writes nothing outside `tdflow-project.json` and `tdflow/memory/`, never overwrites or deletes a record, and never runs Git.

**Link check.** Before every read, write, claim and migration, `tdflow-project.json` must name this memory's `Projectid` with authority `repo`:
- no file → `MEMORY_UNAVAILABLE` (unlinked);
- another `Projectid` → `MEMORY_LINK_MISMATCH`. Claim Project Memory does not override it; the user must link the right folder;
- authority `embedded` → the folder is a derived export left by a migration back (`MEMORY_UNAVAILABLE`, stale).

**Untrusted files (review 11).** A record file is data from outside until the audit log accepts it:
- reading a record never raises; anything malformed, oversized, linked or not encodable is `invalid` and named, and is never claimable;
- the fields the server wrote are validated strictly when read back (`author` is an id, `created_at` a timestamp, fingerprint paths are operator paths, digests and parameter names are plain tokens);
- resume returns **no comparison** when the task's latest record is not `accepted` (`fingerprint.comparison: "unavailable"`), and each `tdflow_task_list` row carries its `ledger` status;
- an announced record whose write definitely failed (`error` or `aborted` outcome) is not authentic if a file with that text appears.

**Ledger.** Unchanged: the audit log announces every record (intent with the text hash) and the user's claims. Files changed outside TDflowMCP, for example by a Git checkout or pull, are `changed_outside`, `foreign` or `missing`, and memory is read-only for agents until Claim Project Memory. In repository mode the claim reads the files on a background thread.

**Threads.** The engine reads the files on its HTTP handler thread. A write is: durable intent → a main-thread step that re-checks the kill switch, the token and the grants and confirms that the component still links the same folder (it changes nothing) → the kill switch, token and capability are read once more → under one process-wide lock the link is checked again and the file is created only if the record set is unchanged (so two requests can never write the same revision, and a migration cannot switch the authority in between; review 11) → durable outcome → the derived index is refreshed. TouchDesigner's main thread never waits for the disk. When a task's recorded scopes are needed, the engine reads them from the files and plans a second time.

**Access (owner condition D18).** Reading follows role and operator scopes, as in embedded mode. **Writing in repository mode additionally needs the capability `memory:repo:write`**, which only the user issues when creating a token (builder role). Without it: `DENIED`, rule `M8`. `tdflow_whoami` reports `memory.repository_write`.

**Migration.** Setup page → Project memory, paired admin session: a preview (`POST /setup/api/memory/plan`) lists every action and returns a digest; the migration (`POST /setup/api/memory/migrate`) runs only for that exact digest, and only while the ledger is clean (otherwise claim first).
- *Embedded → repository:* refused if the folder holds another project's file or different records. The files are written and verified by hash; then the component is switched in one main-thread step that also re-checks the record set. If that step does not happen, the folder is marked as a derived export.
- *Repository → embedded:* the component's record DATs become exactly the folder's records; the folder is then marked `DERIVED` and is no longer read. The files are left in place.
- *Re-link (review 11):* for repository mode whose link was lost (the `.toe` was not saved after a migration, the project was saved elsewhere, `tdflow-project.json` is missing or marked derived). The component points at the folder again and the folder is marked as this memory's authority. No record is changed, another project's folder is refused, and records the audit log does not know still need a claim.
- Records already in the target folder are never overwritten: a different version of a record is a conflict; other records are left in place and face the ledger afterwards.
- Every preview lists the other files written (`also_written`) and says that anyone who can read the files can read every task record in them.
- All three write `memory_migrate` audit events before (`started`) and after (`ok`, `error` or `unknown`), with the relative folder. A failure after `started` always closes the trail. Record text and hashes do not change, so no claim is needed after a move.

**Friction fixes delivered with this increment** ([record](../research/2026-11-23-phase-2a-increment-3.md#dogfood-friction)): an optional `status` on handoffs; the parameters that changed, per operator, in the resume answer (`fingerprint.parameter_changes`; digests of non-default parameters are keyed with the installation key, kept in the record only in the room the rest of the record leaves, at most 20,000 characters, and never returned; with another key the detail is `known: false`); default placement of created operators.

## 9. Safe source reads in v1.1

The adapter reads the project id, authority and repository link in constant mode. Reserved records, the derived index and migration notes must be authored Text DATs without inputs or external file links. Invalid reserved sources block normal memory use and require owner inspection in TouchDesigner. Network fingerprints skip the text of wired, linked or computed DATs so resuming a task cannot cook those outputs.
