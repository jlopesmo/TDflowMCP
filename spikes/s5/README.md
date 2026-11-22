# Spike S5: compatibility of TouchDesigner 2025.32440 (disposable spike code)

**Not TDflowMCP source.** This code is not built, packaged or imported by `src/`. It exists so the [S5 findings](../../docs/research/2026-11-22-s5-compatibility-2025.32440.md) can be reproduced.

| File | Role |
|---|---|
| `build_toe.py` | Generates a disposable `s5.toe` from text with Derivative's `toecollapse`. It needs an expanded **copy** of a Derivative sample at `.tmp/s5/learn/sample.toe.dir` |
| `s5_runtime.py` | Runs inside TD: probes runtime, libraries, parameters and clipboard; Web Server DAT `VERIFYING → SERVING`/`CLOSED` gate |
| `s5_server.py` | Web Server DAT callback logic. Returns 503 until the gate passes |
| `s5_bindcheck.py` | Reads the OS listener table (`GetExtendedTcpTable` via `ctypes`), runs non-loopback probes, and returns a verdict |
| `s5_harness.py` | Runs outside TD: launches TD with the disposable file only, probes, and kills only its own PID on timeout |

**Rules for re-running:**
- Copy these files to `.tmp/s5/` (git-ignored) and run there.
- Never point the scripts at a real project.
- Set `TDFLOW_S5_NO_WS=1` to skip the Web Server DAT entirely.
- On builds without `localaddress`, the full run briefly exposes a listener on all interfaces before the gate closes it. Do not run the full mode on such builds again unless a new question requires it.
