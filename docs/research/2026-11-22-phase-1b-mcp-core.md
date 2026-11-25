# Phase 1B: secure MCP core in the packaged TDflowMCP.tox (TouchDesigner 2025.32440)

**Date:** 2026-11-22 · **Owner direction:** D12 (roadmap).
**Status:** **Complete.** The packaged development build passes its in-TouchDesigner security and functionality acceptance suite after independent [review 6](2026-11-22-phase-1b-review.md); evidence is in §6.
**Open gate:** ADR-0006 audit write latency; see §4.

## 1. What was built

| Area | Implementation |
|---|---|
| **Policy** (`policy.py`) | Deny-by-default CRUD+execute on operator-path globs.<br>• **Hard denies:** TDflowMCP instances and subtrees, including reads; ancestors' delete, rename, flags and extensions; `tdflowProject`; `/sys`, `/local`, `/ui`; anything outside `Projectroot`; non-canonical paths; tag changes.<br>• Roles cap grants, an explicit deny wins, and mutations are all-or-nothing. Recursive reads are filtered.<br>• `execute` always ends in `APPROVAL_UNAVAILABLE` (no approvals in Phase 1).<br>• **Creation allowlist**, plus a parameter denylist covering extensions, callbacks, external tox, scripts, cloning, shortcuts and files |
| **Classification** (`td_adapter/tdops.py`) | The following need `execute` and are therefore denied: expressions; Python, reference and File/Folder-style parameters; wires into DATs and executable operators; DAT text not tagged `tdflowdata` by the user; executable or unknown operator types |
| **Identity** (`identity.py`) | `tdf_<id>.<secret>` tokens with a 256-bit secret, stored only as salted SHA-256 and compared in constant time. Handler threads use an immutable snapshot. Revocation is immediate. Sessions are grouped from audit (15 min idle) |
| **Audit** (`store.py`) | SQLite in `Datadir`: append-only events (enforced by triggers), a SHA-256 chain, keyed `args_hmac`, redacted arguments.<br>• Keyed checkpoints every 50 events, at Enable MCP and at shutdown; missing checkpoints detected; unverified tail reported.<br>• Durable DAT-text snapshots.<br>• **Intent before execution and outcome after, for every call.** Fail closed: no audit, no call (reads included); a failed outcome write suspends all agents. Auth failures are aggregated into one event per 10 s |
| **Protocol** (`protocol.py`) | MCP **2026-07-28** stateless: `_meta` protocol version and client capabilities; header/body consistency (`-32020`); unsupported version (`-32022`); `server/discover`; `resultType`; 404 for unknown methods.<br>**Legacy** `initialize`/`notifications/initialized` for 2025-11-25, 2025-06-18 and 2025-03-26 clients.<br>Notifications get `202`; batches are rejected |
| **Tools** (`tools.py`, `engine.py`) | 16 tools: the 14 core tools plus session note and session list. A registry refuses unclassified tools; only tools listed in `active_tools` are exposed.<br>Every call: validate schema, resolve, apply policy (protection is checked before any resolution error), audit intent, execute on the main thread in a named undo block (rolled back on partial failure), audit outcome |
| **Undo** | `tdflow_undo` acts only on the caller's own newest change, and only if TD's undo stack is exactly as recorded right after that change (S4b). It verifies the restoration by path and state; DAT text is restored from a snapshot. A change is called reversible only if it was recorded cleanly; an unverified restoration is an error |
| **Kill switch** | `Suspendagents` is mirrored at the top of every frame. It rejects every call (`503 SUSPENDED`), invalidates already-queued requests, and is audited |
| **Setup and admin** | Reverse pairing approved inside TD with a non-secret match code: one pending request, never replaced, expires after 2 min, at most 3 per minute.<br>Admin session: HttpOnly + SameSite=Strict cookie plus an in-page CSRF token for reads and mutations; one session at a time; server-side logout.<br>Token creation (role observer/builder, a scope inside the root, an optional subset of actions; shown once) and revocation; agent, session and audit-integrity lists.<br>`Enablemcp` (with preconditions: `td.build`, `net.bind`, `net.port`, `fs.datadir`, `sec.selftest`, ≥ 1 agent), `Pairingrequest`, `Approvepairing`/`Rejectpairing`, `Agentsummary` |
| **Preserved from 1A** | The embedded transport, bind gate, Setup Assistant, parameters and the full 1A suite (re-run on the 1B build) |
| **Not provided (by design)** | Arbitrary or unrestricted Python execution; approvals; project save; agent-configuration writes; installs |

