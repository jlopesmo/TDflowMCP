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
| Spikes S4 / S7 | `python -I spikes/s4/run_s4.py .tmp/s4`, `python -I spikes/s7/run_s7.py .tmp/s7` | Undo semantics; frame callbacks with the timeline paused | Executed; see [S4](../research/2026-11-22-s4-undo-semantics.md), [S7](../../spikes/s7/README.md) |
| Spike S6 (2025.32440) | `spikes/s6/` copied to `.tmp/s6/`, `python -I build_toe.py`, then `python -I s6_harness.py main` and `… conflict` with `TDFLOW_SRC` set | Embedded transport in TD: binding, LAN, threads, lifecycle, timeouts, conflicts, load, frame impact, recovery, static Setup page + headless browser, gate/auth/kill switch | Executed; see [S6 findings](../research/2026-11-22-s6-embedded-python-transport.md) |
| Protocol/policy/audit tests, S1 | — | — | **Do not exist / not executed** (Phase 1B; S1 optional) |

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

## Local environment facts (file inspection only, 2026-11-22)

- **TouchDesigner 2025.32440** is installed, with `python311.dll`, `_sqlite3.pyd`, `jsonschema` 4.23 and `pip` 24.2. A legacy install bundles `python39.dll`. 2025.33230+ is not installed and is not required (D10). TouchDesigner was launched only with disposable spike files (S5, S6).
- Chrome and Edge are installed. Edge headless made no request on this machine; Chrome headless (throwaway profile) worked.
- Node.js v24.14.0 and Python 3.11.8 are on PATH. There is no `gh` CLI.
- Original bridge hashes were verified with `sha256sum`: the `.tox` matches the audit's stated SHA-256, and the committed audit/zip copies are byte-identical to the supplied files.
