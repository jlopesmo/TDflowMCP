# Phase 1B secure MCP core: independent security review record (review 6)

**Date:** 2026-11-22
**Reviewer:** a separate AI agent instance. It was given the owner's Phase 1B requirements, the code, the tests and the results of the passing in-TD run (41/41 + 99/99), but not the author's conclusions. It worked read-only and launched neither TouchDesigner nor a browser.
**The reviewer's own checks:** 198 unit tests OK (2 skipped).

**Result:** 3 High, 8 Medium, 9 Low. **All High and Medium findings are fixed**, with new unit tests and new checks in the in-TD acceptance suite. The residual risks below are accepted and documented. The acceptance suite was re-run on the rebuilt `.tox` (see the [Phase 1B record](2026-11-22-phase-1b-mcp-core.md)).

## High

| # | Finding | Disposition |
|---|---|---|
| H1 | **Python through data flow without `execute`.** Two routes: a Table DAT holding Python, wired into an Evaluate DAT (which is not on the executable list); or deleting a user's callbacks DAT and recreating that name, fed by agent data | **Fixed:**<br>• **Creation is an allowlist** (`policy.SAFE_CREATE_TYPES`): plain TOP/CHOP/SOP/MAT/COMP types plus `tableDAT`, `textDAT` and `nullDAT`. Anything else is `G2`.<br>• **Wiring into any DAT** (or into an executable type) also needs `execute`.<br>• **DAT text is writable only when the user tagged the DAT `tdflowdata`**; Table DATs no longer count as data by default.<br>• In-TD tests cover Evaluate and File In creation, a wire into a DAT, and an untagged table |
| H2 | **Reads outside scope through reference and file parameters.** Examples: a File In DAT pointed at the audit key, or Info/Examine/OP Find DATs pointed at the TDflowMCP component | **Fixed:**<br>• Parameters of reference styles (OP/COMP/TOP/CHOP/SOP/DAT/MAT/POP/Object/PanelCOMP), `File`/`Folder`/`FileSave` and `Python` are classified as `execute`, which is denied in Phase 1. `file`, `folder` and `plugin*` are also on the denylist.<br>• File-reading and introspection operator types cannot be created (H1 allowlist).<br>• In-TD test: setting a Select TOP's `top` to the component is denied |
| H3 | `EXECUTABLE_TYPES` was incomplete (Touch In/Out, OSC/pipe/sync, C++ plugins, Web Render, WebRTC, file writers…) | **Fixed by the H1 allowlist.** A unit test confirms a list of executable, network, file and introspection types is refused, including unknown names |

## Medium

| # | Finding | Disposition |
|---|---|---|
| M1 | The kill switch relied on a parameter callback that TouchDesigner defers (`kill_param_callback_synchronous: false`); the test had called the handler directly | **Fixed.** `OnFrame` copies `Suspendagents` into the transport **before** dispatching anything in that frame. The in-TD test now flips only the parameter, while a request waits in the queue; the request is rejected and leaves no `intent` event |
| M2 | The protection registry went stale after renames, moves or new instances | **Fixed.** Protection is recomputed at most once per frame, at every resolve |
| M3 | Ancestor rule H5 did not cover cloning or shortcuts | **Fixed.** `clone*`, `enablecloning*`, `cloneimmune`, `opshortcut`, `parentshortcut`, `iop*`, `relpath` and `subcompname` are denied everywhere. In-TD test for `clone` |
| M4 | Undo could revert someone else's newest change if TD recorded nothing, or if the stack was unreadable | **Fixed:**<br>• A change is recorded as reversible only if **exactly one new undo entry appeared on top** during the call.<br>• An unreadable or unrecorded stack means `UNDO_CONFLICT`.<br>• `reversible` in results comes from what was actually recorded.<br>• A restoration that cannot be verified returns `UNDO_UNVERIFIED` as an error, never as success |
| M5 | Logout only cleared the cookie; old sessions stayed valid | **Fixed.** Logout ends the server-side session and is audited. A new pairing approval ends any older admin session (one session at a time). In-TD test replays the old cookie and gets 401 |
| M6 | Cookies are not port-isolated, so admin reads were possible with the cookie alone | **Fixed.** Admin reads (`/setup/api/agents`, `/setup/api/sessions`) also require the in-page CSRF token. In-TD test |
| M7 | A local process could replace a pending pairing request while the user compares codes | **Fixed.** A pending request is never replaced: `409 PAIRING_PENDING` until it is rejected in TD or expires (2 min). **Residual:** a local process can keep pairing busy (denial of service); the user sees a code that does not match and rejects it |
| M8 | Checkpoints were written only on clean shutdown, and deleting the checkpoints file went unnoticed | **Fixed:**<br>• A keyed checkpoint is written every 50 events, at every Enable MCP and at shutdown.<br>• `verify` reports `unverified_tail`, and flags `checkpoints_missing` when the file has lost a checkpoint recorded in the database.<br>• **Residual:** the key sits next to the database, so anyone with write access to `Datadir` can rewrite everything consistently. The claim stays "tamper-evident relative to the last exported checkpoint"; anchoring outside `Datadir` (e.g. Windows DPAPI) is deferred |

## Low

| # | Finding | Disposition |
|---|---|---|
| L1 | Session notes were stored unredacted | **Fixed** (`redact()`; unit test) |
| L2 | Resolution errors could describe the inside of protected components | **Fixed.** Protection facts are checked before any resolution error is returned. In-TD test |
| L3 | Policy used the caller's parameter name | **Fixed.** The real `par.name` must equal the requested name |
| L4 | `re.search` with `$` accepted a trailing newline | **Fixed** (`re.fullmatch`); a trailing `/` is also hard-denied (H0) |
| L5 | `filtered` counts reveal how many operators are hidden | **Accepted.** The spec (security model §3.2) reports `filtered: n`, and the count reveals no names |
| L6 | A malformed checkpoint line raised an error; the full verify ran on every admin read | **Fixed.** Defensive parsing; verify is cached for 10 s |
| L7 | A mutation stood when its outcome could not be written | **Fixed.** All agents are suspended automatically, the user is told, and the result carries `audit_warning` |
| L8 | Writing a bind master can change bound parameters outside the scope | **Residual, documented.** Binds and exports are not followed in Phase 1; to be revisited with scope leases (Phase 4) |
| L9 | Values of OP parameters, expression text and error messages can mention paths outside the scope | **Residual, documented.** Reads of an in-scope operator show its own data |

## Keep (reviewer)
- **Policy:** fixed evaluation order; all-or-nothing mutations; execute always ends in `APPROVAL_UNAVAILABLE`; an exact `op(path).path == path` check.
- **Audit:** a durable `intent` before every execution (fail closed, including reads and identity tools); an append-only hash chain with triggers; keyed `args_hmac`.
- **Tokens:** salted hashes, constant-time compare, an immutable snapshot for handler threads, revocation re-checked at dispatch.
- **Transport:** exclusive `127.0.0.1` bind, authentication before the body is read, `Authorization`/`Cookie` never forwarded, re-checks at dispatch, no CORS headers.
- **Threading:** SQLite only on the main thread, and all dispatch on the main thread.
- **Admin:** exact Origin and CSRF, HttpOnly + SameSite=Strict cookies, pairing credentials handed out once, approval only inside TD.
- **Protocol:** `-32020`/`-32022` handling, batches rejected, legacy and modern eras.
- **DAT text:** snapshot undo with a hash check before restoring.
