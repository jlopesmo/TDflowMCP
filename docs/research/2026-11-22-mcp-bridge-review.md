# Research: original `mcp_bridge.tox` vs. TDflowMCP design

**Date:** 2026-11-22 · **Status:** Research record. It approves nothing by itself.

**Sources:**
- The owner-supplied `mcp_bridge.tox`, its audit and the extracted source, all preserved in [reference/historical/mcp_bridge](../../reference/historical/mcp_bridge/README.md).
- My own read of the extracted Python source (text only, **not executed**). TouchDesigner was not launched.

Evidence labels: **[A]** stated by the owner-supplied audit · **[S]** confirmed by me in the extracted source · **[I]** inference.

## 1. What the bridge is

- **[A][S]** A REST/HTTP automation bridge, not an MCP server. It has one Web Server DAT and a callback DAT with 13 routes, plus a global stdout redirect into a Table DAT. There is no JSON-RPC, no `initialize`/`tools/list`/`tools/call`, and no `/mcp` endpoint.
- **[S]** `build_metadata.txt`: saved with **TD 2025.32440**, Windows, 2026-04-02. The `.tox` SHA-256 matches the audit (`4386bfe5…`).
- **[S]** The Web Server DAT is saved with `active on` and `password ""`. No `localaddress` appears in the serialized parameters, so the bind address is unknown **[A]**. Leaving `localaddress` blank binds all interfaces ([Web Server DAT docs](https://docs.derivative.ca/Web_Server_DAT)), so the bridge may have been reachable from the LAN **[I]**.

## 2. Verified security findings (relevant to TDflowMCP requirements)

| Finding | Evidence | TDflowMCP response |
|---|---|---|
| No authentication or authorization; any path accepted | **[S]** `dispatch()` routes by URI only; no auth checks | Bearer identity + deny-by-default scopes ([ADR-0009](../decisions/0009-permission-model.md)) |
| Arbitrary Python via `/python/run` | **[S]** `exec(compile(code,'<mcp>','exec'), {'op','ops','run'})`. Builtins and `import` remain available, so the restricted namespace doesn't contain anything | Raw exec off by default, approval-gated, labelled policy-bypassing ([security model §5](../architecture/security-model.md#5-code-execution-honesty)) |
| **Code execution without `/python/run`** | **[S]** `/dats/create` creates `executeDAT`/`chopexecDAT`/`datexecDAT`/`parameterexecDAT`/`scriptDAT` and writes their text. `/dats/write` overwrites any DAT text | Confirms the Phase 0 review's H1: these effects are classified as `execute` ([§3.3](../architecture/security-model.md#33-action-classification-of-tool-effects-security-critical)) |
| Destructive operations without confirmation | **[S]** `o.destroy()`, `page.destroy()`, `p.destroy()`, `/textport/clear` | Delete requires `delete` grant; global approval rules; undo blocks |
| Global `sys.stdout` replacement | **[S]** `stdout_redirect.enable()` swaps `sys.stdout` project-wide and appends every line to a Table DAT | Never patch process-global streams. Capture output only per call, bounded and redacted |
| Unbounded responses | **[S]** Recursive tree, all parameters, and all log rows by default | Pagination, response caps, read filtering |
| HTTP 200 for every error | **[S]** `statusCode = 200` always; errors in the JSON body; method ignored | Typed MCP errors; correct HTTP status for gate failures |
| Runtime log persisted in the component | **[A]** `textport_log` holds historical rows saved into the `.tox` | Runtime logs live in `Datadir`, never in the `.toe`/`.tox` (P-9) |

## 3. Functionality worth carrying forward (as requirements, not code)

TDflowMCP does not port this code. The following capabilities are recorded as candidate tools, each to be designed behind policy:

| Bridge capability | Candidate TDflowMCP tool | Phase | Policy note |
|---|---|---|---|
| `/network/state` (with positions, bypass, errors) | `tdflow_list_operators` / `tdflow_get_operator` | 1B | read; paginated |
| `/params/get`, `/params/set` | `tdflow_get_parameters` / `tdflow_set_parameters` | 1B | value = update; expression = update + execute |
| `/dats/read` (table-aware rows) | `tdflow_get_dat_text` (+ table rows mode) | 1B | read; size-capped |
| `/dats/write`, `/dats/create`, `/dats/delete` | `tdflow_set_dat_text`, `tdflow_create_operator`, `tdflow_delete_operator` | 1B | executable DATs need execute |
| `/custompars/get/create/delete` | **New candidates:** `tdflow_get_custom_parameters`, `tdflow_edit_custom_parameters` | 2B (proposed) | update on the target COMP; deleting a page/parameter counts as `delete` |
| `/textport/read/clear` | **New candidate:** `tdflow_get_call_output` (stdout/stderr captured for the agent's own calls only) | 3 (with script execution) | No global redirect |
| `/python/run` | `tdflow_execute_python` (disabled by default) | 3 | execute on `/**`, approval |

## 4. The audit's architecture recommendation vs. ADR-0002

The audit recommends **"authenticated Derivative TDMCP → optional custom governance companion → TouchDesigner"**, with TDMCP as the primary transport and control foundation **[A]**. TDflowMCP's Conditionally Accepted [ADR-0002](../decisions/0002-in-process-mcp-host.md) instead builds its own in-process server. Both options are compared in [§5](#5-evaluation-tdmcp-as-foundation-vs-own-core), which is based on the official TDMCP and TDMCPSkills repositories.

## 5. Evaluation: TDMCP-as-foundation vs. own core

**Sources:** the official repositories [TouchDesigner/TDMCP](https://github.com/TouchDesigner/TDMCP) and [TouchDesigner/TDMCPSkills](https://github.com/TouchDesigner/TDMCPSkills), default branch `2025.30000`, documentation read 2026-11-22. No binaries were downloaded and nothing was tested hands-on.

### What TDMCP documents (**[D]**)
- 26 fixed tools. There is **no documented plugin, hook or middleware API**, no third-party tool registration, no per-tool disable or allowlist, no read-only mode, and **no way to disable `execute_code`** ([component.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/component.md)).
- Consent is all-or-nothing ("Allow full control"), with no scopes. Any local client that registers and is allowed gets full control. The self-protection fence does not cover `execute_code` ([security.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/security.md)).
- Its log records authentication and session events only, "never tool payloads". The only history is TD's undo stack (same sources).
- Its license is revocable (Terms §6.1) and brings indemnification duties for derivative works (§8.4) ([LICENSE.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/LICENSE.md)).

### Assessment of "TDMCP + governance companion" (**[I]**)
| Requirement | Achievable with a companion over TDMCP? | Why |
|---|---|---|
| Server-side CRUD scopes | **No (not enforceable)** | A proxy can filter only its own clients. Any local process can register directly with TDMCP and get full control, so the proxy is bypassable |
| Disable or gate raw code execution | **No** | `execute_code` cannot be turned off in TDMCP |
| Expression-as-execute classification | Only by proxy-side argument inspection | It is bypassable via direct access, as above |
| Per-call audit with payload digests | Only for proxied calls | Direct calls leave no payload record |
| Approvals, undo linkage, kill switch | Proxy-only | Same bypass |
| License stability | Risk | A revocable licence, with indemnification for derivative works |

**Conclusion:** a governance companion can't meet invariant 1 (server-side enforcement), so it fails a hard requirement. **ADR-0002 (own in-process core) is reaffirmed.** TDMCP remains the main **functional benchmark**. Phase 1B adds a comparison against TDMCP on the audit's scenarios, run per the owner's hands-on rule ([ADR-0008 §6](../decisions/0008-upstream-reuse-policy.md)): read a subtree, set a parameter, create and wire nodes, view errors, undo.

### Design convergences noted (independently derived; nothing copied)
TDMCP shows a copyable client command rather than writing client config files, uses a data-driven host registry with a `verified` stamp, and keeps a Skills install manifest so that it removes only the skills it installed ([setup-advanced.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/setup-advanced.md), [TDMCPSkills README](https://github.com/TouchDesigner/TDMCPSkills/blob/2025.30000/README.md)). These match ADR-0011's direction, which independently corroborates the approach. Differences in TDflowMCP:
- Skills are embedded, so installing needs no network. TDMCP fetches its Skills from the latest release.
- Skills versions are tied to the tool-surface version (see [agent integration §2](../architecture/agent-integration.md#2-skills-bundle)).
- Skills use the `tdflow-` prefix. TDMCPSkills reserves `td-`, so the two must coexist without touching each other's files.
