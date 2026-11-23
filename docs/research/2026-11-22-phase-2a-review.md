# Independent review 9: Phase 2A increment 1, project memory (2026-11-22)

**Reviewer:** a separate read-only agent, given the code, the security model, the continuity design and ADR-0012/0013, but not the author's conclusions.

**Scope:** the uncommitted Phase 2A increment:
- `memory.py`;
- the engine memory path;
- the TD adapter's memory methods;
- the Create/Claim pulses;
- the 4 tools;
- store queries;
- Tell Your Coding Agent;
- Skill 1.1;
- the docs.

The reviewer ran the unit suite (253 tests, 2 skipped).

**Result:** no High-severity finding. No raw-tool path reaches the records (H4 for every action, H5 for ancestors, H7 for tags). Writes keep the intent → main-thread write → outcome order. 6 Medium/Low-Medium and 7 Low findings; all addressed.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | Medium | The first memory call of a session had no earlier session event, so the evidence window covered the whole audit history. Older sessions' changes then verified or contradicted claims | **Fixed:** with no earlier event in the session, the window starts now. Unit test `test_first_call_of_a_session_does_not_cover_older_history` |
| F2 | Medium | Claim evidence paths (sub-paths of `changed`) were returned unfiltered on resume | **Fixed:** evidence is filtered to readable paths (`evidence_withheld`). Unit test |
| F3 | Medium | Tell Your Coding Agent listed agent-chosen task ids. They were also shown for scope-hidden tasks and for unclaimed records | **Fixed:** the Resume section is fixed text plus two counts. Task ids come only from `tdflow_task_list`, scope-filtered and ledger-checked |
| F4 | Medium | While memory needed a claim, foreign records were served as if authored. The unkeyed seal was presented as meaningful | **Fixed:** every presented record carries its `ledger` status (`accepted`/`changed_outside`/`foreign`), and `edited_outside_tdflowmcp` comes from the ledger. The docs and code state that the seal is integrity-only |
| F5 | Medium | The fingerprint walk was unbounded before its cap (`findChildren` on a large root) | **Fixed:** a bounded breadth-first walk (≤ 2000 operators) |
| F5b | Medium | Every write parsed all records on the main thread, and the Tell refresh parsed them again | **Fixed:** the write compares names and text hashes without parsing, and parsed records are cached by hash. Measured 300-operator frame max: 28 → 19 ms |
| F6 | Low-Med | The evidence window was silently capped at 5000 events, and the meaning of `unchanged` was overstated | **Fixed:** `window_complete`; audit claims become `unverified` when the window was not read whole. `unchanged` is documented and worded as "not changed by you through TDflowMCP" |
| F7 | Low | Read-only reasons named records of hidden tasks. M4 versus M6 revealed whether a hidden task id exists | **Fixed:** names are shown only for visible tasks (counts for the rest). Resuming a hidden task answers M6 like a missing one; writing to one is still refused (M4) |
| F8 | Low | A hand-edited body with the wrong shape (e.g. `facts: []`) could break every call after a claim | **Fixed:** body shape is validated, and such records are `invalid` (they cannot be claimed until fixed). `based_on_rev` must not be a boolean |
| F9 | Low | The Tell "read-only" line could never appear | **Removed** (Tell has no ledger access on the main thread) |
| F10 | Low | The oversize error was misleading, and evidence was large | **Fixed:** the error reports the size; evidence is capped at 5 per claim |
| F11 | Low | Revocation mid-call fell back to the stale agent, and grants were not re-checked before the write | **Fixed:** plan denies a revoked token, and the write step re-checks read access to the scopes with the current grants. Unit test |
| F12 | Low | `"007" == "7"`; `/` never matched sub-paths; possible false positives in out-of-band detection | **Fixed:** strings compare exactly, and root matching is fixed. `fingerprint.reliable` is false when capped or incomplete. The input-source false positive is documented |

**Checked and sound (reviewer):**
- Agents cannot create tagged COMPs, set `Projectid` or load an external `.tox` (only DoS through ambiguity remains, which is refused).
- The ledger survives claim/write interleavings, an unrecorded outcome (fails closed) and `MainUnknown`.
- The digest check plus `c.op(name)` prevents double writes of a revision.
- The kill switch and token are re-checked on the main thread inside the write.
- No store call runs on the main thread.
- Live claims on unreadable paths reveal nothing.
- The fingerprint map is filtered twice and never returned.
- `top` is read before the fingerprint.
- Token scrubbing covers all agent text.

**Added after the review, from dogfooding:**
- an `input` claim kind (the first agent could not express wiring as a checkable claim);
- an unreadable source is always `unverified`, so a claim cannot confirm hidden wiring.