## 2. Stage 2: S4b and safe execution
See the [S4 record](2026-11-22-s4-undo-semantics.md#s4b-undo-stack-interleaving-identity-and-savereload-executed-2026-11-22). Interleaving, the `Delete Node` relabelling, new operator ids after undo, and the empty stack after a reload all shaped the undo rules above.

## 3. Verified audit and undo behaviour (in TD)
- **Integrity:**
  - The chain verifies, and a checkpoint is written and verified.
  - Tampering is detected on a copy of the live database (a row edited after dropping the trigger).
  - The unit tests add three more cases: a recomputed unkeyed chain, a forged checkpoint and a deleted checkpoints file.
- **Event pairing:** every executed call has an `intent` before its `outcome` (same request id): 39 intent, 39 outcome, 30 deny events in the final run.
- **Fail closed:** with the store closed, a mutation and a read both return `AUDIT_UNAVAILABLE`, and nothing changes. After the store is reopened, calls work again.
- **Verified undo, demonstrated per kind:**
  - parameter value: restored to the previous value;
  - create: the operator is gone;
  - connect and disconnect: the wire is restored;
  - delete: the operator is restored at its path with its type;
  - DAT text: restored from the snapshot.
- **Refused undo:**
  - another agent's change on top → `UNDO_CONFLICT`;
  - a human edit inside TD after the agent's change → `UNDO_CONFLICT`, and the agent's value is kept;
  - undo across a TouchDesigner restart → `UNDO_CONFLICT`;
  - nothing to undo → `NOTHING_TO_UNDO`.

## 4. Measurements and limits
- **Audit append latency:** p50 5.3 ms, p95 6.0 ms (`synchronous=FULL`, TD Python, test machine). That is ≈ 11 ms per call on the main thread, above the 5 ms read-overhead target. **ADR-0006 latency gate open.** The owner chooses between full durability, `synchronous=NORMAL` (WAL) and batching reads.
- **Kill switch:** TouchDesigner defers parameter-execute callbacks when a parameter is set by script (`kill_param_callback_synchronous: false`). The per-frame mirror makes the kill switch independent of that.
- **Residual risks** (documented in review 6):
  - a local process can keep pairing busy;
  - the audit key sits next to the database (tamper-evident only relative to exported checkpoints);
  - bind and export propagation are not followed;
  - in-scope reads can mention out-of-scope paths in values or errors.

## 5. TDMCP comparison
See the [TDMCP comparison](2026-11-22-tdmcp-comparison.md). It is documentation-only: TDMCP requires 2025.33230+ and "will not open" on earlier builds, so nothing was downloaded.

## 6. Final evidence (all on the same build)

| Item | Result |
|---|---|
| Build | `build/TDflowMCP.tox` v`0.2.0.dev1`, tool surface `1`, SHA-256 `52d039eb7fefe241ae403b58f14821f823001b77c80d5e839beee6c538f5f4ec`, built with TD 2025.32440. Development build, git-ignored |
| Reproducibility | Source digest equal across two clean builds; expanded differences only `.build` and the local timeline frame |
| Unit tests | 204 OK on CPython 3.11.8 (2 skipped) and on TD Python 3.11.10 (1 skipped). Includes protocol conformance, the policy tables, store/identity/admin, the engine with a fake adapter, and a `jsonschema` cross-check (TD Python) |
| **Phase 1B in-TD acceptance** (two launches: `mcp` + `mcp_restart`) | **41/41 in TD + 110/110 harness, `pass`**; exit code 0 for both; no listener after exit; other TouchDesigner processes untouched |
| Phase 1A regression on the 1B build | **49/49 + 69/69, `pass`** |
| Real default-browser run | **13/13 + 5/5, `pass`** (page, script and API requested by the real browser) |
| Setup Assistant screenshot | Headless Chrome, Enabled state: `build/setup-assistant-1b.png`; a copy is committed at [`docs/research/img/setup-assistant-1b.png`](img/setup-assistant-1b.png) |
| Secret scans | Repository: 0 findings (CI step). In TD: no token secret in parameters, agent text, audit rows, the tokens table, other HTTP responses, `Datadir` files or the TD console logs |
| Independent review | [Review 6](2026-11-22-phase-1b-review.md): 3 High + 8 Medium fixed; Low findings fixed or documented |
