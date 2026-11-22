# TDflowMCP — Product vision and functional architecture

**Status:** Mission statement **Accepted** (owner brief, 2026-11-22). Feature breakdown and priorities below are **Proposed** until the owner confirms them in [ADR-0001](../decisions/0001-product-scope.md).

## Mission (owner brief, verbatim intent)

TDflowMCP is a **source-first, AI-powered TouchDesigner development environment** combining MCP, Code Mode, a locally hosted browser dashboard, enforceable permissions, audit logging, and intelligent agent workflows.

## Who it serves

- **The TouchDesigner developer (owner/operator).** They want AI agents to inspect, build and debug networks while keeping control of what agents may touch, seeing what they did, and undoing it.
- **AI agents** (Claude Code, Claude Desktop, Codex, Cursor and other MCP clients). They need a reliable, discoverable and efficient tool surface. Phase 1 targets clients that can send an `Authorization` header (Claude Code first). Claude Desktop needs the deferred stdio shim or OAuth ([system design §7](../architecture/system-design.md#7-deployment)).
- **Reviewers and collaborators.** They read audit reports and source diffs instead of opaque `.toe` binaries.

## Functional pillars (owner-required)

| # | Pillar | What it means for the user | Proposed first delivery |
|---|---|---|---|
| P1 | Reusable `.tox` component | Drop `TDflowMCP.tox` into any project and toggle it on | Phase 1 |
| P2 | Standard MCP + optional Code Mode | Any MCP client connects. Agents can optionally batch work as a script against a scoped TD API | MCP: Phase 1; Code Mode: Phase 3 |
| P3 | Local browser dashboard | See sessions, agents, live activity and pending approvals; manage policy | Read-only: Phase 2; approvals: Phase 2 |
| P4 | Server-side CRUD permissions, scopes, identity | Each agent has an identity and may only create/read/update/delete within approved operator scopes | Phase 1 (core enforcement) |
| P5 | Monitoring, audit, reporting, approvals | Every call is recorded. Risky calls wait for human approval. Reports can be exported | Audit: Phase 1; approvals/reports: Phase 2 |
| P6 | Safe execution, experiments, undo, recovery | Changes are undoable. Experiments run in sandboxed COMPs and can be discarded. Snapshots allow recovery | Undo: Phase 1; experiments/snapshots: Phase 3 |
| P7 | Source-first dev, packaging, testing | The repo's text source is the truth. The `.tox` is built and tested automatically | Phase 1 (build + test harness) |
| P8 | Extensibility | Plug-in tool modules for visual analysis, debugging, optimization and multi-agent workflows | Extension contract: Phase 1; modules: Phase 4+ |

## Non-goals (proposed)

- Not a cloud service. Everything runs on the user's machine unless the owner later approves otherwise.
- Not a fork or redistribution of Derivative TDMCP or Embody/Envoy (see [ADR-0008](../decisions/0008-upstream-reuse-policy.md)).
- Not an AI model host. TDflowMCP exposes tools; the agent and model live in the client.
- No security promises based on prompts or client-side settings. Enforcement lives in the TDflowMCP server.

## Success criteria (proposed, measurable)

1. A fresh TouchDesigner project gains an MCP endpoint by dropping in one `.tox`, with no extra installs.
2. An agent whose token only allows reading `/project1/scene` cannot modify or read outside that scope. Tests prove the denial, and the audit log records it.
3. Every mutating call is attributable to an agent identity, can be undone, and is visible in the dashboard within 1 s.
4. The `.tox` is reproducibly built from repository source, and the test suite runs inside TouchDesigner with a machine-readable result.

## Functional architecture summary

See [system design](../architecture/system-design.md) for the component diagram. In short:

```
MCP client ──HTTP──▶ [TDflowMCP.tox inside TouchDesigner]
                      ├─ Transport (Web Server DAT, MCP Streamable HTTP)
                      ├─ Identity + Policy Engine (deny by default)
                      ├─ Tool Registry (core tools + extension modules + optional Code Mode)
                      ├─ Execution layer (main thread, undo blocks, experiments)
                      ├─ Audit store (SQLite, append-only) + approvals queue
                      └─ Dashboard (static HTML/JS served by the same component)
```
