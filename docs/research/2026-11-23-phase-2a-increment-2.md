# Phase 2A increment 2: restart persistence, project identity, restoration (2026-11-23)

**Owner decision D18 (2026-11-22):**
- Accept the role-and-operator-scope memory access model for Phase 2A, recorded conditionally in [ADR-0013](../decisions/0013-project-memory-model.md#conditional-decision-on-memory-permissions-owner-d18-2026-11-22).
- Prioritise TouchDesigner restart persistence, project identity verification and reliable memory restoration.
- Investigate the large-scope fingerprint frame hitch and the unexplained hang without delaying other safe work.

## Delivered
- **Project identity verification.** The audit log binds the project to one memory `Projectid`: the latest `memory_create` or `memory_claim`.
  - A `tdflowProject` with another `Projectid` is read-only for agents (`identity_mismatch`) until the user's Claim Project Memory.
  - The ledger is read from the latest claim on, so the binding is always present.
  - If the ledger cannot be read whole, memory fails closed (`ledger_incomplete`).
- **Restart persistence and restoration (verified in TD).** A two-launch test saves the `.toe`, writes one more record without saving, then restarts TouchDesigner from the saved file:
  - the saved records, the `Projectid`, the project storage identity and the audit chain are intact;
  - the fingerprint is identical across the restart (`reliable: true`, no out-of-band report);
  - the unsaved record is reported `missing`, so memory is read-only with the reason;
  - after Claim Project Memory, writing continues from the restored revision;
  - MCP was not re-enabled by the reload.
- **Agent-reported status.** Task rows carry `status_source: agent_reported`, and presented records carry `authority: agent_reported`. Agents cannot write decision records.
- **Frame hitch.**
  - Fingerprint digests are computed in chunks of 50 operators, one main-thread step each.
  - Each chunk re-checks the kill switch and the token.
  - The operator walk is bounded at 1000 operators.
- **Hang investigation.** A test-only `faulthandler` watchdog in every in-TD suite dumps all thread stacks if the main thread stalls for 10 s, and a `<mode>.no_main_thread_stall` check reads the dump. Two more two-agent dogfood runs (7/7 + 33/33 each), the memory suites and the gate suite ran with it, and no stall occurred. The original hang remains unexplained and unreproduced.

## Measurements (TouchDesigner 2025.32440)

| Scope | Frame max during the call | Call time |
|---|---|---|
| 300 operators, one step (increment 1, after review 9) | ≈ 19 ms | ≈ 145 ms |
| 300 operators, chunked | ≈ 2.4 ms | ≈ 216 ms |
| 2000-operator scope, walk bounded at 2000, 300 fingerprinted | 13.3 ms (the walk in one step) | ≈ 190 ms |
| 2000-operator scope, walk bounded at 1000 (final, candidate `68e1f1f9…`) | **7.1 ms** | ≈ 185 ms |

## Independent review 10
A separate read-only agent reviewed the increment. It found no High findings, 2 Medium and 5 Low.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | Medium | `current-state.md` still listed restart persistence as unverified and as next work | **Fixed** |
| F2 | Medium | The continuity measurements were stale (28 ms step; "splitting fingerprints" still listed) | **Fixed** with this run's numbers |
| F3 | Low | Identity was enforced only while a create or claim event was inside a 20,000-event window, so a flood of checkpoints could push it out | **Fixed:** the window starts at the latest claim, and the latest create is fetched when no claim exists. A truncated ledger fails closed. Unit tests cover both |
| F4 | Low | Fingerprint chunks did not re-check revocation or the kill switch | **Fixed:** each chunk re-checks both. The fingerprint spans several frames; it is not atomic, and audited changes after `audit_to` explain anything in between |
| F5 | Low | The walk plus readability facts still ran in one step | **Measured** at 2000 operators (13.3 ms); walk bound lowered to 1000 |
| F6 | Low | The in-TD identity case also had foreign records | **Added:** an identity-only case with zero records |
| F7 | Low | The watchdog re-armed by frame count | **Fixed:** it re-arms on wall time |

The reviewer also checked and found sound:
- No agent path to `admin` events.
- Every lockout is recoverable through Claim Project Memory.
- Readable filtering is preserved after chunking.
- Plan/write consistency is preserved.
- No disk I/O on the main thread.
- The owner's D18 conditions are stated correctly.
