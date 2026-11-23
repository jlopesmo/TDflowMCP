# Spike S9 — frames while the TouchDesigner window is minimized

**Question:** does TouchDesigner 2025.32440 keep running frames while its window is minimized? TDflowMCP's main-thread pump, kill-switch mirror and listener checks all run from a per-frame callback.

**Why now:** an in-TD suite stalled on 2026-11-23 while the user, working in another application, had minimized the test window. The stall watchdog showed the main thread with no Python frame. This matched the hang recorded twice before and never explained ([findings](../../docs/research/2026-11-23-s9-minimized-window.md)).

**Run:** `python -I spikes/s9/run_s9.py [run_dir]` (default `.tmp/s9`). It launches a disposable project, then minimizes and restores **that process's own window** without changing focus, and compares three counters over 4-second windows.

**Result (2026-11-23, TouchDesigner 2025.32440, Windows 11):**

| Window state | Root-timeline frames | Independent-time frames | Python thread ticks |
|---|---|---|---|
| Visible | 227 | 227 | 46 |
| Bottom of the z-order | 237 | 237 | 40 |
| **Minimized** | **0** | **0** | 25 |
| Restored (not activated) | 239 | 238 | 40 |

- Frames stop completely while minimized, also for a component with its own independent Time COMP (the S7 remedy for a paused timeline does not help here).
- Python threads keep running, more slowly.
- Frames resume immediately on restore.
- The "bottom of the z-order" row was measured while the test window was still the foreground window, so it does **not** prove that a fully covered window keeps running. Earlier suites passed while the user worked in other windows, which is consistent with it, but it was not measured.

The code here is disposable. It is never built or imported by the component.
