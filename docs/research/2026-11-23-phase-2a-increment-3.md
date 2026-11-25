# Phase 2A increment 3: repository-mode memory, link check, migration; dogfood friction (2026-11-23)

**Owner direction D19 (2026-11-23):** continue in dependency order, starting with the remaining Phase 2A requirements. This increment closes the two open [ADR-0013](../decisions/0013-project-memory-model.md) gates (a mismatched `Projectid` blocks linking; a migration in both directions preserves records and hashes) and the three friction items recorded by the [2A dogfood pass](2026-11-22-phase-2a-memory.md#dogfood-findings-friction-recorded).

## Delivered

### Repository mode
- **Where records live.** With `Memoryauthority` = `repo`, the task records are files: `<folder>/tdflow/memory/<record_id>.json`, one record per file, with `<folder>/tdflow-project.json` naming the memory's `Projectid`. The `tdflowProject` component then holds only the link (`Repopath`), the `Projectid` and a `derived_index` DAT with counts. Design and rules: [continuity §8](../architecture/continuity-and-memory.md#8-phase-2a-increment-3-repository-mode-2026-11-23).
- **The linked folder stays inside the project's own tree.** `Repopath` is always relative to the `.toe` folder: that folder, a folder below it, or up to three levels straight above it (a repository that contains the project). Absolute paths, drive letters and sibling folders are refused. The link therefore never holds a user-profile path and moves with the project.
- **Link check (ADR-0013 gate).** Every read, write, claim and migration first requires `tdflow-project.json` to name this project's memory `Projectid` with authority `repo`. Another id answers `MEMORY_LINK_MISMATCH`, and Claim Project Memory cannot override it.
- **The audit log is still the ledger.** A file the audit log never announced is `foreign`; an edited one is `changed_outside`. A Git checkout or pull that changes records therefore makes memory read-only until the user's Claim Project Memory.
- **No disk I/O on TouchDesigner's main thread.** The engine reads and writes the files on its HTTP handler thread. The main thread only confirms that the component is unchanged. Tell Your Coding Agent reads its counts from the derived index.
- **Migration (ADR-0013 gate).** On the Setup page (paired admin session): preview with a digest, then confirm exactly that preview. Embedded → repository writes the files, verifies them by hash, then switches the component. Repository → embedded recreates the record DATs and marks the folder as a derived export that TDflowMCP no longer reads. Both are audited before and after (`memory_migrate`), and noted in both homes. Record text and hashes are unchanged, so the ledger stays valid without a claim.

### Dedicated permission for repository-backed records (owner condition D18)
- A new **capability**, `memory:repo:write`, is issued only by the user when creating a token (builder role only). It is never implied by a role or a scope, is stored with the credentials in private storage, and is re-read right before a file is written.
- In repository mode an agent without it can list and resume tasks, but a write is refused with rule `M8`. Embedded mode is unchanged (role and operator scopes).
- No new agent-writable record type was added. Agents still cannot write decisions or project ICM.

### Dogfood friction
- **Default placement.** `tdflow_create_operator` without `x` and `y` places the operator to the right of the rightmost node and returns its position. An explicit position still wins.
- **Handoff status.** `tdflow_handoff_create` accepts an optional `status`; a task's status is that of the newest record that states one. It stays labelled `agent_reported`.
- **Which parameter changed.** Records now hold a short digest per parameter that is away from its default (size-budgeted). `tdflow_handoff_resume` names the parameters that differ for each changed operator (`fingerprint.parameter_changes`), for readable operators only, names only. Older records answer `known: false`.

Skill **1.2** describes all of this. The tool surface stays `1` (additive).

## Evidence (TouchDesigner 2025.32440)

| Check | Result |
|---|---|
| Unit tests | See the [validation index](../icm/validation-index.md) |
| In-TD memory suite, new launch `memory_repo` | See the [validation index](../icm/validation-index.md). ADR-0013 gates in capitals in the results file: `MISMATCHED_PROJECTID_BLOCKS_LINKING`, `MISMATCH_BLOCKS_READS`, `MISMATCH_BLOCKS_WRITES`, `MISMATCH_NOT_CLAIMABLE`, `MIGRATION_TO_REPO_PRESERVES_RECORDS_AND_HASHES`, `MIGRATION_BACK_PRESERVES_RECORDS_AND_HASHES`; D18: `WRITE_WITHOUT_DEDICATED_PERMISSION_DENIED`; ledger: `OUTSIDE_FILE_EDIT_MAKES_MEMORY_READ_ONLY`; friction: `RESUME_NAMES_THE_CHANGED_PARAMETER` |

## Independent review 11
[Review 11](2026-11-23-phase-2a-increment-3-review.md): no High finding, 5 Medium, the rest Low or notes; all addressed except two test gaps that are listed there. The fixes changed behaviour in these ways: links are checked on the real path; record files are validated strictly and can never crash the tools; resume compares nothing against a record the audit log does not accept; hidden-task scopes are not named in denials; repository writes are serialised; a **re-link** action repairs a lost folder link; the migration audit trail is always closed; per-parameter digests are keyed.

## Limits
- **Repository-mode calls are slower:** each file operation from a request thread waits for TouchDesigner's main thread to yield. Measured in TD: see the [validation index](../icm/validation-index.md).
- A migration creates or removes all record DATs in one main-thread step. With many records this is a visible frame hitch; it is a rare, user-started action. Not measured beyond the 4 records of the test.
- The linked folder is not checked for being a network or cloud-synced location (the records are small text files, unlike the SQLite audit database).
- Repository mode was verified with a folder inside the project folder. A parent-folder link (`..`) is unit-tested, not exercised in TouchDesigner.
- TDflowMCP writes files and never runs Git. Committing the records is the user's action.
- Not done: tasks, decisions and ICM record types (`tdflow_memory_get/put`) and record archiving. Adding any agent-writable record type reopens owner condition D18.
