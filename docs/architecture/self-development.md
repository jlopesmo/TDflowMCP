# Self-development: Candidate Development Mode (A/B cycle)

**Status: Conditionally Accepted** (owner D17, 2026-11-22; [ADR-0014](../decisions/0014-candidate-development-mode.md)). **Stage 1** (isolated candidate build and test, manual owner promotion and rollback) is approved as the first implementation stage. **Stage 2** (same-project candidate workspace) is a future research milestone that needs a passed S8 spike and a new owner decision. Stage 3 is not approved. Nothing here is implemented yet; Phase 2A comes first.

## 1. Goal
Use TDflowMCP itself for meaningful development of TDflowMCP, inside a disposable TouchDesigner project:
- **Instance A:** the stable, protected, active MCP controller.
- **Instance B:** an inactive development candidate.
- The coding agent connects through A to inspect, develop and test B.
- At a milestone, B is validated, synchronized with the authoritative repository source, and exported as a versioned `.tox`.
- After explicit owner approval, a controlled handoff replaces A with B. Earlier versions stay available for rollback.
- The cycle repeats for each development stage.

## 2. Non-negotiable constraints
- **The hard deny on active instances (H3) is unchanged.** Agents never read or modify A's subtree, never modify its ancestors' protected parameters (H3 on ancestors covers modification), and never touch any other *active* TDflowMCP instance.
- **No uncontrolled self-modification:** A never changes its own code, and nothing an agent does takes effect in A without an owner-approved promotion.
- **Source-first:** the repository is the authority for B's code ([source-of-truth](source-of-truth.md)). B inside a project is a synchronized, hash-checked *view* of that source, never a second editable truth.
- **Auditability:** every candidate action (load, sync, export, test, promotion, rollback) is a durable audit event, with source digests and `.tox` SHA-256.
- Tests, ICM governance and approval gates apply to every promotion.

## 3. Key safety insight: B must never execute in A's process
A TouchDesigner COMP with extension parameters can initialise its extension (Python) on load or re-init. A "dormant" B that sits as a live component next to A could therefore run modified code inside A's process, with access to A's memory, transport and data. No policy rule inside A can contain that.

**Design rule (all TD facts below are hypotheses to verify in spike S8):**
- B's code exists in A's project only as **inert data**: Text DATs mirroring `src/` files, in a candidate workspace COMP with no extension parameters and no active Execute or Parameter Execute DATs.
- A forces this inert form when it loads a candidate.
- **B runs only in separate disposable TouchDesigner processes** (the existing `tests/td` harness, or a dedicated candidate test launcher), built from the repository with `tools/build_tox`.

## 4. Components (proposed)

| Part | Responsibility |
|---|---|
| **Candidate workspace** (stage 2, not the PoC) | A Base COMP tagged `TDflowMCPCandidate` (distinct from `TDflowMCP`, so H3 still protects only active instances). It is **registered as a protected kind like `tdflowProject`**: raw operator tools are hard-denied on it, and access is only through `candidate:*` tools. It holds inert Text DATs plus a manifest (`source_commit`, per-file SHA-256). It is created only by a user action in A ("Load Candidate") from a repository checkout chosen by the user. The source path is never settable by an agent or through any agent-updatable parameter. Nothing is ever loaded with `loadTox` into A's process: a built `.tox` contributes only its sidecar `TDflowMCP.json` |
| **Inertness enforcement (A)** | An **allowlist**: the workspace may contain only Text and Table DATs, with `file`/`syncfile` parameters cleared, no inputs, no COMPs, no extension parameters, and no `TDflowMCP` tag. Detection reuses `policy.EXECUTABLE_TYPES`. A also verifies that **no operator outside the workspace references, selects, imports or watches a workspace DAT** (execute DATs, callbacks/script parameters, `mod()`/import, Select OPs). Mirrored DAT names must not shadow module imports (S8). All checks run again atomically on the main thread inside every candidate write. Any violation makes the workspace read-only and alerts the user |
| **Candidate grants** | A separate grant kind, `candidate:<action>`, never implied by builder/observer roles. `candidate:read` inspects the workspace. `candidate:update` (stage 2) edits manifest-listed source DATs. **Execute classification is never relaxed** (review 8): writing Python text stays execute-class, so `candidate:update` writes need the same approval path as any execute action, which is unavailable until approvals exist (2B) |
| **Source synchronization** | A diff of the candidate DAT hashes against the repository working tree (`src/` + manifest). Edits made in the workspace are exported as a patch for review. Import is explicit and previewed (like Skills: preview → confirm digest). There is never an automatic two-way sync |
| **Candidate build and test** | `tools/build_tox` from a clean worktree of a commit, then the in-TD suites against the built `.tox` in disposable processes: `run_td_tests`, `run_td_mcp_tests`, `run_td_gate_tests`, `run_td_onboard_tests`, dogfood. Results are bound to the `.tox` SHA-256 |
| **Export** | `tools/package_release.py` produces a versioned package (`TDflowMCP-<version>-<commit>`, `SHA256SUMS`). The candidate is promotable only from a package whose acceptance results match its hash |
| **Promotion (handoff)** | Owner-approved, in TD (like pairing approval): MCP off → A records the promotion intent (old and new hashes) → A's component is replaced by the packaged `.tox`, keeping `Projectid`, `Port` and storage settings → the new A opens storage, verifies the audit chain and records the promotion outcome → the owner re-enables MCP. Agents can never trigger promotion |
| **Rollback** | Previous packages stay in a private `versions/` folder (by hash). Rollback is the same handoff with an older package. The audit log is append-only across versions (the same store), so history survives promotions. Hypothesis to verify: an older version can read a store written by a newer one (schema compatibility) |
| **Credentials across promotion** | Tokens and grants persist (same `Projectid` and private storage), so a promoted build honours existing grants. A promotion may require re-confirming tokens; this is an owner choice |

## 5. Staged plan

| Stage | Scope | Gate |
|---|---|---|
| **S8 spike** | Verify the TD facts behind §3: what initialises when a `.tox` with extensions is loaded or re-initialised (with cooking off, with the extension parameters cleared), and whether a COMP of inert Text DATs can be guaranteed non-executing | Recorded findings; ADR-0014 revised if a fact differs |
| **PoC (smallest safe; revised after review 8)** | **No in-project workspace.**
- The agent develops B in the repository with its normal file tools, and uses A to dogfood and inspect the disposable project.
- `tools/build_tox` builds B, and the in-TD suites test it in separate processes; the results are bound to the `.tox` SHA-256.
- Promotion is a **manual owner step**: replace A's component with B's packaged `.tox` while MCP is off.
- The *new* A verifies the package hash against the acceptance results and records "previous hash → new hash" with a durable audit event. (The old A cannot record the intent of a manual replacement.)
- Rollback is the same step with the previous package. | H3 tests unchanged and passing; a candidate package passes all suites; promotion and rollback each keep the audit chain verifying and are recorded with both hashes; no agent tool can trigger them |
| **Stage 2** | The protected candidate workspace with `candidate:read`. `candidate:update` follows only once approvals exist (execute class). Workspace-to-repository patch export with preview | Workspace inertness and no external references are proven (S8); no write reaches A; the exported patch equals the DAT diff |
| **Stage 3** | In-TD owner-approved promotion pulse, version store, rollback pulse | Promotion is impossible without owner approval in TD; rollback restores the previous hash |

## 6. Open questions for the owner
- Should B's workspace live in the same disposable project as A, or in a second disposable project inspected through a separate A instance?
- Retention of previous versions: how many, and where (private storage by default)?
- Should promotion require all suites, or a named subset per milestone?
