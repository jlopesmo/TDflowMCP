# TouchDesigner `mcp_bridge.tox` — Static Architecture & Security Audit

**Date:** 2026-11-22  
**Source:** user-provided `mcp_bridge.tox` (SHA-256 `4386bfe5dacda95f7966d2df484dd0fdc0d01919e8f7c2c8fd70aafafc079891`)  
**Saved with:** TouchDesigner 2025.32440 on Windows, 2026-04-02  
**Method:** Offline binary container decoding; source/parameter review and Python syntax parsing only. **No TouchDesigner runtime tests**; no execution of embedded scripts; original file unchanged.

## Executive finding

The provided component is a **TouchDesigner REST/HTTP automation bridge**, not yet an MCP protocol server. It has 5 internal TouchDesigner DAT operators, 13 HTTP routes, and three embedded Python scripts. The core callback source is 447 lines. It has valuable CRUD/introspection functionality, but it lacks MCP transport/discovery, enforceable access scopes, write confirmations, and replay-safe transactional controls.

**Security disposition: do not activate this revision on a production/valuable project or expose it beyond an isolated test environment.** Its WebServer DAT is saved active with an empty password, no app-level authentication/authorization is present in the handler, and `/python/run` permits arbitrary Python execution in the TouchDesigner process.

## Recovered component structure

| File or operator | Purpose | Relevant evidence |
|---|---|---|
| `mcp_bridge/init` | Execute DAT start/exit callbacks | calls stdout redirect enable/disable |
| `mcp_bridge/webserver1` | WebServer DAT | `active on`, callback `webserver_callbacks`, password `""` |
| `mcp_bridge/webserver_callbacks` | HTTP request dispatcher | Routes listed below; ignored HTTP method |
| `mcp_bridge/stdout_redirect` | Python stdout wrapper | replaces global `sys.stdout` and logs to table |
| `mcp_bridge/textport_log` | Persistent Table DAT log | includes historical logs; excluded from source export |

The embedded scripts are linked to external relative paths via `syncfile on`: `mcp/init.py`, `mcp/stdout_redirect.py`, and `mcp/webserver_callback.py`. Their on-disk presence and synchronization semantics should be tested in a copied TouchDesigner project.

## The 13 routes

| Route | Function | Classification |
|---|---|---|
| `/network/state` | Returns operators/tree; positions, bypass, errors | Read |
| `/params/get` | Reads operator parameters | Read |
| `/params/set` | Changes parameter values | Write |
| `/dats/read` | Reads text or rows from DAT | Read |
| `/dats/write` | Overwrites DAT text | Write |
| `/dats/create` | Creates DAT child | Write |
| `/dats/delete` | Destroys DAT | Destructive |
| `/custompars/get` | Reads custom parameters | Read |
| `/custompars/create` | Adds custom parameters or pages | Write |
| `/custompars/delete` | Destroys custom parameters or pages | Destructive |
| `/textport/read` | Reads captured output rows | Read |
| `/textport/clear` | Removes saved output rows | Destructive |
| `/python/run` | `exec(compile(code, '<mcp>', 'exec'), ...)` | Privileged execution |

### Protocol gap

The uploaded component does not declare MCP `initialize`, `tools/list`, or `tools/call`; it does not parse or emit JSON-RPC envelopes or host an `/mcp` streamable-HTTP endpoint. The callback treats request paths as REST-style routes. **It is not discoverable by a standard MCP client without an adapter.**

## Risk assessment — priorities

