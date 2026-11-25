# Phase 1A component: independent security and implementation review record (review 5)

**Date:** 2026-11-22
**Reviewer:** a separate AI agent instance. It was given the owner's Phase 1A requirements, the code, the build and in-TD results, and the docs, but not the author's conclusions. It worked read-only and launched neither TouchDesigner nor a browser.
**The reviewer's own checks:** 138 unit tests OK (1 skipped). `build.plan()` digest = the built `.tox`'s recorded digest. In-TD results 35/35 + 59/59; real-browser run 12/12 + 5/5.

**Result:** **no High finding**, 3 Medium, 13 Low. All are dispositioned below. Fixes are covered by unit tests and the re-run in-TD suite (§ evidence in the [Phase 1A record](2026-11-22-phase-1a-component.md#5-final-evidence)).

## Medium

| # | Finding | Disposition |
|---|---|---|
| M1 | No time limit on a running evidence check. A hung `collect` (DNS, OS table) left the listener in `Starting` forever (503 only), or silently stopped re-verification while serving | **Fixed.** Every evidence run is timed; after 5 s it counts as `ambiguous (evidence_timeout)` and closes the listener at once, both while starting and while serving. Unit tests with a never-finishing stub |
| M2 | Unverified: all dispatch depends on the Execute DAT's `onFrameStart`. With the timeline paused, requests would get 504 and the watchdog would close the listener | **Confirmed in TD, then fixed.** The in-TD run froze when the root timeline was paused. Spike S7 showed that a COMP with its own **independent** `local/time` keeps its frame callbacks running: 141 frames during a 3 s pause versus 1 for a COMP on the root timeline. The builder now gives TDflowMCP an independent, playing local timeline. The in-TD suite pauses the project timeline and checks the Setup API still answers 200 |
| M3 | Test over-claims and gaps: `exit.no_listener_after_exit` cannot prove `onExit` ran. Not covered: re-init, two components, the watchdog, idle stop, re-verify failure, stop during `Starting`, a paused timeline. `no_off_main_dispatch` was misnamed | **Fixed.** The in-TD suite now covers: `onExit` handler → listener closed; extension re-init while serving; a second component refused; idle stop; an injected verification failure (with a redaction scan); stop in the same frame as open; and the paused timeline. The thread test is renamed `dispatch_on_main_thread_only`. The watchdog stays unit-tested: in TD, deletion is handled by `onDestroyTD` in 0.05 s, before the watchdog can act |

## Low

| # | Finding | Disposition |
|---|---|---|
| L1 | A Port change while `Stopping` for another reason was kept but not applied; diagnostics checked the old port | **Fixed.** The pending port is applied on every close outcome and before diagnostics on Open Setup. Unit test |
| L2 | `/mcp` gave 405/415/403 rather than `MCP_DISABLED` for non-standard requests | **Fixed.** With MCP disabled, every `/mcp` request returns `503 MCP_DISABLED` once Host, path and `VERIFYING` have been checked; a bad Host still gets 403 first. Unit and in-TD tests |
| L3 | On a failure, `events`, `Diagnostics()` and `gate.last` held LAN addresses and PIDs | **Fixed.** `bindverify.redacted()` keeps reason categories only. It is used for logged events and for the evidence passed into checks. An in-TD scan on the injected-failure path checks for the LAN address |
| L4 | Checks went stale (e.g. `net.bind` "unknown" while serving; the failure reason was missing in Error) | **Fixed.** Network checks re-run on each state change, using cached module and instance facts. Redacted bind evidence is shown in Setup and after `BIND_UNVERIFIED` |
| L5 | After re-init or destroy, or with two components in one process, the error was the misleading "in use by another process" | **Fixed.** A process registry of closing transports and live instances gives "previous listener still closing" or "another TDflowMCP component in this project". In-TD test for the second component |
| L6 | `CopyTellAgent` overwrote the error guidance | **Fixed.** A separate transient note is appended to `Statusdetail` for 15 s |
| L7 | Any request, including rejected ones, refreshed the idle timer | **Fixed.** Only served (< 400) requests count. An open Setup tab (2 s poll) deliberately keeps the listener alive; documented |
| L8 | The exclusive-bind requirement was not enforced if `SO_EXCLUSIVEADDRUSE` were missing | **Fixed.** On Windows the bind fails instead of binding non-exclusively |
| L9 | The loader trusted `source_digest`; lazy imports were a hazard | **Fixed.** The build id is recomputed from the verified module hashes, and the extension's function-level core import was removed |
| L10 | Close confirmation counted frames only (≈ 0.1 s at high FPS) | **Fixed.** It escalates to `STUCK` only after both ≥ 10 checks **and** ≥ 3 s. Unit test |
| L11 | Project-wide `findChildren` and first imports on every diagnostics run | **Mitigated.** Cached; repeated only on an explicit Open Setup or Run Diagnostics |
| L12 | `rmtree` on paths the operator supplies | **Fixed.** The tools refuse paths outside `<repo>/.tmp/…` and `<repo>/build` |
| L13 | (Reported as "Correct and worth keeping" items) | Kept |

## Keep (reviewer)
- Owner-thread guards; TD-free background work; static snapshot as bytes.
- Literal `127.0.0.1` with an exclusive bind; PID-filtered OS table; probes can only fail the verdict; `STUCK` is terminal.
- Gate ordering; the custom header with no preflights; deny-by-default mutations; no exception text in errors.
- Bounded queue, the unauthenticated-job quota, cancel-before-dispatch.
- `textContent`-only page with external assets compatible with the CSP.
- Allowlisted agent text; `Datadir` unexpanded; `/.well-known` = product and version only.
- Disposable builder project, hash-verified embedding, harness process isolation.
