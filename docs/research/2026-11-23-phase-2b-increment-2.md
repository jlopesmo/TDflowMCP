# Phase 2B increment 2: minimized-window checks, permission editing, staged code (2026-11-23)

**Owner decision D20 (2026-11-23):** accept `memory:repo:write`; improve the minimized-window handling; expand code-running capabilities one step at a time with approval, staging, audit, recovery tests and independent review; complete Phase 2B; then finish TDflowMCP ([roadmap D20](../icm/roadmap.md#owner-decisions-2026-11-22)).

## Delivered

### Minimized window: background checks (D20 item 2)
- While TouchDesigner runs no frames and its window is known to be minimized ([S9](2026-11-23-s9-minimized-window.md)), the transport's serve thread takes over the two safety checks the main thread normally drives:
  - **binding re-verification** every 10 s, on its own short-lived thread, with the same positive loopback-only evidence as before (OS socket table for this process, LAN connect probes, other owners of the port). Anything but a positive verdict, an error or an exception closes the listener; a slow probe never stalls accepting connections;
  - **the idle stop** (30 minutes).
- No TouchDesigner API is called from these threads. When frames resume, the controller reports the outcome: an idle stop as a normal stop, a failed re-verification as `BIND_UNVERIFIED`-style closure.
- **Idle counts meaningful activity only:** agent MCP requests and admin actions (POSTs, except the dashboard's live-tail search), and the user's pulses on the component. Page loads and the automatic polling of the Setup page and the dashboard no longer keep the listener alive. This applies with and without frames.
- Authorization, the kill switch and fail-closed behaviour are unchanged.

### Permission editing (D20 item 4)
- The dashboard's Agents tab lists each agent's grants and lets the user add or remove **allow** and **deny** grants (scope pattern, actions). An allow grant can only name actions the agent's role has; scopes must lie inside the project root; a deny grant wins over any allow.
- Each change is audited before it takes effect. The agent's policy version changes, so its pending approvals no longer match and must be asked again.

### Code-running capabilities with approval and staging (D20 item 3)
- **Code-DAT text** (`tdflow_set_dat_text` on a DAT TouchDesigner may run): approvable for the operator role. The user sees the complete new text; the approval also binds the hash of the text being replaced. Undo restores the durable snapshot.
- **Execute DATs** (`execute`, `chopexecute`, `datexecute`, `opexecute`, `parameterexecute`, `panelexecute`): creatable with approval, **created switched off**. The code is a second approval and switching it on (`active`) a third: changing `active` on an executable operator is now classified as execute. Creation fails if the operator cannot be left switched off.
- Unchanged: wiring into DATs, Script operators, network and file-system operator types and raw Python stay unavailable. In-process Python is not a sandbox: approved code runs with full access to TouchDesigner, and every A1 request says so.

### Event delivery (D20 item 4)
The dashboard **polls** the audit search every second for new events. This meets the ADR-0005 gate (events within 1 s) and needs no new transport code. A WebSocket or SSE stream would need long-lived connections in the bounded, thread-per-connection embedded server (at most 16), which competes with agent requests. Polling was tested in TouchDesigner; it is the chosen design.

## Independent review (review 14)
A security review of this increment found no high-severity issue. Its findings and what changed:

| # | Finding | Fix |
|---|---|---|
| 1 | Undo of a DAT text write restored into whatever DAT sat at the path, for example an Execute DAT holding the same text | Undo restores only into the same operator (id and type recorded); the text approval also binds the operator id |
| 2 | Only `active` counted as activation; callback toggles on an Execute DAT were plain updates | Every parameter of the six Execute DAT types is an execute change ("decides when its code runs") |
| 3 | A hung background re-verification never failed | A check still running after 5 s (the controller's evidence timeout) closes the listener |
| 4 | A failed verdict could be lost (non-atomic hand-over, ignored once frames resumed) | Hand-over under the transport lock; a failure is acted on even if frames resumed |
| 5 | `close_reason` was set after the closing flag | Reason set first |
| 6 | Unauthenticated pairing requests counted as activity | They no longer count; the user's pulse does |
| 7 | An Execute DAT that could not be staged off was left created | The check runs inside the undo block, so the create is rolled back |
| 9 | An expression on `active` lost its "expression" reason | Both reasons are listed |
| 10 | Which DAT texts are approvable was described too narrowly | [Security model](../architecture/security-model.md) corrected |
| 11 | Adding and removing a grant restored the old policy version, so old approvals became usable again | Any grant change cancels the agent's open approvals |

Finding 1 was verified by reading and is not reproduced in TouchDesigner (the swap needs a second principal); findings 2–7 and 11 have tests.

## Evidence
See the [validation index](../icm/validation-index.md): unit tests `test_not_cooking.py` (paused guard), `test_grants.py`, `test_approval_flow.py` (code DAT); in-TD checks `minimized.BINDING_RE_VERIFIED_IN_THE_BACKGROUND` and `code.*`.

## Limits
- The idle stop while minimized is unit-tested only (a 30-minute wait in TouchDesigner was not run).
