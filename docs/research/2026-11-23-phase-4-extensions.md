# Phase 4: extensions and multi-agent work, plus Phase 3 snapshots (2026-11-23)

**Owner decision D20:** approved Phase 4, after Phase 3 ([roadmap D20](../icm/roadmap.md#owner-decisions-2026-11-22)). Roadmap row 4: visual analysis, debugging, optimization hints and scope leases, each behind policy with tests.

## Delivered
- **Optimization hints and debugging: `tdflow_describe_network`** (delivered with [Phase 3 increment 1](2026-11-23-phase-3-code-mode.md)). One network level in a single call, with wiring, errors, TouchDesigner's own cook times and heuristic hints.
- **Visual analysis: `tdflow_capture_top {path, format}`.** It returns the current image of a TOP the agent can read, as MCP image content (PNG or JPEG, up to 1920×1080 pixels and 8 MB).
  - TouchDesigner's own encoder runs on the main thread, which is why the size is bounded. The base64 encoding happens on the handler thread.
  - The image goes neither into the structured result, nor into the audit log, nor to a Code Mode script; the tool is not in the script facade.
  - It does not ask TouchDesigner to cook, but reading the pixels may let TouchDesigner finish a pending cook of that TOP. An operator that never cooked may show an empty image. At most one capture per second (review 17: main-thread cost). A readable TOP can show content from outside the agent's scope (a Select or Render TOP).
- **Scope leases: `tdflow_lease_scope {scope, ttl_s, release}`.**
  - An agent that may update a scope reserves it for 30 s to 30 minutes.
  - Other agents' changes, including undo, inside or above it are refused with `LEASED` (rule L2), naming the holder (and the scope only if the caller may read it). The check runs at planning and again right before execution, so a lease taken in between also stops the change. Reads stay open.
  - The whole project cannot be leased (L4), and one agent can renew a lease for at most an hour (`LEASE_LIMIT`).
  - Leases are audited and shown on the dashboard overview, where the user can end any lease. They end on release or expiry, and stop counting when the holder is disabled, loses its last token or loses update on the scope. They live in memory and do not survive a restart.
- **Snapshots (Phase 3): `inert: true` on `tdflow_experiment_create`.** The copy's cooking is switched off (`allowCooking`), and its executable operators are switched off like any copy. Promoting the snapshot replaces the current component with it and makes it cook again. That brings back its operators and parameters; executable operators stay switched off until they are switched on with approval, and failed rewiring is reported in `connections_lost`. The undo of that promote returns the snapshot, inert, and is verified only if it is inert again.
- **Code Mode benchmark** (ADR-0004 gate), measured in TouchDesigner on candidate `60d9a70d…`, one run. The same 20 parameter changes cost:

  | | Calls | Wall time | Bytes sent and read (arguments + results) |
  |---|---|---|---|
  | Separate calls | 20 | 1750 ms | 5613 |
  | One script | 1 (20 facade calls) | 1534 ms | 1978 |

  The script cost about 2.8× fewer bytes and 12 % less time, plus one approval by the user per distinct script. Bytes are not model tokens. No token saving is claimed beyond this measurement, and the per-call overhead (audit, main-thread steps) is the same either way.
- **Skill 1.5** describes experiments, snapshots, capture, leases and save.

## Independent review 17
| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Medium | Two agents could get overlapping leases at the same time and block each other | Check and write under one lock |
| 2 | Medium | `tdflow_undo` ignored leases | Undo is lease-checked like any change |
| 3 | Medium | A lease outlived token revocation and grant removal | A lease counts only while its holder has a live token and may update the scope |
| 4 | Medium | One agent could block the whole project indefinitely; the user could not clear a lease | Project-root lease refused; renewal capped at an hour; dashboard release (audited) with the holder's name |
| 5 | Medium | Snapshot docs overclaimed what promote restores | Wording fixed (executable operators stay off; wiring reported) |
| 6 | Low | Lease policy check without protection facts; `LEASED` named unreadable scopes | Protection facts added; scope named only if readable |
| 7 | Low | Capture: main-thread cost, "never cooks" unverified, content from outside the scope | One capture per second; claim softened; stated in the docs |
| 8 | Low | A failed inert restore on undo read as verified | Verification checks the inert state and the tag |
| Note | | A lease granted while another agent's checked change is already executing does not stop that change | Recorded under Limits |

## Evidence
See the [validation index](../icm/validation-index.md): unit tests `test_leases.py`; in-TD checks `capture.*`, `lease.*`, `snapshot.*` and `benchmark.CODE_MODE_MEASURED`.

## Limits
- **Leases** are cooperative coordination between agents, not a permission. A lease granted while another agent's already-checked change is executing does not stop that change. The user is never blocked by a lease; it can be ended on the dashboard, and the kill switch stops everything. They are not kept across restarts.
- **Snapshots live in the project** as inert components; file snapshots outside the project are not built. Turning cooking off stops cooking. It does not stop code that reacts to events, which is why copied executable operators are also switched off.
- **Capture** reads the image as it is. A TOP larger than 1920×1080 is refused; capture a resolution TOP after it instead.
- **Not built** from the Phase 4 row: host-project source-first (TDXN interop evaluation).