| Priority | Issue | Evidence / impact | Suggested correction |
|---|---|---|---|
| **P0** | Privileged code execution without a policy gate | `/python/run` executes arbitrary Python; no authorization in source | Disable by default, opt-in scoped permission and human approval; prefer official TDMCP's authorization infrastructure |
| **P0** | Missing authorization / coarse write scope | Routes accept arbitrary operator paths; no root allowlist/read-write tags; WebServer password empty | Authentication, loopback binding/origin checks, root scopes, protected operators, per-action policy |
| **P1** | No MCP protocol | REST routes only | Adopt official TDMCP or build a deliberately limited compliant adapter |
| **P1** | Destructive changes lack snapshots/approval | DAT and custom-parameter deletion call `destroy()` with no confirmation; overwrites unrestricted | Preview/diff/approval; undo groups, backup for irreversible ops, explicit test project |
| **P1** | Global stdout replacement | Wraps `sys.stdout`, affecting entire project, writes each line to a Table DAT | Scoped bounded logging, redaction, rate/size limits, robust cleanup |
| **P1** | Unbounded read responses | Recursive tree, all parameters and DAT data, full saved log with no default cap | Pagination, limits, token-efficient summaries and selective reads |
| **P1** | Error contract weak | Callback returns HTTP 200 for unknown routes/exceptions; error as JSON body | Consistent status and typed, structured MCP-compatible errors |
| **P2** | No read/write action batching or transactional execution | One route per operation, no version compare or conditional update | Plan→validate→stage→apply→verify→rollback; scoped batches |
| **P2** | Synchronization fragile | Embedded DAT content plus `syncfile on` to relative paths | Verify on clean copied project; externalization strategy; source-controlled docs |
| **P2** | Limited introspection | No wiring, signal values, pixel capture, profiling, doc lookup or multi-instance routing | Avoid reimplementation: reuse official TDMCP features where possible |

**Important correction:** The earlier discussed `readable`/`writable` tags, per-node permissions, dry-run controls, AI provider settings and explicit approval workflows were **not found in this uploaded `.tox`**. Those were possible design ideas, not verified implemented features in this file.

## Comparative implementation strategy

| Candidate | Documented strength | Recommended use |
|---|---|---|
| [Derivative TDMCP](https://github.com/TouchDesigner/TDMCP) | Official in-process MCP; authenticated consent, loopback defaults, documentation tools, undo, images, code execution | **Primary transport/control foundation** |
| [Embody / Envoy](https://github.com/dylanroscover/Embody) | Efficient subtree snapshots (`read_tdn`), batches, TDXN versionable network exports | Borrow snapshot/diff and network-as-text ideas |
| [MindDesigner / Pantani tdmcp](https://github.com/Pantani/tdmcp) | Operator knowledge, creation→verification→preview loop | Borrow outcome-oriented workflows and operator knowledge |
| [TwoZero TD MCP](https://github.com/twozero-team/twozero-td-mcp) | Multi-instance/creative workflows | Compare instance selection and workflow needs |
| Uploaded `mcp_bridge.tox` | Small explicit REST API; DAT CRUD, custom parameter helpers, local stdout capture | Preserve as reference and evaluate narrowly differentiated functions |

The documented features above come from project documentation, not live comparative benchmarks. Do not assume their licenses are interchangeable. Derivative TDMCP is published under a Shared Use License and cannot be redistributed as a product without checking its terms.

## Proposed target architecture

AI agent (Codex / Claude Code) → authenticated Derivative TDMCP → **optional custom governance companion** → TouchDesigner operation. Read-only inspection is default. Mutating requests require scope + validation and, for destructive/high-impact operations, an approval checkpoint. Bounded batch execution returns concise structured results. Build/check loops use output captures, errors, and cook-time comparisons. Store recoverable network snapshots/diffs before broad edits.

### Governance/ICM checklist for any development agent

1. Identify the correct source repository and read its authoritative architecture, security, test, and ICM documents before changing code.
2. Keep the uploaded `.tox` untouched as a reference. Make all experiments in an isolated copy/test `.toe` or feature branch; never edit valuable live-show output.
3. First benchmark official TDMCP against concrete cases: read subtree, change parameter, create+wire nodes, capture visual output, diagnose cook hotspots, undo and restore.
4. Compare custom REST endpoints with official tools; prove a real gap before reusing or porting the code.
5. Add focused tests for authorized/unauthorized paths, deletion refusal, input validation, bounded output, error propagation and rollback. Preserve all approval gates.
6. Update affected ICM documentation and verify it matches working code and tests before claiming completion. No production activation or merge without approval.

## Open validation items

- Whether the saved WebServer DAT binds only loopback or additional interfaces (not explicit in serialized parameters).
- Whether embedded `syncfile on` source DATs load correctly without the external `mcp/` files.
- Whether TouchDesigner API methods called by handlers behave as intended on the installed TouchDesigner version.
- Whether original code was previously registered with a separate MCP adapter/server, which was not supplied with the `.tox`.
- Runtime performance/security behavior under test loads.
