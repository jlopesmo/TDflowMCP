# Phase 2A increment 1: project memory, checkpoints, verified handoffs (2026-11-22)

**Owner decision D17:** conditionally accept ADR-0014 (stage 1 only), close the audit-performance investigation, then proceed with Phase 2A as planned in disposable TouchDesigner projects, keeping `main` unchanged.

**Delivered:** embedded-mode project memory with a single authority, task checkpoints, verified handoffs, resume with out-of-band detection, a context-aware Tell Your Coding Agent, and Skill 1.1. The design and its deliberate differences from the earlier proposal are in [continuity §7](../architecture/continuity-and-memory.md#7-phase-2a-increment-1-what-is-implemented-2026-11-22). The review is [review 9](2026-11-22-phase-2a-review.md).

## Evidence (TouchDesigner 2025.32440)

| Check | Result |
|---|---|
| Unit tests | 27 memory tests (pure module + engine path with a fake adapter); full suite passes |
| In-TD memory suite (`run_td_memory_tests.py`, candidate `d2a9e077…`) | 14/14 + 55/55. ADR-0012 gates: **false claim tagged `contradicted`**, **resume detects an out-of-band edit**. ADR-0013 gate: **stale write rejected**. Also: observer denied, H4 on record DATs, edited/missing/foreign records read-only until claim, two authorities refused, Tell has no task ids or agent text, no token in answers |
| Dogfood handoff pass (real Claude Code 2.1.289 sessions, two different agents) | in-TD 7/7, harness 32/32. Agent B checkpointed, built `noise1 → level1 → blur1 → out` and handed off (7 claims verified, 0 contradicted, 1 note unverified). The user then edited `noise1` in TD. Agent A, given only the Tell Resume section, resumed, was told `/project1/fx/noise1` changed outside TDflowMCP, finished the task (`blur1` size 10) and recorded it as done (rev 3). 0 errors, 0 unaccounted executions, TDflowMCP internals unchanged |
| Main-thread cost | Memory calls ≈ 66–145 ms p50/max. A 300-operator fingerprint gave a frame max of ≈ 19 ms (28 ms before the review-9 fixes); small scopes cost ≈ 0.1 ms per operator |

## Dogfood findings (friction, recorded)
1. **Wiring could not be claimed** (dogfood pre-fix pass). Added the `input` claim kind; the rerun agent used it for three claims, all verified.
2. **New operators land at 0,0** when the agent gives no position, overlapping the network. Candidate: default placement next to the input or parent's free space.
3. **A handoff has no status**, so the task status stays that of the last checkpoint. The agent noticed. Candidate: an optional `status` on handoffs.
4. **The fingerprint says *which operator* changed, not which parameter.** The resuming agent compared parameters with defaults to guess. Candidate: a per-parameter digest diff on resume, for readable operators only.
5. **`unchanged` claims** are honestly worded ("no audited change by me; others may have changed it"), and agents relayed the caveat.
6. **One unexplained TouchDesigner hang** in the first handoff pass: main-thread calls timed out, then the listener went away, with an empty TD log. It did not reproduce in the rerun or in any other suite, so it is recorded like the earlier 1A one-off, with no cause established.

## Not done in this increment
Repo mode, `tdflow-project.json` and the `Projectid` link check (ADR-0013 gate), mode migration (ADR-0013 gate), tasks/decisions/ICM record types, archiving, splitting large fingerprints across frames, and TouchDesigner restart persistence of records (untested).
