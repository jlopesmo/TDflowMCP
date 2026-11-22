# ADR-0006 — Audit and approvals store

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to the Phase 1B audit gates. Revised by the Phase 0 review: intent/outcome events, keyed checkpoints, HMAC arguments ([security model §6](../architecture/security-model.md#6-audit)).

## Decision
- SQLite (`sqlite3`; verified present in the 2025.32440 bundle, the minimum supported build (owner D10)) at `%LOCALAPPDATA%\TDflowMCP\<project-key>\audit.sqlite` (project key defined in [continuity §2](../architecture/continuity-and-memory.md#2-authority-model-avoids-conflicting-authoritative-records)), outside the repository and the `.toe`.
- The `events` table is append-only. The application never issues UPDATE or DELETE on events, and triggers reject them. Each row carries `prev_hash` and `hash` (a SHA-256 chain over canonical JSON).
- Approvals, agents and policy live in separate tables. Token secrets are stored only as salted hashes.
- Phase 1 writes are synchronous on the main thread (WAL mode, measured). Batching or async writes only if budgets fail.
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
- **Write latency: OPEN.** Measured ≈ 5.3 ms p50 / 6.0 ms p95 per append with `synchronous=FULL` on the test machine, so ≈ 11 ms per call (intent + outcome). That exceeds the 5 ms per-read overhead target. Options for the owner: keep full durability; use `synchronous=NORMAL` in WAL (durable across application crashes, not power loss); or batch read events. Not changed unilaterally ([record](../research/2026-11-22-phase-1b-mcp-core.md)).
