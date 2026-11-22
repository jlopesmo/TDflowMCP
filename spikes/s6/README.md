# Spike S6: embedded Python transport on TouchDesigner 2025.32440 (disposable spike code)

**Not TDflowMCP source.** Nothing here is built, packaged or imported by `src/`. The spike drives the **real** core modules (`src/tdflowmcp/net/pyhttp.py`, `bindverify.py`, `gate.py`, `oslisteners.py`) inside a disposable TouchDesigner project, so the [S6 findings](../../docs/research/2026-11-22-s6-embedded-python-transport.md) can be reproduced.

| File | Role |
|---|---|
| `build_toe.py` | Generates a disposable `s6.toe` from text with `toecollapse`: one boot Execute DAT, plus three Text DATs holding the Setup page assets. Needs an expanded **copy** of a Derivative sample at `<run dir>/learn/sample.toe.dir` (the S5 copy works) |
| `assets/` | The Setup page (`index.html`, `app.css`, `app.js`) that gets embedded in the Text DATs. CSP-compatible: no inline script |
| `s6_runtime.py` | Runs inside TD, from `onFrameStart` on the main thread. Sequence: baseline, then bind gate, then `VERIFYING` → `SERVING`, then a per-frame `pump()` with periodic re-verification, then restart, then close and confirm, then quit |
| `s6_harness.py` | Runs outside TD and launches TD with the disposable file only. 66 checks in `main` mode, 4 in `conflict` mode. On any exit it kills only the process tree it started |

**Spike-only control methods (`s6/*`, bearer token required).** These are `mark`, `slow`, `raise`, `touch`, `suspend`, `conflict`, `restart` and `finish`. They exist to drive the experiment and are **unsafe in production**: they can quit TD, block the main thread, edit operators without policy, and toggle the kill switch. Never copy them into `src/`.

**Token handling.**
- Each run generates a fresh bearer token and writes it to `results/ready*.json` for the harness.
- The harness deletes those files when it finishes.
- `results/` lives in the git-ignored run directory. `spikes/*/results/` is git-ignored too.

**Re-running:**
```
copy spikes/s6/* into .tmp/s6/          (git-ignored), plus .tmp/s5/learn -> .tmp/s6/learn
cd .tmp/s6 && python -I build_toe.py
set TDFLOW_SRC=<repo>/src
python -I s6_harness.py main            (then: python -I s6_harness.py conflict)
```
- Never point the scripts at a real project.
- Port 9480 must be free; the harness aborts otherwise.
- The conflict mode occupies `127.0.0.1:9480` itself, never a wildcard address.
- The headless browser check uses Chrome with a throwaway profile under the run directory. Edge's headless mode made no request on the test machine.
