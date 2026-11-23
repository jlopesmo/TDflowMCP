# Independent review 13: Phase 2B increment 1 (2026-11-23)

A separate read-only agent reviewed the approval flow and the dashboard ([increment record](2026-11-23-phase-2b-increment-1.md)). It ran the unit tests and wrote proof-of-concept scripts against the test fixtures; it did not launch TouchDesigner. **No High finding. Two Medium findings were demonstrated by running code.** All were addressed before the commit.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Medium (shown by a PoC) | The arguments shown to the user were not bound to the digest the decision MAC covered. A process able to write the project-local database could change the stored digest so that the user approved harmless text and a different call ran | **Fixed.** Approval rows now live in the private credentials database (per-user storage, with the tokens), never in the project folder. Before a decision the digest of the stored arguments is recomputed; a mismatch cancels the request (`APPROVAL_INVALID`). Unit test |
| 2 | Medium (PoC) | Single use relied on a state column in the project-local database; resetting it allowed a replay within the expiry | **Fixed** by the move to private storage. Used arguments are also deleted. Unit test |
| 3 | Low (PoC) | The approval bound only action and path, not the target facts: a delete approved for 60 operators still ran after the component grew to 5000 | **Fixed.** The approval binds action, path, change, parameter, operator count and operator type, compared at redemption and again at execution. Unit test |
| 4 | Medium | Bidirectional overrides and invisible characters in code could make what the user reads differ from what runs ("Trojan Source"); the page re-serialised the arguments | **Fixed.** Such arguments cannot be approved (`APPROVAL_REFUSED`); the page shows the stored text as is, with its length. Unit test |
| 5 | Low | Cards could move under the pointer as new requests arrived | **Fixed.** No re-render while the pointer or focus is in the queue; buttons name the request and digest prefix |
| 6 | Low | Complete arguments stayed in the project folder indefinitely | **Fixed** by private storage; arguments are deleted once a request is used or cancelled |
| 7 | Low | Two races between the dashboard kill switch (handler thread) and OnFrame could let a pending resume undo a later suspend | **Fixed.** A lock and a suspend generation: a resume completes only for the generation it started from |
| 8 | Low | The dashboard kill switch was unavailable while the audit store was down | **Fixed.** Suspending is routed before the store check |
| 9 | Low | Audit ordering: a request row could exist without its `requested` event; a redemption was committed before its event; a refused decision looked like a decision | **Fixed.** The row is cancelled if its event cannot be written; `redeeming` is recorded before the approval is consumed; a refused decision is recorded as `decision_refused` |
| 10 | Note | Approving an expression grants full TouchDesigner Python, including TDflowMCP itself | **Stated** on every A1 request ("approved code runs with full access to TouchDesigner, including TDflowMCP itself; scopes do not limit it") and in the [security model](../architecture/security-model.md#5-code-execution-honesty) |
| 11 | Notes | Object-URL leak; resume request not attributed; report digest unkeyed; GET `/api/*` without Origin; diagnostic log content | **Fixed:** object URLs revoked; `resume_requested` recorded. **Recorded:** the report digest identifies a file but is not a signature; GET reads are protected by the SameSite cookie and the CSRF header |

## Checked by the reviewer and found sound
- Only `tdflow_set_parameters` (A1) and `tdflow_delete_operator` (A2) can be approved; code DAT text and wires into DATs stay `APPROVAL_UNAVAILABLE`; executable operator types stay outside the creation allowlist for every role.
- `approved=True` lifts only A1 and A2; hard denies, role, grants and limits run first at plan and at execution; the policy version is compared at redemption and execution.
- Two identical concurrent re-calls cannot both run; another agent cannot redeem; `tdflow_approval_status` is scoped to the caller.
- The decision MAC uses the private installation key and a constant-time compare.
- Undo of an approved change is sound.
- Every `/api` route needs the session and CSRF token; mutations need the exact Origin; audit search is parameterised; no secrets in the views; the page uses no HTML sinks; the mandatory audit cannot be changed.

## Left open
- `_dashboard_api`, the kill-switch concurrency and the settings endpoint are tested in TouchDesigner, not by unit tests; the page has no tests of its own.
- A report's digest is not a signature.
