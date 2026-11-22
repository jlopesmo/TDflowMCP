# Spike S7: frame callbacks while the project timeline is paused (disposable spike code)

**Not TDflowMCP source.** It was raised by review 5 (M2). Findings are recorded in the [Phase 1A record](../../docs/research/2026-11-22-phase-1a-component.md#4a-spike-s7-paused-timeline).

`run_s7.py` (run outside TD) generates a disposable project with two COMPs, each counting `onFrameStart` calls:
- `plain`, which follows the root timeline;
- `local_time`, which has its own `local/time` Time COMP. That COMP's Execute DAT also drives the experiment: it pauses the root timeline for 3 s of wall time, then resumes it.

Observed on 2025.32440:
- **Without `independent`:** the local time COMP reported `/sys/local/time`, and both counters stopped after one call. The run was then ended by the launcher's timeout, which kills only its own process.
- **With `independent = True` and `play = True`:** during the pause `plain` advanced 1 frame and `local_time` advanced 141.

Re-run: `python -I spikes/s7/run_s7.py .tmp/s7`.
