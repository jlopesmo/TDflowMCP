# Audit worker, project-local storage and Phase 1C onboarding (2026-11-22)

**Owner direction (2026-11-22):**
- Conditionally approve ADR-0011.
- Keep `synchronous=FULL`.
- Investigate moving audit disk I/O off TouchDesigner's main thread before accepting the latency gate.
- Authorize Phase 1C once the audit performance and safety gate passes.
- Keep 2025.32440 supported; keep `main` unchanged.
- Mid-task decision: by default, store the audit database, sessions and recovery snapshots in a `.tdflowmcp/` folder next to the `.toe`. Credentials, tokens and audit keys stay in private per-user storage.

**Environment:** Windows 11; TouchDesigner 2025.32440 (Python 3.11.10); disposable projects under `.tmp/` only; Claude Code 2.1.289.

## 1. Architecture (implemented)

| Part | What it does |
|---|---|
| `auditworker.py` | One daemon thread owns every SQLite connection. FIFO, bounded queue (1024), per-operation timeout 2 s, sticky failure on storage errors. `submit` never blocks; `call` raises on the TD main thread. `close` is a flag: drain, checkpoint, close |
| Handler-thread orchestration | `/mcp` and the paired admin API are transport route handlers. A TD step runs through `RouteContext.call_main` (a function job run by `pump` on the main thread). "Certainly not run" maps to abort; "may have run" maps to `OUTCOME_UNKNOWN` and suspends agents |
| Engine | Per call: 1. plan on main (resolve + policy); 2. durable intent (+ DAT snapshot); 3. execute on main (re-resolve and compare → `PRECONDITION_CHANGED`; re-check kill switch, token, policy); 4. durable outcome, else `OUTCOME_UNRECORDED` and all agents suspended. A step never runs once MCP is off or the engine was replaced |
| Main thread | Only *submits* audit work. Pairing approval, Enable MCP (verify → record → checkpoint) and resuming agents take effect only after confirmation, which is polled in `OnFrame` |
| `storage.py` | Location classes; private per-project registry; identity decisions (NEW, OK, FALLBACK, UNSAVED, MOVED, COPIED, RELOCATE, LEGACY); TD incremental saves (`show.12.toe`) count as the same project; `.gitignore` = `*`; verified migration (read-only/immutable source, staging in private storage, credential tables dropped + VACUUM, project binding) |
| `store.py` | Audit DB (`datadir`) and private `credentials.sqlite`, `audit.key`, `checkpoints.jsonl` (`private_dir`). `bind_project`; read-only open; fsynced checkpoint lines; torn-tail repair |

## 2. Durability guarantees (each tested)

| Guarantee | Evidence (in-TD unless noted) |
|---|---|
| Intent durable before execute | Unit: an independent connection sees the row once `call` returns. Engine tests: no execute when the intent fails |
| Outcome durable before success | Injected outcome-write failure → `OUTCOME_UNRECORDED`, the change is not reported as successful, agents suspended, store failed closed, second call rejected; the unaccounted intent stays visible |
| Main thread never waits on disk | `call` raises on the owner thread (unit). With a simulated 50 ms disk, OnFrame p99 = 1.44 ms, max 2.23 ms |
| Fail closed: overflow | Queue limited to 2 with a 0.2 s disk: 9 of 12 calls `AUDIT_UNAVAILABLE` (nothing executed), 3 OK, each with a durable outcome |
| Fail closed: worker failure | Store closed inside the worker → calls fail; MCP turns itself off; no silent reopen; Enable MCP reopens and re-verifies (1B suite) |
| Kill switch during the intent write | Switch flipped while a 0.3 s intent write was in flight → `SUSPENDED`, parameter unchanged, an `aborted` outcome recorded |
| Crash | 8 clients, hard kill after 2 s. On restart, chain and keyed checkpoints verify (813 events, unverified tail 13). All 33 successes the clients saw are durable outcomes. 0 unaccounted intents |
| Order and chain | 8 concurrent writers × 25 (unit): per-writer order kept, chain intact. `BEGIN IMMEDIATE` re-reads the head, so a draining old worker cannot fork it |
| Existing history | The 1B layout migrates by verified copy; the source is untouched (unit). Restart persistence (1B suite) |

## 3. Performance: main thread versus MCP latency (measured separately)

Same load in each run: 30 sequential `set_parameters`, then 8 clients × 30 calls (writers + readers).

| Build | Frame interval p95 / max (load) | OnFrame cost p99 / max (load) | MCP sequential p50 / p95 | MCP concurrent p50 / p95 | Throughput |
|---|---|---|---|---|---|
| Phase 1B baseline (clean `e15ec55` build, SHA-256 `b9da9a18…`) | 21.07 / **46.47 ms** | not instrumented (synchronous fsync on the main thread) | 54.9 / 68.7 ms | 140 / 168 ms | 54.2 calls/s |
| Audit worker, `perf` launch (`1b6a44fd…`) | 20.04 / **23.64 ms** | 1.56 / 1.83 ms | 152 / 172 ms | 534 / 665 ms | 14.4 calls/s |
| Audit worker, `gate` launch | 16.77 / 19.31 ms | 1.54 / 1.72 ms | 83 / 97 ms | 268 / 350 ms | 27.6 calls/s |
| Audit worker, simulated 50 ms disk | 16.74 / 17.52 ms | 1.44 / 2.23 ms | 184 / 268 ms | 1099 / 1167 ms | 7.3 calls/s |

