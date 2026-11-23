# ADR-0006 — Audit and approvals store

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to the Phase 1B audit gates. Revised by the Phase 0 review: intent/outcome events, keyed checkpoints, HMAC arguments ([security model §6](../architecture/security-model.md#6-audit)). **Revised 2026-11-22 (owner direction):** a dedicated audit worker replaces main-thread writes, and storage is split into a project-local audit folder and private per-user credentials and keys ([record](../research/2026-11-22-audit-worker-and-phase-1c.md)).

## Decision
- SQLite (`sqlite3`; verified present in the 2025.32440 bundle, the minimum supported build (owner D10)), with WAL and **`synchronous=FULL` (kept by the owner)**.
- **Storage split (revision):**
  - The audit database (events, sessions, DAT snapshots) lives by default in a git-ignored `.tdflowmcp/<toe name>/` folder next to the saved `.toe`.
  - Credentials (agents, salted token digests, grants), the audit HMAC key and the keyed checkpoints live in private per-user storage (`%LOCALAPPDATA%\TDflowMCP\projects\<project id>\`).
  - Unsaved, read-only, network and cloud-synced locations fall back to private storage, with a notice.
  - The project id (`Projectid`, saved in the `.toe`) plus a private registry detect copied, moved, renamed and relocated projects. Nothing is relocated or adopted without an explicit, verified user action. A database bound to another project is refused.
- The `events` table is append-only. The application never issues UPDATE or DELETE on events, and triggers reject them. Each row carries `prev_hash` and `hash` (a SHA-256 chain over canonical JSON).
- Approvals, agents and policy live in separate tables. Token secrets are stored only as salted hashes.
- **Audit worker (revision):** a single bounded worker thread owns every connection (FIFO). TouchDesigner's main thread never waits for disk I/O. Durable intent is confirmed before execute, and durable outcome before success. The design fails closed on overflow, timeout, worker failure or lost durability, and suspends agents when an outcome cannot be recorded. It replaces "Phase 1 writes are synchronous on the main thread".
- Retention is configurable, defaulting to unlimited. Pruning writes a "checkpoint" event so the chain stays verifiable.
- Export: JSONL, CSV and a self-contained HTML report with chain verification.

## Alternatives
JSONL append files (simpler, weaker querying); storing audit inside the `.toe` (bloats the project and is lost on revert).

## Consequences
Hash chaining detects tampering but does not prevent a local admin from deleting the file. This is documented in the [security model](../architecture/security-model.md).

## Validation gates
- **Tamper detection relative to a checkpoint: passed** (unit and in-TD). This includes a recomputed unkeyed chain caught by the keyed checkpoint, and a deleted checkpoints file. Checkpoints are written every 50 events, at Enable MCP and at shutdown.
- **Mutations fail closed when the store is unavailable: passed.** Reads also fail closed. A failed outcome write suspends all agents.
- **Auth-failure aggregation: implemented** (one `auth_fail_burst` event per 10 s) and observed in the in-TD run. No flood test was run.
- **Write latency (main-thread impact): passed with the audit worker; MCP-latency tradeoff awaits owner acceptance.**
  - Main thread: the 1B build appended ≈ 5.3 ms p50 per event on the main thread (frame max 46 ms under load). With the worker, OnFrame costs ≤ 1.8 ms even with a simulated 50 ms disk (frame max 23.6 ms).
  - Cost: MCP latency rises to ≈ 85–150 ms sequential (1B: 55 ms), because each worker step waits for TouchDesigner to release the GIL (about once per frame).
  - Guarantees: kept and tested in TD: crash, shutdown/restart, load, slow disk, overflow, kill-switch race, injected outcome failure.
  - Follow-up (proposed): a main-thread continuation to cut the handoffs ([record](../research/2026-11-22-audit-worker-and-phase-1c.md)).
