# Spike B6: main-thread continuation (rejected, 2026-11-22)

**Owner decision B6:** keep the SQLite audit worker with `synchronous=FULL` as the approved baseline, and run a bounded experiment with a main-thread continuation to reduce MCP latency. Acceptance: a meaningful, repeatable improvement (aim: ≥ 25 % lower median latency) without a frame-time regression or weakened guarantees. Otherwise keep the worker and document the finding.

**Result: rejected.** The median sequential latency did not change (−0.2 %). The product keeps the verified audit-worker pipeline, and the experiment is preserved here as [`continuation.patch`](continuation.patch) (against `298988f`).

## What was built (the patch)
- `src/tdflowmcp/pipeline.py`: `Continuations` (futures with callbacks, polled on the owner thread within a 4 ms budget) and `CallBox` (start/cancel race under a lock, so a cancelled call can never start).
- Engine path `_call_continued`, selected by `TDFLOW_PIPELINE=continuation`; the default stayed the baseline:
  - the plan step on the main thread *submits* the snapshot and intent (`Store.append_intent`, one worker operation);
  - `OnFrame` (after the kill-switch mirror) executes once the intent is durable, after re-checking the exec gate (MCP on, same engine), the kill switch, the token, the targets and the policy, and cancellation;
  - the main thread *submits* the outcome; the handler thread waits for it to be durable before reporting success.
- Fail-closed paths: intent overflow or failure → nothing executed; handler timeout before start → cancelled, never executed, aborted outcome recorded; started but unconfirmed → `OUTCOME_UNKNOWN` and suspend; unrecorded outcome → `OUTCOME_UNRECORDED` and suspend.
- 11 unit tests (`tests/unit/test_continuation.py` in the patch), with an owner thread standing in for TD's main thread: success and ordering, concurrent calls with a slow disk, kill switch between intent and execute, exec gate, revocation, target change, intent overflow, outcome failure, handler-timeout cancellation, start/cancel race, owner-only polling. All passed.
- `run_td_gate_tests.py --compare N`: N alternating `perf` launches per pipeline on the **same** `.tox` (`4c129e93…`).

## Measurements (TouchDesigner 2025.32440, 60 fps, identical workload)
Load: 30 sequential `set_parameters`, then 8 clients × 30 calls (writers and readers). Three alternating launch pairs; 6/6 + 150/150 checks passed.

| Median of 3 launches | Worker (baseline) | Continuation | Change |
|---|---|---|---|
| Sequential p50 | 83.8 ms | 83.6 ms | **−0.2 %** |
| Concurrent p50 | 298 ms | 282 ms | −5.6 % |
| Throughput | 25.9 calls/s | 27.3 calls/s | +5 % |
| OnFrame p99 (per launch) | 1.38–1.48 ms | 1.30–1.60 ms | no regression |
| Frame interval p95 | 16.79–16.91 ms | 16.77–16.91 ms | no regression |

The continuation ran every call through the new path (270 continuations per launch, 0 errors, 0 expired).

## Why there was no gain (root cause)
| Request | p50 |
|---|---|
| MCP `ping` (handler thread only: no audit, no main thread) | 17.0 / 17.8 ms (one frame) |
| `tools/list` | 21.1 / 17.5 ms |
| `set_parameters` | 84.2 / 83.6 ms (five frames, both pipelines) |

A thread that blocked (socket, fsync, event) resumes only in a frame-boundary window in which TouchDesigner releases the GIL (`gate_bench`: the worker's durable append is 16.7 ms, the same append on the main thread 5.1 ms). A call needs, strictly in order:
1. the request reaching a handler (1 frame);
2. the plan on the main thread;
3. the durable intent;
4. execution on the main thread;
5. the durable outcome.

Each step needs its own window: about 5 frames, which is what both pipelines measure. The continuation removes handler-side handoffs, but those overlap with the windows that steps 2–5 need anyway.

So ≈ 5 frames is the floor of **this architecture**: durable writes on a thread other than TouchDesigner's main thread, resuming only in GIL windows. It is *not* a floor of strict durable ordering in TD. The Phase 1B build made the same `synchronous=FULL` writes **on the main thread** at ≈ 34–55 ms sequential p50, at the cost of main-thread fsync (frame max 46 ms under load). The owner ruled that out (B6: never return disk I/O to the main thread).

Options that would change this floor are outside B6:
- main-thread fsync (excluded: frame impact);
- weaker durability (not allowed);
- a higher frame rate (a user setting);
- a native extension that never needs the GIL to complete (a new dependency, needing an ADR).
Batching intents across concurrent calls can raise throughput but not single-call latency.

## Safety re-runs
Because the performance gate failed decisively, the in-TD safety suites were not rerun for the rejected variant, and it received no separate review. For the retained baseline, the in-TD crash, restart, overflow, audit-failure and kill-switch-race tests (gate suite) and the timeout and undo tests (1A/1B suites) ran again on candidate `.tox` `a414f3c9…` (see the [validation index](../../docs/icm/validation-index.md)).
