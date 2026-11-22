# MCP tool surface (proposed)

**Status: Proposed.** No tool is implemented. Names use the `tdflow_` prefix so they don't collide with other TD MCP servers a client may have loaded. Every tool declares its required actions and target resolver, using the [action classification](security-model.md#33-action-classification-of-tool-effects-security-critical).

## Phase 1 core tools (14)

| Tool | Actions | Annotations | Purpose |
|---|---|---|---|
| `tdflow_whoami` | none | readOnly | Agent identity, role, effective scopes, server version, limits |
| `tdflow_project_info` | none (fixed summary) | readOnly | TD build, project name, top-level COMP names under the host root, FPS, cook state |
| `tdflow_list_operators` | read `path/*` | readOnly | Children with type and family, paginated; protected subtrees filtered |
| `tdflow_get_operator` | read `path` | readOnly | Type, flags, inputs/outputs, errors/warnings |
| `tdflow_get_parameters` | read `path` | readOnly | Values, modes, expressions, defaults (paginated by page name) |
| `tdflow_set_parameters` | update `path`; **+ execute** when setting an expression/bind/export mode | idempotent | Set values (and, with `execute`, expressions/modes). Returns previous values |
| `tdflow_create_operator` | create `parent/*`; **+ execute** for executable/network op types | | Create by type and name; optional position |
| `tdflow_delete_operator` | delete `path/**` | destructive | Delete. Global rules may require approval, which means deny in Phase 1 |
| `tdflow_connect` | update `dst`, read `src` | | Wire operators by connector index |
| `tdflow_disconnect` | update `dst`, read `src` | | Remove a wire |
| `tdflow_get_dat_text` | read `path` | readOnly | DAT text, size-capped |
| `tdflow_set_dat_text` | update `path`; **+ execute** for DATs TD executes | | Replace DAT text |
| `tdflow_get_errors` | read `path/**` | readOnly | Errors and warnings under a scope (filtered) |
| `tdflow_undo` | inverse actions on the audited targets | | Undo the agent's own last audited action, only while it is on top of TD's undo stack (else `UNDO_CONFLICT`) |

`tdflow_approval_status` is deferred to Phase 2b together with the approval flow.

## Later phases (candidates)

- **Phase 2:** `tdflow_search_operators`, `tdflow_get_docs` (offline operator and Python reference), `tdflow_cook_performance`, `tdflow_report` (session summary), `tdflow_approval_status`.
- **Phase 3:** `tdflow_experiment_create/promote/discard`, `tdflow_snapshot_create/restore`, `tdflow_execute_script` (Code Mode facade), `tdflow_search_api`/`tdflow_describe_api` (progressive disclosure), `tdflow_execute_python` (raw, disabled by default), `tdflow_save_project` (approval only).
- **Phase 4+ extensions:** `tdflow_x_capture_top` (visual analysis), `tdflow_x_trace_cook` (debugging), `tdflow_x_optimize_suggest`, multi-agent `tdflow_lease_scope`.

## Conventions

- Results return both human-readable `content` and machine-readable `structuredContent` with an `outputSchema`.
- Errors are tool results with `isError: true` and a stable `code` (`DENIED`, `NOT_FOUND`, `INVALID_ARGS`, `BUDGET_EXCEEDED`, `APPROVAL_UNAVAILABLE`, `UNDO_CONFLICT`, `AUDIT_UNAVAILABLE`, `SUSPENDED`, …). Protocol errors use JSON-RPC errors.
- Every mutating result includes `audit_seq` and `undo_label`.
- `tools/list` returns only the tools the calling agent has *some* grant for (progressive disclosure, fewer tokens). Policy is still enforced at call time.