**Root cause of the higher MCP latency** (`gate_bench`, in TD):

| Operation | p50 |
|---|---|
| Durable append on the main thread | 5.1 ms |
| The same append on the worker thread | 16.7 ms (one frame) |
| Main-thread handoff | 16.6 ms |
| Worker read | 0.03 ms |

After fsync, a thread can resume only when TouchDesigner releases the Python GIL, about once per frame. Each cross-thread step therefore costs about a frame, and a call has several.

- Yielding the GIL with `time.sleep(0)` at the end of `OnFrame` made no measurable difference, so it was reverted.
- **Proposed follow-up (not implemented):** a main-thread continuation. Plan and submit the intent; execute on the frame after the intent is confirmed; submit the outcome. Expected 2–3 frames per call instead of ≈ 6–9. This is a hypothesis.

**Tradeoff for the owner:**
- The worker removes disk I/O from the main thread: no frame regression, even with a slow disk.
- Durability stays at `synchronous=FULL`, and every security guarantee holds.
- MCP calls are about 1.5–3× slower than in 1B (≈ 85–150 ms sequential), which is small next to coding-agent turn times.
- Nothing was weakened to get here.

## 4. Project-local storage (in TD, gate + restart launches)

- **New saved project:** `.tdflowmcp/<name>/` holds only `tdflowmcp.sqlite*`, plus `.gitignore` (`*`) and a README.
  - Private storage holds `audit.key`, `credentials.sqlite` and `checkpoints.jsonl`.
  - `Projectid` is set; Data Location In Use shows no absolute path; the store is verified at open.
  - Harness scan of the project folder: no key, credentials or checkpoints file; no token secret; no token tables.
- **Identity recovered** from the private registry when the `.toe` was not saved since (crash launch).
- **Rename** (Save As; TD named it `td_tests_renamed.1.toe`), original removed:
  - detected as `MOVED`; MCP refused until confirmed;
  - Confirm Move made a verified copy (826 events) and left the source untouched;
  - the chain verifies at the new location; agents and tokens are kept.
- **Copy** into another folder while the original exists: `COPIED`. Claim As New Project gives a new id, an empty log and no tokens carried over; the original's event count is unchanged.
- **Data Location → private:** `RELOCATE`; Confirm Move made a verified copy into private storage; the source is kept.
- **Unit-tested only:** network, cloud-synced and read-only fallbacks; unsaved projects; adoption of a database that moved with the folder; a foreign database refused; traversal through the `.toe` name blocked.

## 5. Phase 1C

- **Registry** (`src/integrations/clients.json`):
  - Claude Code is `primary`; Codex CLI, Cursor and VS Code Copilot are `documented`.
  - Facts were checked against vendor docs on 2026-11-22 (two research agents; URLs in the file). The Codex details came from a summarised fetch, not verbatim text.
- **Connection instructions** ("Connect your coding agent" in Tell Your Coding Agent):
  - token entry kept out of shell history (bash/zsh `read -rs`; PowerShell `Read-Host -AsSecureString`, verified on 5.1 and 7.7);
  - per-client JSON/TOML entries, with `${TDFLOW_TOKEN}` single-quoted in the bash/zsh/PowerShell `claude mcp add` commands.
- **Live Claude Code session (2.1.289):**
  - Connected using only the JSON entry extracted from the generated text (temporary `--mcp-config`, `--strict-mcp-config`, three allowed tools plus `Skill`; token only in the child environment).
  - Result line: `skill_version=1.0 expected_skill_version=1.0 opacity=0.25 audit_seq=22`.
  - The edit is applied in TD and audited with the client's info. No token in the config file, the output, the Skills files or the logs.
  - The first live attempt found a **real protocol bug**: 2026-07-28 `tools/list` and `server/discover` results lacked the required caching hints `ttlMs`/`cacheScope`. Claude Code rejected the list. Fixed (`tools/list`: `ttlMs` 0, `private`; discover: 60 s, `public`) with unit and in-TD checks.
- **Skills** (`src/skills/tdflow-core/SKILL.md`, authored independently): guidance only.
  - Assisted install through the paired admin API: preview, then confirm the exact preview digest; hash-verified against the build manifest; atomic exclusive writes; `tdflow-skills.manifest.json`.
  - Never overwrites a modified file; stale previews are refused; unlisted targets are refused; links are rejected; uninstall keeps modified files; every write is audited.
  - Round-trip passed on a disposable home and the disposable project folder. The project-scope Skill was discovered by the live session.
- **Version management:** `tdflow_whoami.expected_skill_version` = `1.0`. The skill compares it and tells the user. The Setup page's Skills table shows installed versus bundled versions and flags a mismatch (tested by forcing manifest version `0.9`).
- **UX:**
  - Status Detail shows one item (≤ 160 characters, newest wins, errors always win).
  - Essential parameters stay on the **TDflowMCP** page; the rest moved to **Advanced**.
  - The **View Agent Instructions** pulse opens the full text.
- **Packaging:** `tools/package_release.py` (clean worktree → `dist/`) and the [install guide](../operations/install.md). Package evidence is in the [validation index](../icm/validation-index.md).

## 6. Independent review 7
11 findings (0 critical), all fixed: [review 7](2026-11-22-phase-1c-review.md).
