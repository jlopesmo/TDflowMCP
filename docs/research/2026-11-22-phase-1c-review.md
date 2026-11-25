# Independent review 7: audit worker, project-local storage, Phase 1C onboarding (2026-11-22)

**Reviewer:** a separate read-only agent, given the code and the security model rather than conclusions. **Scope:** the uncommitted audit-worker, storage, Skills, connection-instruction and protocol changes on `dev` after `a4660cd`. The reviewer ran the unit suite (225 tests, 2 skipped) and wrote one repro script outside the repository. **Result:** 11 findings (0 critical, 1 High/Medium, 6 Medium, 4 Low). All are fixed, each with a regression test where one is practical.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | High/Medium | Revocation needed a second I/O call (reload) before the in-memory token snapshot dropped the token. A timeout there reported failure while the token kept working | **Fixed:** `Identity.revoke` removes the token from the snapshot *before* the durable write and keeps it out if the reload fails (fail toward denial). Test `test_revoke_denies_even_if_reload_fails` |
| 2 | Medium | Requests already in flight still executed after Enable MCP was turned off. A store reset (Data Location) could orphan their outcomes | **Fixed:** every main-thread step refuses to run (`MainNotRun`, nothing executed) unless MCP is still on and the engine is the one the call started with. Store resets wait until no MCP request is in flight |
| 3 | Medium | An engine rebuild (Project Root change) cleared the sticky audit failure. Calls in flight used the old policy. The root was not re-checked | **Fixed:** the audit-failure latch lives on the extension and survives rebuilds; it is cleared only by the reopen-and-verify resume path. A replaced engine refuses old calls (see 2). A root change while MCP is on re-checks that the root exists and is a COMP, else MCP turns off |
| 4 | Medium | `tdflow_undo` did not re-check revocation (or policy) right before executing | **Fixed:** the undo main step re-checks the token, agent and policy, like every other execute step |
| 5 | Medium | Skills uninstall did not check manifest paths for symlinks/junctions before deleting | **Fixed:** previews check every manifest path for links. Test `test_uninstall_refuses_linked_skill_dir` (real junction) |
| 6 | Medium | The atomic write used a fixed temp name, which a planted link could redirect | **Fixed:** random temp name, created with `O_CREAT|O_EXCL` (+`O_NOFOLLOW` where available); the destination is re-checked for links. Test `test_planted_temp_name_is_not_followed` |
| 7 | Medium/Low | A failed DROP/VACUUM during migration could leave token digests in the project folder | **Fixed:** the copy is built, stripped and vacuumed in private storage, then moved into place |
| 8 | Low/Medium | After a torn final checkpoint line, the next checkpoint glued onto it and verification failed forever (false tamper alarm) | **Fixed:** a newline-less final fragment is truncated before appending (it holds no complete MAC). Test `test_torn_tail_then_new_checkpoint_still_verifies` |
| 9 | Low | Migration accepted a source with missing checkpoints. Adoption bound the project id before verifying | **Fixed:** migration requires full verification (chain and keyed checkpoints). Adoption verifies read-only with this project's private key and requires a matching checkpoint; it binds only afterwards |
| 10 | Low | A `.toe` named `...1.toe` produced the stem `..`, so the database escaped `.tdflowmcp/` | **Fixed:** stems that are empty, dots only, or contain separators become `project`. Test `test_toe_name_cannot_escape_tdflowmcp_folder` |
| 11 | Low | Smaller gaps: a Skills success reported without a durable outcome; an orphan credential when `actions` was invalid; a stale project folder after Save As; a copy misread as a move when only an incremental sibling remained; DAT snapshots not anchored in the chain; late worker submissions never resolved; `-MaskInput` needs PowerShell 7.1 | **Fixed:** Skills report `OUTCOME_UNRECORDED` instead of success, and failures are recorded. Actions are validated before any credential exists, and a half-configured agent is disabled. The project folder is refreshed every frame. Incremental siblings count as "original still exists" (copy). The outcome records the snapshot's `before_sha`, and undo restores only a snapshot whose text hash matches both the row and the chain. The worker fails anything that slipped in after closing. The token recipe uses `Read-Host -AsSecureString` (verified on Windows PowerShell 5.1 and PowerShell 7.7) |

**Checked and sound (reviewer):**
- No TD access off the main thread.
- The main thread never waits on disk.
- Durable intent before execute and durable outcome before success; "not run" versus "may have run" is handled correctly.
- The kill switch is re-checked at dispatch, at execute and at undo.
- A failed store is never restarted automatically.
- Event order and chain integrity hold.
- Identity and copy handling are correct.
- Skills preview/confirm digests and registry-only targets work.
- The tell-agent text contains no secrets.
- The caching hints are correct.
