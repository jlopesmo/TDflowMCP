# Independent review 8: B6 record, dogfood round 1, read-tool changes, ADR-0014 (2026-11-22)

**Reviewer:** a separate read-only agent, given the code and the security model. **Scope:** the uncommitted changes after `298988f`:
- read-tool changes in `tdops.py`/`tools.py`;
- the dogfood harness and runner;
- the spike-B6 record;
- ADR-0006, ADR-0014 and the self-development design;
- ICM accuracy.

The reviewer ran the unit suite (231 tests, 2 skipped).

**Result:** no high-severity finding in shipped code. 5 Medium and 7 Low findings, all addressed.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| M1 | Medium | The new docs said "TDflowMCP never forces a cook", but `get_parameters` evaluates expression/bind parameters with `eval()`. This behaviour predates this change; the expressions are the user's own, since agents cannot set them | **Wording fixed:** the `get_errors` note now says only that *this tool* cooks nothing. `get_parameters`'s evaluation is documented in the tool surface as a hypothesis to verify, and an `evaluate: false` option is recorded as a candidate |
| M2 | Medium | `--allowedTools` only pre-approves tools: the built-in write/shell tools were available in the dogfood sessions, and user settings or hooks could have applied | **Fixed in both harnesses** (dogfood, onboarding): `--tools Skill,ToolSearch`, `--setting-sources project` (`--restricted` also hid project Skills, checked), `--permission-mode default`, explicit `--disallowedTools`, `--no-session-persistence`, and an assertion from the init message that only TDflowMCP tools, Skill and ToolSearch exist. Earlier passes: no call to any other tool appears in their logs |
| M3 | Medium | Sessions ran with a working directory inside the repository, so the repository's Claude Code project memory, history and settings could apply, and sessions were persisted under the user's Claude Code folder | **Fixed:** the disposable project (Claude Code's working directory) is now a fresh system-temp folder outside the repository, with no session persistence. Checked: the repository's auto-memory folder was not written (it is still empty). The earlier runs left two session-history folders for the disposable project paths in the user's Claude Code data; they were left in place for the owner to decide |
| M4 | Medium | The B6 "structural floor of strict durable ordering" wording was overstated: 1B made the same durable writes on the main thread at 34–55 ms | **Fixed:** the floor is that of off-main-thread durable writes under TD's GIL; main-thread fsync is listed as the faster option excluded for frame impact (spike B6, ADR-0006, current-state) |
| M5 | Medium | ADR-0014: relaxing execute classification for "inert" DATs is a real weakening, since an outside operator could execute them; the type list was incomplete; the workspace was not a protected kind; TD facts were stated as certain | **Fixed in the proposal:** execute classification is never relaxed. Inertness is an allowlist (Text/Table DATs only, no file sync, no inputs, no COMPs) plus "no outside references", re-checked atomically on each write. The workspace is a protected kind with raw tools hard-denied. Nothing is `loadTox`-ed into A. The candidate path is user-only. The TD facts are S8 hypotheses |
| L1 | Low | The PoC was not minimal: it needed the riskiest new code. The old A cannot record a manual promotion's intent. Credentials persist across promotion | **Fixed:** the PoC has no workspace (isolated build/test + manual promotion that the new A verifies and records). The workspace moves to stage 2. Credential persistence is stated as an owner choice |
| L2 | Low | `list_operators` built inputs/positions for every child before paginating | **Fixed:** extra fields only for the requested page |
| L3 | Low | `never_cooked` was capped silently | **Fixed:** `never_cooked_total`, `never_cooked_truncated`, `complete` |
| L4 | Low | Timeouts did not line up; a timeout killed only the direct child | **Fixed:** the per-session budget (420 s) drives the launch and in-TD budgets; a timeout kills the session's process tree (own child only) |
| L5 | Low | `tdflowmcp_internals_untouched` checked too little | **Fixed:** a before/after digest of the component's operators, DAT texts and non-status parameters |
| L6 | Low | `tool_surface_version` stayed `1` after additive changes | **Documented:** additive changes do not bump it; breaking changes do (tool surface, conventions) |
| L7 | Low | Documentation accuracy: an estimated share stated as measured; mixed build hashes; duplicate list numbering; "ran on this commit" before any commit | **Fixed** in current-state, the dogfood record, spike B6 and the validation index |

**Checked and sound (reviewer):**
- The new read fields stay behind the same readable filter, so no out-of-scope or protected operator leaks through inputs, positions, cook counts or `never_cooked`.
- The `names` filter bypasses nothing.
- No new side effects.
- The token is handled correctly (environment only, transcripts redacted, git-ignored output).
- Skills go only to disposable targets.
- ADR-0014's intent (H3 unchanged, B never in A's process, owner-only promotion, single authority).
- The B6 numbers match the validation index, and the rejection is justified.
