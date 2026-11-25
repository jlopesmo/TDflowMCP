# Independent review 11: Phase 2A increment 3 (2026-11-23)

A separate read-only agent reviewed the uncommitted [increment 3](2026-11-23-phase-2a-increment-3.md) change (repository-mode memory, the `memory:repo:write` capability, the friction fixes). It could read, search and run the unit tests; it did not launch TouchDesigner. It was asked to attack the change as an agent with limited scopes, as the author of a hostile linked folder, and as a web page on another origin. **No High finding; 5 Medium; the rest Low or notes.** The minimized-window handling ([S9](2026-11-23-s9-minimized-window.md)) was written after the review started and has its own review 12.

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Medium | A junction or symbolic link on an *intermediate* component of the link (for example `sub` in `sub/dir`) was not detected, so files could be written outside the project's tree | **Fixed.** The real path of the linked folder must equal the real path of the `.toe` folder plus the link; otherwise `SYMLINK_REJECTED`. Unit test with a junction |
| 2 | Medium | Text from record files the audit log never accepted reached server-labelled fields: `author` in the task list, crafted operator paths in `fingerprint.removed`/`unexplained`, crafted "parameter names" | **Fixed.** `author`, `created_at`, fingerprint paths, digests and parameter names are validated strictly when a record is read (otherwise the record is `invalid`). Resume returns no comparison when the task's latest record is not `accepted`. Each task-list row carries its `ledger` status |
| 3 | Medium | One crafted file (a lone surrogate escape, or 60,000 nested brackets) made every memory tool fail with HTTP 500, and Claim could not fix it | **Fixed.** Reading a record can no longer raise; such a file is reported `invalid` by name. Text that cannot be encoded is replaced before hashing, redaction and storing |
| 4 | Medium/Low | A write to a task the caller cannot read disclosed the task's scope path in the denial | **Fixed.** When the scopes come from the task's record, the answer is the plain `M4`/`M6` denial with no path, in both modes |
| 5 | Medium/Low | In repository mode two concurrent requests could both pass the record-set check and write two records with the same revision | **Fixed.** The check, the creation and the read-back run under one lock, and the link is checked again inside it. Unit test with two threads |
| 6 | Low | A builder token narrowed to `read` could still get the write capability | **Fixed.** Refused at token creation (`CAPABILITY_NOT_ALLOWED`); in-TD check |
| 7 | Low | The per-parameter digests could push a record over the 64,000-character limit | **Fixed.** They only take the room the rest of the record leaves |
| 8 | Medium | A migration not followed by a project save could leave states the Setup page could not leave: a stale link, a missing `tdflow-project.json`, newer files in the folder, a record written during the switch | **Fixed.** New previewed, audited **re-link** action; records already in the folder are left in place and face the ledger; migrations hold the write lock, and a write re-checks the link inside it. In-TD check for the lost link |
| 9 | Medium/Low | The `memory_migrate` audit trail was not always closed (a disk error after `started`; a failed final append; the folder not marked) and did not name the folder | **Fixed.** Every failure after `started` appends `error` or `unknown`; a failed final append answers `OUTCOME_UNRECORDED`; the events carry the relative folder and `folder_marked` |
| 10 | Low | The ledger accepted the hash of an announced record whose write had definitely failed | **Fixed.** `error` and `aborted` outcomes remove it; an intent with no outcome, or an `unknown` one, keeps it |
| 11 | Low | `memory_to_repo` destroyed the record DATs before switching the parameters; `ValueError` was used both for "refused" and "failed part-way" | **Fixed.** The link is set first and the DATs go last; a part-way failure is a different exception and is reported as unknown |
| 12 | Low | The `.git`/`.tdflowmcp` refusal was by name only (an 8.3 short name passed) | **Fixed** by the real-path check of finding 1 |
| 13 | Low | Unsalted per-parameter digests in record files could confirm a low-entropy value offline; in repository mode every record is readable by anything with file access | **Fixed / documented.** The digests are keyed with the installation key (another key means "unknown", never a wrong answer). The migration preview now says that anyone who can read the files can read every task record |
| 14 | Low | The "no absolute path" tests could not fail on Windows | **Fixed.** They compare the unique folder name |
| 15 | Low | Test gaps: revocation between the check and the file write, an unconfirmed check step, the per-operator filter | **Added** for the first two and for findings 1–5, 7, 8, 10, 13. Not added: `OUTCOME_UNRECORDED` after a file write, and each `_migrate_failed` branch inside TouchDesigner |
| 16 | Note | The preview did not list the other files written; the Setup page re-read all records every 2 s; auto-placement revealed the position of an unreadable sibling; the migration held the admin lock across disk I/O | **Fixed:** `also_written` in every preview; the memory status refreshes every 10 s; placement uses readable siblings only; migrations use the write lock, not the admin lock |
| S1 | Suspected | An unsaved project may still report a folder | **Fixed defensively:** "saved" now means the `.toe` file exists. Not reproduced in TouchDesigner |
| S2 | Suspected | A link swapped in between the check and the write | **Fixed.** After the write the file's real path must be inside the folder, else it is removed and the write fails. Unit test |
| S3 | Suspected | Cloud-files placeholders counted as links | **Fixed.** Only junctions and symbolic links count. Unit-tested with injected attributes, not on a real cloud folder |
| S4 | Note | A migration creates or removes up to 2,000 DATs in one main-thread step | **Recorded** as a limit in the [increment record](2026-11-23-phase-2a-increment-3.md#limits) |

## Checked by the reviewer and found sound
- Path text handling (absolute paths, drive letters, UNC, alternate data streams, up-then-down, trailing dots or spaces).
- "Never overwrite a record" on Windows and POSIX.
- No path lets a token without the capability, a revoked token or a suspended state create a file; nothing new makes the main thread wait on the disk.
- Neither migration direction can launder a record; the `Projectid` link check runs on every read, write, claim and migration.
- The capability can only be issued through the paired admin route and is re-checked before the write; the three new routes need the session, exact Origin and CSRF.
- H4 on the `tdflowProject` component is untouched.

## Left open
- Each `_migrate_failed` branch is exercised only by reading, not by a test.
- The 8.3 short-name and cloud-placeholder cases were checked by unit tests with injected values only.
- The transport change for a minimized window was reviewed separately ([review 12](2026-11-23-s9-minimized-window.md#independent-review-12)).
