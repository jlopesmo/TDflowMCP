# TDflowMCP — Context

## Purpose

TDflowMCP lets AI agents work on TouchDesigner projects safely and productively. It is a TouchDesigner component that exposes an MCP server, enforces per-agent permissions, records everything agents do, and keeps the project's source in reviewable text form. See the [product vision](../product/vision.md).

## System boundary

**Inside TDflowMCP (proposed, see [ADR-0002](../decisions/0002-in-process-mcp-host.md)):** one `.tox` component running in the TouchDesigner process. It contains the MCP transport, policy engine, tool registry, execution layer, audit store, approvals queue and the dashboard's static files and API.

**Outside:**
- MCP clients and their models (Claude Code, Claude Desktop, Codex, Cursor and others).
- The TouchDesigner application and the user's project (`.toe`). TDflowMCP acts on the project only through policy-checked tools.
- Optional compatibility shims: a stdio bridge (deferred) for clients that cannot speak Streamable HTTP. It holds no policy logic.
- Upstream reference projects (TDMCP, Embody/Envoy and others). They are references only, not dependencies.

## Terminology

| Term | Meaning |
|---|---|
| **Component** / **the .tox** | The built, distributable `TDflowMCP.tox` |
| **Host project** | The user's `.toe` into which the component is dropped |
| **Agent** | An MCP client session authenticated with an agent token. Has an **agent identity** (id, display name, role) |
| **Scope** | A set of operator paths (glob patterns rooted in the host project, e.g. `/project1/scene/**`) to which a grant applies |
| **Grant** | `(agent or role, scope, actions ⊆ {create, read, update, delete, execute}, constraints)` |
| **Policy** | The ordered set of grants plus global settings. Evaluated server-side, deny by default |
| **Tool** | An MCP tool. Each declares its **required actions** and how its **target paths** are resolved |
| **Approval** | A human decision (dashboard) required before a pending call executes |
| **Audit event** | An immutable record of a request, its policy decision, outcome and undo handle |
| **Experiment** | A sandboxed copy or child COMP where an agent may work under relaxed policy, later promoted or discarded |
| **Code Mode** | Optional orchestration in which the agent submits a script against a scoped TD facade instead of many tool calls ([ADR-0004](../decisions/0004-code-mode-strategy.md)) |
| **Source tree** | Text files in `src/` that are the truth; the `.tox` is built from them ([ADR-0007](../decisions/0007-source-first-build.md)) |

## Platform facts (verified locally on 2026-11-22, no TD launched)

- Installed: `C:\Program Files\Derivative\TouchDesigner.2025.32440` (bundles `python311.dll`) and a legacy `TouchDesigner` install (bundles `python39.dll`).
- The 2025.32440 bundle includes `sqlite3`/`_sqlite3.pyd`, `ssl`, `hmac`, `secrets`, `asyncio`, `unittest`, `jsonschema` 4.23 and `pip` 24.2. It does **not** include `pydantic`, `starlette`, `uvicorn`, `anyio`, `httpx` or `mcp`.
- Upstream TDMCP and Embody both document **TD 2025.33230+** as their minimum. The local build is older. The minimum TD version for TDflowMCP is an open owner decision ([roadmap](roadmap.md#owner-decisions-pending)).
