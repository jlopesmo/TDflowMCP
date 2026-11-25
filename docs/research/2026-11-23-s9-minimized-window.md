# S9: the "unexplained hang" is a minimized TouchDesigner window (2026-11-23)

**Status:** Verified by a controlled spike and an in-TD acceptance test on TouchDesigner 2025.32440. The product now handles the situation ([what changed](#what-changed-in-the-product)).

## The earlier symptom
Recorded twice and never reproduced on purpose ([2A increment 1](2026-11-22-phase-2a-memory.md#dogfood-findings-friction-recorded), [increment 2](2026-11-23-phase-2a-increment-2.md)): during a long run, main-thread calls timed out, then the listener went away, the TouchDesigner log was empty, and the process did not exit cleanly. Increment 2 added a test-only stall watchdog to every in-TD suite to catch it.

## What happened on 2026-11-23
During the 1A suite the watchdog fired. Evidence was taken from the live process before anything was ended (kept in `.tmp/archive/2026-11-23-hang-minimized/`, git-ignored):
- The watchdog dump shows the main thread with **no Python frame**: it was stalled in TouchDesigner's own code, not in the component. One HTTP handler thread was waiting for the main-thread pump. The audit worker was idle.
- The process was alive and "responding", using about 1 % of one CPU: it was not cooking.
- Its main window was **minimized** (`IsIconic`), with no dialog, no menu and no move or resize in progress. The session was unlocked and the user was working in another application.
- The listener was gone: after 20 s without a pump the transport's orphan watchdog had closed it, as designed.
- Restoring the window (without activating it) made the in-TD test runner continue at once.

## Controlled measurement (spike S9)
[`spikes/s9`](../../spikes/s9/README.md) minimizes and restores its own disposable TouchDesigner window and counts frames over 4-second windows:

| Window state | Root-timeline frames | Independent-time frames | Python thread ticks |
|---|---|---|---|
| Visible | 227 | 227 | 46 |
| Bottom of the z-order | 237 | 237 | 40 |
| **Minimized** | **0** | **0** | 25 |
| Restored | 239 | 238 | 40 |

**TouchDesigner 2025.32440 runs no frames while its window is minimized.** A component with its own independent Time COMP (the S7 remedy for a paused timeline) stops as well. Python threads keep running.

Not established: whether a window that is fully covered by other windows keeps running. Suites have passed while the user worked in other applications, which is consistent with it, but the spike's "bottom of the z-order" row was measured while the test window was still in the foreground.

## Why it matters
Minimizing TouchDesigner while a coding agent works is an ordinary thing to do. Before this change the agent then saw 5-second timeouts, and after 20 s TDflowMCP closed its listener and turned MCP off, so the user had to open Setup and enable MCP again. Nothing unsafe happened (every path failed closed), but it looked like a crash.

## What changed in the product
- **Fail fast, with the reason.** Once the main thread has not pumped for 2 s, a request that needs it is refused at once with `TDFLOW_NOT_COOKING` instead of waiting for a timeout. The request is never queued, so it can never run later. An MCP tool call gets a normal tool error: "TouchDesigner is not running frames right now, so nothing was executed. Its window may be minimized (ask the user to restore it), or it is busy with a long operation. Then retry." The refusal is audited as a denial. `tools/list`, the identity tools and the static Setup page need no frames and keep answering.
- **The listener survives a minimized window.** The orphan watchdog asks the operating system whether this process's own TouchDesigner window is minimized (`src/tdflowmcp/net/oswindow.py`, no `td`, Windows only), and only for an instance that is still the registered, live one. Only a definite "yes" keeps the listener. "No", "unknown", an error, a shut-down or replaced instance, or any other platform closes it after 20 s exactly as before. The pause is sampled from the first second without a pump, so after a restore the owner always gets one more 20 s to pump again.
- **MCP stays enabled.** When the window is restored, calls work again without Open Setup or Enable MCP.
- **Users and agents are told.** Tell Your Coding Agent, Skill 1.2, the Setup page and the install guide say that the window may be behind other windows but must not be minimized.

Unchanged: nothing executes without frames; the kill switch, token checks and audit rules are the same; a lost owner still closes the listener.

## What changed in the test harness
`tools/tdrun.py` restores its own test window, without activating it, whenever it is minimized (a daemon thread, twice a second), and counts the restores. The deliberate minimize test switches this off for its duration. Other processes' windows are never touched.

## Evidence
See the [validation index](../icm/validation-index.md): unit tests `tests/unit/test_not_cooking.py`, and the 1B in-TD checks named `minimized.*` (a 26-second minimize: refused in about 0.3 s with the reason; listener still open after the watchdog period; nothing executed; working again after the restore with MCP still enabled).

## Independent review 12
A separate read-only agent reviewed the transport change, the window query, the engine messages and the harness change (it ran the two unit files; it did not launch TouchDesigner). **It found no path on which anything executes that would not have executed before.** Findings and dispositions:

| # | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | Medium | The pump time was stamped only when a pump started. After a main-thread step longer than 2 s, the next step of the same call (plan → execute, or the fingerprint chunks) was refused as "not cooking" although TouchDesigner was cooking | **Fixed.** The stamp is renewed before each finished job wakes its handler thread, and when the pump returns. Unit test with a two-step call |
| F2 | Low | The pause was first sampled only after the 20 s heartbeat, so a pause ending just before that mark earned no grace | **Fixed.** Sampled from the first second without a pump, at most once a second. Unit test |
| F3 | Low | An instance that was shut down or replaced kept its listener for as long as the window stayed minimized | **Fixed.** Only the registered, live instance may claim the pause. A component deleted without any notification is still not covered (see limits) |
| F4 | Low, owner decision | While minimized, the periodic bind re-verification and the 30-minute idle stop do not run, and the listener now outlives them | **Recorded** as a limit below and in the [security model](../architecture/security-model.md#83-embedded-transport-threading-and-limits). Not changed |
| F5 | Medium, harness only | The new window tender in `tools/tdrun.py` would have restored the window that the S9 spike minimizes, so the spike could no longer reproduce its result | **Fixed.** The spike switches the tender off; rerun recorded in the validation index |
| F6 | Low | With sustained frames slower than 2 s, more requests are refused than before; the message blamed a minimized window | **Message made neutral.** The behaviour is a recorded limit |
| F7 | Low | The window query used the shared `ctypes.windll.user32` without argument types, and an error inside the callback gave a partial list | **Fixed.** A private library handle with explicit types; any callback error makes the answer unknown |
| F8 | Low | The harness's tender and a deliberate minimize could interleave | **Fixed** with a lock |
| N1–N4 | Notes | Query rate; the pause counter counted once ever; a closing transport answered "not cooking" | **Fixed:** at most one query a second; one count per pause; closing is reported first |
| N5 | Suspected | With a second visible, non-minimized TouchDesigner window (a floating pane or perform window) the query answers "not minimized"; whether TouchDesigner then still runs frames was not measured | **Open.** The direction is fail-closed (the earlier 20 s close) |

## Limits and follow-ups
- **While minimized, safety checks driven by frames are paused:** the periodic re-verification of the loopback binding and the idle stop do not run, and the `Suspendagents` parameter cannot be flipped. Requests that need no frames are still answered (`tools/list`, the identity tools, the static Setup page, the paired admin API). Nothing that touches the project can execute. **For the owner:** accept this, or ask for a background re-verification while paused.
- A component deleted while TouchDesigner cannot notify the extension would keep its listener for as long as the window is minimized, then 20 s.
- Frames slower than 2 s (not just one long frame) make requests fail fast instead of waiting.
- Only Windows was examined. On other platforms the query answers "unknown", so the previous behaviour applies (timeouts, then the listener closes).
- While minimized, TouchDesigner parameters cannot be used, so the `Suspendagents` kill switch cannot be flipped from TouchDesigner. Nothing can execute in that state. A kill switch on the 2B dashboard must work without frames.
- Candidate, not done: an optional way to keep TouchDesigner cooking while minimized (for example a small perform window). It needs its own spike and an owner decision, because it changes what the user sees.
- The 2 s threshold also refuses requests during a single frame longer than 2 s (for example while a large file loads). That is a retryable refusal instead of a 5-second wait.
