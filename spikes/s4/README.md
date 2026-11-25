# Spike S4: undo semantics (disposable spike code)

**Not TDflowMCP source.** Nothing here is built or imported by `src/`. Findings: [S4 record](../../docs/research/2026-11-22-s4-undo-semantics.md).

| File | Role |
|---|---|
| `run_s4.py` | Runs outside TD. Generates a disposable `s4.toe` with `tools/tdtoe.py`, launches TouchDesigner 2025.32440 with it, and waits for the results |
| `s4_runtime.py` | Runs inside TD, one step per frame: prepare, edit (with or without an undo block), undo, redo, undo |

To re-run: `python -I spikes/s4/run_s4.py .tmp/s4`. Results go to `.tmp/s4/s4_results.json` (git-ignored). Only the generated file is opened.
