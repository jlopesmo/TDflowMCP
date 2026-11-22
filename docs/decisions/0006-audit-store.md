# ADR-0006 — Audit and approvals store

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to the Phase 1B audit gates. Revised by the Phase 0 review: intent/outcome events, keyed checkpoints, HMAC arguments ([security model §6](../architecture/security-model.md#6-audit)).

## Decision
- SQLite (`sqlite3`; verified present in the local 2025.32440 bundle; presence on the 2025.33230+ target to be confirmed in S1/S5) at `%LOCALAPPDATA%\TDflowMCP\<project-key>\audit.sqlite` (project key defined in [continuity §2](../architecture/continuity-and-memory.md#2-authority-model-avoids-conflicting-authoritative-records)), outside the repository and the `.toe`.
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
- Tamper detection relative to a checkpoint.
- Mutations fail closed when the store is unavailable.
- Auth-failure aggregation under flood.
- Measured write latency on TD's main thread is within the Phase 1B targets. Otherwise this ADR reopens for batching.
