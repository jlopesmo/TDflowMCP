# TDflowMCP — Validation index

This file lists every check that exists, how to run it, and its last **executed and observed** result. Unrun checks are marked **not executed**. Unobservable results are marked **not observed**, with the reason.

## Checks

| Check | Command | Scope | Last result |
|---|---|---|---|
| Doc checker | `python tools/check_docs.py` | Required files, relative links/images + anchors, ADR status vs index, privacy patterns in `.md/.py/.yml/.yaml/.json/.txt` | See runs below |
| Doc checker unit tests | `python -m unittest discover -s tests -t .` | `tools/check_docs.py` | See runs below |
| CI workflow `docs.yml` | GitHub Actions on push to `dev`/`main` and on every pull request | Doc checker + all unit tests on Python 3.11, Ubuntu + Windows | **Not observed.** The repo is private, and no GitHub integration is available in this environment: no `gh` CLI, no GitHub connector (checked 2026-11-22), and a credential-based API read was denied. No run status, URL or log has been seen for any commit. Owner action [B3](roadmap.md#open-blockers-and-owner-actions) |
| Independent documentation review | separate agent, read-only | All docs + tooling | Review 1 and review 2, see runs below |
| Core unit tests (`tests/unit/`) | `python -m unittest discover -s tests -t .` (same command) | `net.bindverify`, `net.oslisteners`, `net.pyhttp` (real loopback sockets, ephemeral ports), `gate`, `compat`, troubleshooting data, `td`-free/no-subprocess boundaries | See runs below |
| TD bundled-Python parity | `"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\python.exe" -m unittest discover -s tests -t .` | The same suite on TD's interpreter (no TouchDesigner process) | See runs below |
| Spike S5 (2025.32440) | `spikes/s5/` copied to `.tmp/s5/`, then `python -I s5_harness.py` (and `TDFLOW_S5_NO_WS=1`) | Runtime, libraries, parameters, clipboard, Web Server DAT, binding, gate | Executed; see [S5 findings](../research/2026-11-22-s5-compatibility-2025.32440.md) |
| `.tox` build (S2) | `python -I tools/build_tox/build.py [--check-reproducible]` | Source-first build, embedded hash self-check, reproducibility | See runs below |
| In-TD integration suite (S3) | `python -I tests/td/run_td_tests.py [--mode real_browser]` | The packaged component in a disposable project: parameters, lifecycle, binding, LAN, gate, MCP disabled, Setup page, clipboard/browser failures, timeouts, conflicts, re-init, deletion, paused timeline, secrets | See runs below |
| Phase 1B in-TD acceptance | `python -I tests/td/run_td_mcp_tests.py` | Packaged `.tox`: pairing and admin API, tokens, Enable MCP, both MCP eras, 16 tools, scoped allow/deny + hard denies + review-6 classification, verified undo + conflicts, kill switch incl. queued request, audit fail-closed/integrity/tamper, revocation, secrets, restart persistence | See runs below |
| Secret scan | `python tools/scan_secrets.py` | Every git-tracked file (CI step) | See runs below |
| Spikes S4 / S7 | `python -I spikes/s4/run_s4.py .tmp/s4`, `python -I spikes/s7/run_s7.py .tmp/s7` | Undo semantics; frame callbacks with the timeline paused | Executed; see [S4](../research/2026-11-22-s4-undo-semantics.md), [S7](../../spikes/s7/README.md) |
| Spike S6 (2025.32440) | `spikes/s6/` copied to `.tmp/s6/`, `python -I build_toe.py`, then `python -I s6_harness.py main` and `… conflict` with `TDFLOW_SRC` set | Embedded transport in TD: binding, LAN, threads, lifecycle, timeouts, conflicts, load, frame impact, recovery, static Setup page + headless browser, gate/auth/kill switch | Executed; see [S6 findings](../research/2026-11-22-s6-embedded-python-transport.md) |
| Audit-worker gate + storage + perf | `python -I tests/td/run_td_gate_tests.py [--only …]` | Five launches: storage decision, frame impact (idle/load/slow disk) and MCP latency, kill-switch race, overflow, outcome failure, crash + restart, move/copy/relocate; 1B baseline versus worker; `gate_bench` handoff costs | See runs below |
| Phase 1C onboarding (live) | `python -I tests/td/run_td_onboard_tests.py [--no-live]` | Generated connect text → live Claude Code session (`--strict-mcp-config`, token in env only); Skills round-trip via the admin API; version mismatch; secrets | See runs below |
| Dogfood (real agent work) | `python -I tests/td/run_td_dogfood.py [--tasks …]` | Five Claude Code sessions doing development tasks; transcripts analysed for bugs and friction ([dogfood 1](../research/2026-11-22-dogfood-1.md)) | See runs below |
| Release package | `python -I tools/package_release.py` | Clean-worktree build on 2025.32440; commit, dirty and hash checks; `dist/` | See runs below |
| Policy branch coverage, S1, live sessions with other clients | — | — | **Not executed** (no coverage tool; S1 optional; Codex/Cursor/VS Code `documented` only) |

## Runs

| Date | Environment | Check | Result |
|---|---|---|---|
| 2026-11-22 | Windows 11, CPython 3.11.8 | `check_docs.py` (foundation `82b64f1`) | Pass: `35 markdown files, 0 problem(s)` |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (foundation) | Pass: `Ran 12 tests … OK` |
| 2026-11-22 | — | Independent review 1 | 20 findings; dispositions in [review 1](../research/2026-11-22-phase-0-independent-review.md) |
| 2026-11-22 | — | Independent review 2 (revision) | 21 findings (3 High, 11 Medium, 7 Low), all addressed; see [review 2](../research/2026-11-22-phase-0-revision-review.md) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | `check_docs.py` (revision, pre-commit) | Pass: `47 markdown files, 0 problem(s)` |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (revision, pre-commit) | Pass: `Ran 14 tests in 0.238s OK` |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable `s5.toe`) | S5 full run | Exit code 0; bind gate **CLOSED** (verdict fail: `0.0.0.0:9480`, non-loopback connect succeeded); 1 request in `VERIFYING` → 503; no 200 served before or after the verdict |
| 2026-11-22 | TouchDesigner 2025.32440 (no Web Server DAT) | S5 params-only run | Exit code 0; late pulse → `onPulse`; `onValueChange` fired; no listener |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (S5 + 1A prep, before review 3) | Pass: `Ran 58 tests … OK (skipped=1)` |
| 2026-11-22 | TD 2025.32440 bundled Python 3.11.10 | unit tests (parity, before review 3) | Pass: `Ran 58 tests … OK (skipped=1)` |
| 2026-11-22 | — | Independent review 3 (S5 + 1A prep) | 25 findings (2 High, 10 Medium, 13 Low); dispositions in [review 3](../research/2026-11-22-s5-review.md) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (after review-3 fixes) | Pass: `Ran 76 tests in 0.280s OK (skipped=1)` |
| 2026-11-22 | TD 2025.32440 bundled Python 3.11.10 | unit tests (parity, after review-3 fixes) | Pass: `Ran 76 tests in 0.315s OK (skipped=1)` |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (S6, before review 4; 3 consecutive runs) | Pass: `Ran 99 tests … OK (skipped=1)` ×3 |
| 2026-11-22 | TD 2025.32440 bundled Python 3.11.10 | unit tests (S6 parity, before review 4) | Pass: `Ran 99 tests in 5.162s OK (skipped=1)` |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable `s6.toe`) | S6 main (before review 4) | Pass 65/65; exit code 0 |
| 2026-11-22 | — | Independent review 4 (S6 security + threading) | 12 findings (1 High, 4 Medium, 7 Low); dispositions in [review 4](../research/2026-11-22-s6-review.md) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (after review-4 fixes) | Pass: `Ran 111 tests in 5.770s OK (skipped=1)` |
| 2026-11-22 | TD 2025.32440 bundled Python 3.11.10 | unit tests (parity, after review-4 fixes) | Pass: `Ran 111 tests in 5.733s OK (skipped=1)` |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable `s6.toe`) | S6 main (after review 4) | **Pass 66/66**; exit code 0; 924 dispatches on `MainThread`, 0 elsewhere; LAN connect not connected; no listener after exit |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable `s6.toe`) | S6 conflict (after review 4) | **Pass 4/4**; `PORT_IN_USE`, TD never listened; exit code 0 |
| 2026-11-22 | Windows 11, CPython 3.11.8 | `check_docs.py` (S6, pre-commit) | Pass: `53 markdown files, 0 problem(s)` |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | S4 undo spike (2 runs) | Exit code 0; results in [S4](../research/2026-11-22-s4-undo-semantics.md) |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | `.tox` build + `--check-reproducible` (before review 5) | Pass: builder checks OK; source digest equal; expanded diff `.build` only |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | In-TD suite, first run | 34/35 + 58/59: a controller bug (re-open launch never consumed) and a test race; both fixed |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | In-TD suite + real browser (before review 5) | Pass: 35/35 + 59/59; 12/12 + 5/5 |
| 2026-11-22 | — | Independent review 5 (Phase 1A component) | 16 findings (0 High, 3 Medium, 13 Low); dispositions in [review 5](../research/2026-11-22-phase-1a-review.md) |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | In-TD suite with the paused-timeline test (before S7 fix) | **Froze**: frame callbacks stop while the root timeline is paused; the harness ended its own TD at the deadline (rc 1). This led to S7 |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | S7 (3 runs) | Without `independent`: no frames (killed by timeout, own process only). With `independent`: 141 vs 1 frames during the pause; rc 0 |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | `.tox` build + `--check-reproducible` (final) | Pass: SHA-256 `46eab026…`; source digest equal; expanded diff `.build`, `local/time/cframe.ts` |
| 2026-11-22 | Windows 11, CPython 3.11.8 / TD Python 3.11.10 | unit tests (final) | Pass: `Ran 142 tests … OK (skipped=1)` on both |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | **In-TD full suite (final)** | **Pass: 48/48 in TD + 69/69 harness**; rc 0; other TD processes untouched |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | **Real default-browser run (final)** | **Pass: 12/12 + 5/5**; the browser fetched `/setup/`, `app.js`, `/setup/api/status` |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable, 2 launches) | S4b undo stack / interleaving / save-reload | rc 0 both; results in [S4 record](../research/2026-11-22-s4-undo-semantics.md) |
| 2026-11-22 | TD Python 3.11.10 | audit append latency (300 appends, `synchronous=FULL`) | p50 5.3 ms, p95 6.0 ms, max 9.2 ms; ADR-0006 latency gate **open** |
| 2026-11-22 | TouchDesigner 2025.32440 | Phase 1B suite, runs before review 6 | 40/41 + 87/98 (bugs: `td.ParMode`, `get_parameters`, an existence leak, float detail lost an outcome); fixed → 41/41 + 99/99 pass |
| 2026-11-22 | — | Independent review 6 (Phase 1B security) | 3 High, 8 Medium, 9 Low; dispositions in [review 6](../research/2026-11-22-phase-1b-review.md) |
| 2026-11-22 | Windows 11, CPython 3.11.8 / TD Python 3.11.10 | unit tests (final) | Pass: `Ran 204 tests … OK` (skipped=2 / skipped=1) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | `scan_secrets.py` (final) | Pass: 0 findings (tracked files, plus all new untracked files before commit) |
| 2026-11-22 | TouchDesigner 2025.32440 | `.tox` build + `--check-reproducible` (final) | Pass: SHA-256 `52d039eb7fefe241…`; source digest equal; expanded diff `.build`, `local/time/cframe.ts` |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable, 2 launches) | **Phase 1B in-TD acceptance (final)** | **Pass: 41/41 in TD + 110/110 harness**; rc 0; audit 39 intent / 39 outcome / 30 deny |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | **Phase 1A suite on the 1B build (final)** | **Pass: 49/49 + 69/69** |
| 2026-11-22 | TouchDesigner 2025.32440 + default browser | **Real-browser run (final)** | **Pass: 13/13 + 5/5** |
| 2026-11-22 | TouchDesigner 2025.32440 (disposable) | `gate_bench` (handoff costs) | Main-thread append p50 5.1 ms; worker append 16.7 ms; main handoff 16.6 ms; worker read 0.03 ms. A `time.sleep(0)` yield in OnFrame had no effect (reverted) |
| 2026-11-22 | TouchDesigner 2025.32440 | Gate suite, runs before review 7 | First run: 24/24 + 90/91 (two runner kwarg bugs aborted scripts; restart lacked fixtures). Second: 46/49 (TD's incremental save `name.1.toe` → **product fix**: base names). Third: 48/49 (test baseline bug) |
| 2026-11-22 | TouchDesigner 2025.32440 + Claude Code 2.1.289 | Onboarding, runs before review 7 | Skills listing 404 (gate fix: `/setup/api/skills` is an admin read); then the live session connected but **rejected `tools/list`: missing MCP 2026-07-28 `ttlMs`/`cacheScope`** (fixed); then pass 5/5 + 45/45 |
| 2026-11-22 | — | Independent review 7 (audit worker, storage, 1C) | 11 findings (0 critical, 1 High/Medium, 6 Medium, 4 Low), all fixed; see [review 7](../research/2026-11-22-phase-1c-review.md) |
| 2026-11-22 | Windows 11, CPython 3.11.8 / TD Python 3.11.10 | unit tests (after review 7) | Pass: `Ran 231 tests … OK (skipped=2)` / `Ran 248 tests … OK (skipped=1)` (TD run includes `tests/`) |
| 2026-11-22 | Windows PowerShell 5.1.26100 / PowerShell 7.7.0 | token-entry recipe | `[Net.NetworkCredential]::new('', <SecureString>).Password` returns the text on both |
| 2026-11-22 | TouchDesigner 2025.32440 (working-tree build `1b6a44fd…`) | **Phase 1A suite (after review 7)** | **Pass: 49/49 + 69/69** |
| 2026-11-22 | TouchDesigner 2025.32440 (`1b6a44fd…`) | **Phase 1B suite (after review 7)** | **Pass: 43/43 + 110/110** (`kill.queued_request_rejected` now also accepts the audited `SUSPENDED` tool error) |
| 2026-11-22 | TouchDesigner 2025.32440 (`1b6a44fd…`; baseline clean `e15ec55` build `b9da9a18…`) | **Gate suite (after review 7)** | **Pass: 49/49 + 98/98.** OnFrame p99 1.54 ms under load, 1.44 ms with a 50 ms disk. Frames max 46.5 ms (1B) → 23.6 ms (worker). MCP sequential p50 55 ms (1B) → 83–152 ms (worker). Crash: 33/33 reported successes durable, 0 unaccounted. Overflow: 9 fail closed, 3 OK durable |
| 2026-11-22 | TouchDesigner 2025.32440 + **Claude Code 2.1.289** (`1b6a44fd…`) | **Onboarding (after review 7)** | **Pass: 5/5 + 45/45**; `RESULT skill_version=1.0 expected_skill_version=1.0 opacity=0.25 audit_seq=22` |
| 2026-11-22 | Windows 11 | `scan_secrets.py`, `check_docs.py` (pre-commit) | Pass: 0 findings (167 files incl. new); 0 problems (66 files) |
| 2026-11-22 | TouchDesigner 2025.32440 | **`package_release.py` at `2a7e72f`** | Pass: clean detached worktree, `source_dirty` false; `dist/TDflowMCP-0.2.0.dev1-2a7e72f361/`: tox SHA-256 `9a06308385d4a75504291fb3faa1a7e8258293a1c39df575457678985f531fa8`, zip SHA-256 `be597fb97ed81db7a3ce281784d1bef2065ce1251fb97fbb8b0e0e4e1289d757`; `sha256sum -c SHA256SUMS` OK |
| 2026-11-22 | TouchDesigner 2025.32440 (packaged `9a063083…`) | **Packaged-component acceptance** | **All pass:** 1A 49/49 + 69/69; 1B 43/43 + 110/110; gate 49/49 + 98/98 (OnFrame p99 1.30 ms / max 1.81 ms with a 50 ms disk; crash: 51/51 reported successes durable, 2 in-flight intents unaccounted (≤ 8 allowed); MCP sequential p50 33.5 ms (1B) → 83.4 ms (worker) in the `perf` launches); onboarding with **Claude Code 2.1.289** 5/5 + 45/45, `RESULT skill_version=1.0 expected_skill_version=1.0 opacity=0.25 audit_seq=22` |
| 2026-11-22 | TouchDesigner 2025.32440 (same `.tox` `4c129e93…`, 3 alternating pairs) | **B6 continuation experiment** | Rejected: median sequential p50 83.8 → 83.6 ms (−0.2 %), concurrent 298 → 282 ms, throughput +5 %, no frame regression; 6/6 + 150/150. `ping` 17 ms, `set_parameters` ≈ 84 ms (5 frames) in both pipelines ([spike B6](../../spikes/b6/README.md)) |
| 2026-11-22 | Windows 11, CPython 3.11.8 | unit tests (B6 experiment, before removal) | Pass: 11 continuation tests; full suite OK |
| 2026-11-22 | TouchDesigner 2025.32440 + Claude Code 2.1.289 | **Dogfood pass 1** (baseline source) | 5/5 sessions completed; **bug:** `get_parameters` page filter `INTERNAL_ERROR`; misleading `get_errors` on uncooked operators; friction recorded ([dogfood 1](../research/2026-11-22-dogfood-1.md)) |
| 2026-11-22 | TouchDesigner 2025.32440 (candidate `a414f3c9…`) | **Suites after dogfood fixes 1/3/4** | 1A 49/49 + 69/69; 1B 43/43 + 113/113; gate (gate, crash, restart) 47/47 + 44/44; onboarding pass; **dogfood pass 2** 6/6 + 35/35 (only the 3 intended denials) |
| 2026-11-22 | TouchDesigner 2025.32440 (candidate `1625ec54…`) | **After fix 2 (never-cooked)** | 1B 43/43 + 115/115; dogfood `inspect` pass 3: 27/27, agent reports the uncooked caveat |
| 2026-11-22 | — | Independent review 8 (B6 record, dogfood, read tools, ADR-0014) | 0 High, 5 Medium, 7 Low, all addressed; see [review 8](../research/2026-11-22-dogfood-review.md) |
| 2026-11-22 | Claude Code 2.1.289, throwaway folder (no TD) | isolation-flag probe | `--restricted` and empty setting sources hide project Skills; `--setting-sources project` keeps them; `--tools Skill,ToolSearch` leaves only those built-ins |
| 2026-11-22 | TouchDesigner 2025.32440 (review-8 candidate `2dac3a27…`, uncommitted tree) | **Final suites** | 1A 49/49 + 69/69 (one earlier run ended with TD exit code 1 and no results; not reproduced on rerun); 1B 43/43 + 115/115; gate (gate, crash, restart) 46/46 + 44/44; onboarding 5/5 + 46/46 (`skill_version=1.0 expected_skill_version=1.0`; the Skill check was previously vacuous and is now a whole-word match); **dogfood pass 4** (isolated: tools asserted, working directory outside the repo, no session persistence) 6/6 + 40/40, component digest unchanged, 0 unaccounted executions, OnFrame p99 0.13 ms |

## Local environment facts (file inspection only, 2026-11-22)

- **TouchDesigner 2025.32440** is installed, with `python311.dll`, `_sqlite3.pyd`, `jsonschema` 4.23 and `pip` 24.2. A legacy install bundles `python39.dll`. 2025.33230+ is not installed and is not required (D10). TouchDesigner was launched only with disposable spike files (S5, S6).
- Chrome and Edge are installed. Edge headless made no request on this machine; Chrome headless (throwaway profile) worked.
- Node.js v24.14.0 and Python 3.11.8 are on PATH. There is no `gh` CLI.
- Original bridge hashes were verified with `sha256sum`: the `.tox` matches the audit's stated SHA-256, and the committed audit/zip copies are byte-identical to the supplied files.
