# MCP tool surface

**Status:** the 14 core tools plus `tdflow_session_note` and `tdflow_session_list` are **implemented in Phase 1B** (tool-surface version `1`). They are registered in [`src/tdflowmcp/tools.py`](../../src/tdflowmcp/tools.py), executed by [`src/td_adapter/tdops.py`](../../src/td_adapter/tdops.py), and verified in TouchDesigner 2025.32440 by the Phase 1B acceptance suite. They follow the Conditionally Accepted [ADR-0009](../decisions/0009-permission-model.md). Later-phase tools remain proposals. Names use the `tdflow_` prefix so they don't collide with other TD MCP servers a client may have loaded. Every tool declares its required actions and target resolver, using the [action classification](security-model.md#33-action-classification-of-tool-effects-security-critical).

## Phase 1B core tools (14)

| Tool | Actions | Annotations | Purpose |
|---|---|---|---|
| `tdflow_whoami` | none | readOnly | Agent identity, role, effective scopes, `server_version`, `tool_surface_version`, `expected_skill_version` (`1.0` since 1C; the bundled Skills compare it), limits |
| `tdflow_project_info` | none (fixed summary) | readOnly | TD build, project name, top-level COMP names under the host root **filtered to those the agent can read**, FPS, cook state |
| `tdflow_list_operators` | read `path/*` | readOnly | Children with type, family, readable `inputs` and node position `x`/`y` (dogfood 1), paginated; protected subtrees filtered |
| `tdflow_get_operator` | read `path` | readOnly | Type, flags, inputs/outputs, errors/warnings, `x`/`y`, `cook_count` (errors exist only after a cook) |
| `tdflow_get_parameters` | read `path` | readOnly | Values, modes, expressions, defaults; optional `page` and `names` (≤ 50) filters; paginated. **Note (review 8):** values are read with `eval()`, so expression and bind parameters are evaluated (the user's own expressions; agents cannot set them). That may cook upstream operators, which is a hypothesis to verify; an `evaluate: false` option is a candidate |
| `tdflow_set_parameters` | update `path`; **+ execute** for `expressions` and Python-style parameters (denied in Phase 1) | idempotent | Set constant values; returns previous values. Parameter denylist (extensions, callbacks, external tox, scripts…). Pulses not supported |
| `tdflow_create_operator` | create `parent/*`; **+ execute** for executable/network op types | | Create by type and name; optional position |
| `tdflow_delete_operator` | delete `path/**` | destructive | Delete. Global rules may require approval, which means deny in Phase 1 |
| `tdflow_connect` | update `dst`, read `src` | | Wire operators by connector index |
| `tdflow_disconnect` | update `dst`, read `src` | | Remove a wire |
| `tdflow_get_dat_text` | read `path` | readOnly | DAT text, size-capped |
| `tdflow_set_dat_text` | update `path`; **+ execute** unless the DAT is data (Table DAT, or Text DAT tagged `tdflowdata` by the user) | | Replace DAT text. Undo via a durable TDflowMCP snapshot (TD's undo does not cover DAT text, S4) |
| `tdflow_get_errors` | read `path/**` | readOnly | Errors and warnings under a scope (filtered), plus `never_cooked` operators whose errors are unknown (with total and truncation flags; this tool does not cook anything) |
| `tdflow_undo` | inverse actions on the audited targets | | Undo the agent's own newest audited change, only if TD's undo stack is exactly as it was right after that change (S4b), else `UNDO_CONFLICT`. Reports `verified` after checking the restored state |

`tdflow_approval_status` is deferred to Phase 2B together with the approval flow.

## Continuity and memory tools

| Tool | Phase | Actions | Purpose |
|---|---|---|---|
| `tdflow_session_note` | 1B | none (own session) | Append an agent note to the current session log |
| `tdflow_session_list` | 1B | none (own sessions) | The agent's recent sessions; admins see all in the dashboard |
| `tdflow_task_checkpoint` | 2A | update `memory:checkpoint` | Record a checkpoint with server-attached facts |
| `tdflow_handoff_create` | 2A | update `memory:handoff` | Create a verified handoff (facts and tagged claims) |
| `tdflow_handoff_resume` | 2A | read `memory:handoff` | Latest handoff, activity since it, fingerprint drift |
| `tdflow_memory_get` / `tdflow_memory_list` | 2A | read `memory:<type>` | Read project ICM, decisions, tasks |
| `tdflow_memory_put` | 2A | update `memory:<type>` | Write a record with `based_on_rev` (`MEMORY_CONFLICT` if stale) |

## Later phases (candidates)

- **Phase 2B:** `tdflow_describe_network` (bounded, read-only network summary; dogfood 1), `tdflow_get_custom_parameters`, `tdflow_edit_custom_parameters` (capabilities from the original bridge; deleting a page or parameter counts as `delete`), `tdflow_search_operators`, `tdflow_get_docs` (offline operator and Python reference), `tdflow_cook_performance`, `tdflow_report` (session summary), `tdflow_approval_status`.
- **Phase 3:** `tdflow_experiment_create/promote/discard`, `tdflow_snapshot_create/restore`, `tdflow_execute_script` (Code Mode facade), `tdflow_search_api`/`tdflow_describe_api` (progressive disclosure), `tdflow_execute_python` (raw, disabled by default), `tdflow_save_project` (approval only), `tdflow_get_call_output` (stdout/stderr of the agent's own script calls; no global redirect).
- **Phase 4+ extensions:** `tdflow_x_capture_top` (visual analysis), `tdflow_x_trace_cook` (debugging), `tdflow_x_optimize_suggest`, multi-agent `tdflow_lease_scope`.

## Conventions

- **Versioning:** additive, backward-compatible changes (new optional arguments, new output fields) do not change `tool_surface_version`. Removing or renaming a tool, argument or field, or changing a meaning, bumps it.

- Results return both human-readable `content` and machine-readable `structuredContent` with an `outputSchema`.
- Errors are tool results with `isError: true` and a stable `code` (`DENIED`, `NOT_FOUND`, `INVALID_ARGS`, `BUDGET_EXCEEDED`, `APPROVAL_UNAVAILABLE`, `UNDO_CONFLICT`, `AUDIT_UNAVAILABLE`, `SUSPENDED`, `MCP_DISABLED`, `MEMORY_CONFLICT`, …). Protocol errors use JSON-RPC errors.
- Every mutating result includes `audit_seq` and `undo_label`.
- `tools/list` returns only the tools the calling agent has *some* grant for (progressive disclosure, fewer tokens). Policy is still enforced at call time.
