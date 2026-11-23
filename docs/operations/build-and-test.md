# Building and testing TDflowMCP.tox

**Status:** Implemented (Phase 1A, 2026-11-22). The procedures below were executed on Windows 11 with TouchDesigner 2025.32440; evidence is in the [validation index](../icm/validation-index.md). Release packaging and signing are Phase 1C (item 24).

## Prerequisites
- **TouchDesigner 2025.32440** (the minimum and tested build) in its default install location. Set `TDFLOW_TD_BIN` to use a different `bin` folder.
- CPython 3.11 on `PATH` to run the tools. TouchDesigner's bundled Python runs the component itself.
- A logged-in desktop session: TouchDesigner needs its window and licence. The tools open **only** generated disposable projects.
- For the in-TD tests: ports 9480–9483 free, and Chrome installed (used headless with a throwaway profile). Edge's headless mode made no request on the test machine.

## 1. Build (`tools/build_tox`)
```
python -I tools/build_tox/build.py                       # -> build/TDflowMCP.tox + build/TDflowMCP.json
python -I tools/build_tox/build.py --check-reproducible  # builds twice, compares
```
- **Inputs:** `src/component/manifest.json`, which lists every module, DAT, callback and custom parameter. Text is LF-normalised.
- **Generator:** `tools/tdtoe.py` generates a disposable builder `.toe` under `.tmp/build_tox/` (no committed binary input, ADR-0007). `builder_runtime.py` then runs inside TouchDesigner. It:
  - constructs the `TDflowMCP` base COMP: embedded modules in `./src`, Setup page assets, troubleshooting data, the manifest, callbacks and custom parameters;
  - verifies every embedded text against its SHA-256;
  - checks that the extension initialised with `Status = Off` and no listener;
  - saves the `.tox` and quits.
- **Output:** `build/TDflowMCP.json` records the version, the source digest, the TD build used, the minimum build, the `.tox` SHA-256 and size, the build time, and the source commit and dirty flag. `build/` is git-ignored; development builds are **not** release artifacts.
- **Reproducibility:**
  - The **source digest** (SHA-256 over every embedded text and the parameter specification) is identical across clean builds.
  - The `.tox` **bytes differ** between builds. Expanding both with `toeexpand` shows the only differing file is `.build`, TouchDesigner's own build timestamp.
  - So verify content with `source_digest` or an expanded comparison, and identify a specific file with its SHA-256.
- **Load-time integrity:** the component re-verifies every embedded module against the manifest SHA-256 before importing it, and refuses to load on a mismatch.

## 2. Unit tests (no TouchDesigner)
```
python -m unittest discover -s tests -t .
"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\python.exe" -m unittest discover -s tests -t .
```

## 3. In-TouchDesigner integration tests (`tests/td`)
```
python -I tests/td/run_td_tests.py                       # full suite -> build/td-test-results.json
python -I tests/td/run_td_tests.py --mode real_browser   # opens your default browser once
python -I tests/td/run_td_mcp_tests.py                   # Phase 1B MCP acceptance -> build/td-mcp-test-results.json
python -I tests/td/run_td_gate_tests.py                  # audit-worker gate, storage, perf -> build/td-gate-test-results.json
python -I tests/td/run_td_onboard_tests.py               # 1C: live Claude Code + Skills -> build/td-onboard-test-results.json
python -I tests/td/run_td_dogfood.py [--tasks inspect,…]  # real Claude Code development sessions -> build/td-dogfood-results.json
```
- **Dogfood** (`run_td_dogfood.py`): five Claude Code sessions (inspect, edit/verify/undo, build and wire, DAT + session notes, boundaries) in a disposable project. The safety rules are the same as for onboarding. Token-redacted transcripts go to `.tmp/td-dogfood/`, and per-call tools, errors, turns and costs go to the results file. In-TD checks: final state, audit verification, no unaccounted executions, TDflowMCP internals untouched.
- **Gate suite** (`run_td_gate_tests.py`, five TouchDesigner launches, all under `.tmp/td-gate-tests/`):
  - `gate`: project-local storage decision; idle, load and simulated slow-disk frame impact (OnFrame cost and frame intervals) with MCP latency measured separately; the kill switch flipped while an intent is being written; queue overflow; an injected outcome-write failure.
  - `gate_crash`: load from 8 clients, then a hard kill of that TouchDesigner process (own child only).
  - `gate_restart`: recovery checks (chain and keyed checkpoints verify; every reported success is durable); then move/rename, copy and data-location flows via Save As.
  - `perf_baseline` and `perf_worker`: the same load against `--baseline` (default: a clean `e15ec55` build in `.tmp/wt-e15ec55/build/`) and the current build.
  - `gate_bench` (`--only gate_bench`): handoff costs inside TD (worker read/append, main-thread append, main-thread handoff).
  Private storage goes to `TDFLOW_PRIVATE_ROOT` under `.tmp`, so nothing is written to your real profile.
- **Onboarding suite** (`run_td_onboard_tests.py`; `--no-live` skips Claude Code): takes the Claude Code JSON entry from the generated "Tell Your Coding Agent" text into a temporary `--mcp-config` file and runs `claude -p` with `--strict-mcp-config` and an explicit `--allowedTools` list. The token is passed only in the child process environment. The suite never runs `claude mcp add` and never writes your Claude Code configuration. Skills go only to a disposable `TDFLOW_SKILLS_HOME` and the disposable project folder. Claude Code itself still keeps its usual session history for that run.
- **How it works:** the harness generates a disposable project whose runner (`td_runner.py`) loads the built `.tox` into `/project1` with `loadTox`, the same load path as dropping the file into a network. The runner drives the component only through its custom parameters, plus two test seams on the extension (`launcher`, `clipboard_writer`) and one injected fault. At named checkpoints the harness runs external probes: the OS socket table, LAN connects, HTTP, the gate matrix, headless Chrome and a secrets scan.
- **Safety:**
  - The harness aborts if a test port is busy.
  - It records the TouchDesigner processes already running and verifies afterwards that they are untouched. Only its own process tree is ever killed, and only on timeout.
  - It binds only `127.0.0.1`; the LAN address is only connected to.
  - It restores your clipboard text after the clipboard test.
- **`real_browser` mode:** uses the production launcher, and passes only if your browser actually loaded `/setup/`, `app.js` and `/setup/api/status`. The tab stays open after TouchDesigner quits.

## 4. Packaging (Phase 1C item 24)
```
python -I tools/package_release.py [--commit HEAD]
```
Builds from a **clean detached worktree** of the commit (never the working tree) on TouchDesigner 2025.32440. It refuses the package unless `TDflowMCP.json` reports that commit, not dirty, TD 2025.32440 and no embedded hash mismatch. The output is `dist/TDflowMCP-<version>-<commit>/` (`TDflowMCP.tox`, `TDflowMCP.json`, `INSTALL.md` from [install.md](install.md), `SHA256SUMS`) plus a `.zip`. `dist/` is git-ignored. `.dev` versions are development packages, not releases.

## 5. Spikes
`spikes/s4` (undo semantics), `spikes/s5`, `spikes/s6`. They are disposable and reproducible; see each README.
