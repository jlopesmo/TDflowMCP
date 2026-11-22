# TDflowMCP Phase 1B vs. Derivative TDMCP: functional comparison (item 18a)

**Date:** 2026-11-22
**Method:** documentation and architecture comparison only.
- **Sources:** the official repository [TouchDesigner/TDMCP](https://github.com/TouchDesigner/TDMCP): README, releases page, and `docs/component.md` at ref `2025.30000`. Read on 2026-11-22.
- **TDMCP version described:** latest release 1.1.58.
- **Licence:** Shared Use License (`LICENSE.md`), "Do not redistribute TDMCP itself as a product" (ADR-0008: reference only).

**Hands-on comparison: not performed.**
- TDMCP states "Requires TouchDesigner 2025.33230 or newer", and its 1.1.57 notes say "Earlier builds will not open this component".
- The supported environment here is **2025.32440**, and the owner ruled out an upgrade.
- Per ADR-0008 §6 a binary is downloaded only when hands-on testing needs it. Since TDMCP cannot open on this build, **nothing was downloaded**.
- A side-by-side run needs a machine with 2025.33230+; it is listed as a Phase 1C/2 follow-up.

No TDMCP code, schema, prompt or skill was read for reuse or copied.

## Workflow comparison

| Workflow | TDMCP (documented) | TDflowMCP Phase 1B (verified in TD 2025.32440) |
|---|---|---|
| Inspect operators | `list_operators`, `search_operators`, `get_operator_info`, `get_connections`, `get_errors`, `inspect_values`, `view_operator` | `tdflow_list_operators` (paginated, filtered), `tdflow_get_operator` (flags, inputs/outputs, errors), `tdflow_get_errors`. No search or visual capture yet (2B/4) |
| Create | `create_operator`, `build_network` (several operators and wires in one call) | `tdflow_create_operator` (one per call; executable/network types need `execute`, which is denied in 1B) |
| Wire | `wiring` (connect/disconnect) | `tdflow_connect`, `tdflow_disconnect` (update on the destination, read on the source) |
| Parameter editing | `set_parameters`, `pulse_parameter`, `edit_custom_parameters` | `tdflow_set_parameters`: constants only. Expressions and Python-style parameters are classified as `execute` and denied. A parameter denylist covers extensions, callbacks, external tox, scripts and similar. No pulses or custom-parameter editing in 1B |
| DAT text | `get_dat_content`, `set_dat_content` | `tdflow_get_dat_text`, `tdflow_set_dat_text`. Writing is allowed only on data DATs (Table DATs, or Text DATs the user tagged `tdflowdata`); everything else needs `execute` |
| Diagnostics | `get_errors`, `get_cook_performance`, `project_info` | `tdflow_get_errors`, `tdflow_project_info` (filtered to readable components). No cook-performance tool yet (2B) |
| Code execution | `execute_code` (runs Python in TD; "not fenced", 1.1.57) | **None.** No arbitrary Python in Phase 1B; raw execution stays disabled and approval-gated in later phases |
| Undo | "Everything it does lands in TouchDesigner's undo stack, so Ctrl+Z reverses it" | A named undo block per call. `tdflow_undo` undoes only the agent's **own newest** change, refuses (`UNDO_CONFLICT`) if anything else happened since (a human edit, another agent, a reload), and **verifies** the restoration. DAT text, which TD's undo does not cover (S4), uses a durable snapshot |
| Save project | `save_project` | Not offered in Phase 1 |

## Security and governance comparison

| Aspect | TDMCP (documented) | TDflowMCP Phase 1B |
|---|---|---|
| Bind address | Localhost by default; an option for "All interfaces (LAN/remote)" | Literal `127.0.0.1` only, exclusive bind, verified from the OS table before serving, re-verified every 10 s |
| Authentication | OAuth 2.1 with an in-TD consent panel; 24 h tokens | Per-agent bearer tokens (`tdf_<id>.<secret>`, salted SHA-256), created and revoked only from a browser session **approved inside TD** |
| Authorization | Consent per client; no per-path scopes documented | Server-side deny-by-default **CRUD+execute scopes** on operator paths, plus non-overridable hard denies: TDflowMCP instances, their ancestors' flags, delete, rename and extensions, `tdflowProject`, `/sys`, `/local`, `/ui`, and everything outside the project root |
| Destructive actions | Only `delete_operator` asks for confirmation (unless auto-accepted) | Delete is a separate grant. Deletes of more than 50 operators need approval, which is unavailable in 1B, so they are denied |
| Audit | Not documented | Durable SQLite hash chain with intent and outcome events, keyed checkpoints and tamper detection. **Fail closed**: no audit, no execution |
| Kill switch | Revoke Access ends sessions | `Suspendagents` rejects every call, **including requests already queued**, and is audited |
| Self-protection | Tools refuse to modify the TDMCP component itself | Hard deny on every TDflowMCP instance and its subtree (also for reads), on its ancestors' delete, rename, flags and extensions, and on reserved tags |
| Protocol | Streamable HTTP | Streamable HTTP, **2026-07-28 stateless** and the legacy `initialize` eras |
| Minimum TD build | 2025.33230 | 2025.32440 |
| Extra processes | Node.js only for the Claude Desktop plugin route | None |

## Takeaways for later phases
- TDMCP's broader surface (`build_network`, `pulse_parameter`, custom parameters, cook performance, view capture, search, docs) marks the functional gap. The Phase 2B/3/4 candidates in `tool-surface.md` cover it, each behind the same classification and audit.
- **Not to adopt:** TDMCP's unfenced `execute_code` and the LAN bind option. Both contradict TDflowMCP's invariants.
- **Open:** a side-by-side latency and functionality benchmark on a machine with TouchDesigner 2025.33230+.
