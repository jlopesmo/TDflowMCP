# ADR-0006 — Audit and approvals store

**Status:** Proposed (2026-11-22).

## Decision
- SQLite (`sqlite3`, bundled with TD 2025.32440, verified) at `%LOCALAPPDATA%\TDflowMCP\<project-id>\audit.sqlite`, outside the repository and the `.toe`.
- The `events` table is append-only. The application never issues UPDATE or DELETE on events, and triggers reject them. Each row carries `prev_hash` and `hash` (a SHA-256 chain over canonical JSON).
- Approvals, agents and policy live in separate tables. Token secrets are stored only as salted hashes.
- Phase 1 writes are synchronous on the main thread (WAL mode, measured). Batching or async writes only if budgets fail.
- Retention is configurable, defaulting to unlimited. Pruning writes a "checkpoint" event so the chain stays verifiable.
- Export: JSONL, CSV and a self-contained HTML report with chain verification.

## Alternatives
JSONL append files (simpler, weaker querying); storing audit inside the `.toe` (bloats the project and is lost on revert).

## Consequences
Hash chaining detects tampering but does not prevent a local admin from deleting the file. This is documented in the [security model](../architecture/security-model.md).
