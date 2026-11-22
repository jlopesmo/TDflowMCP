# ADR-0002 — Host the MCP server inside TouchDesigner (Web Server DAT)

**Status:** Proposed (2026-11-22).

## Context
The owner asked not to assume a separate Node.js server is necessary. MCP 2026-07-28 Streamable HTTP is stateless per-POST JSON-RPC ([spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)). TouchDesigner's Web Server DAT serves HTTP and WebSocket with an `onHTTPRequest` callback and a `localaddress` bind parameter ([docs](https://docs.derivative.ca/Web_Server_DAT)). TDMCP (Derivative) and johnsabath/touchdesigner-mcp host MCP in-process. Envoy does too, via a uvicorn worker thread.

## Decision
Run the MCP endpoint, dashboard API and static dashboard from **one Web Server DAT inside the TDflowMCP component**, bound to `127.0.0.1`. All policy and audit live in that process, next to the TD state they protect. A stdio shim for stdio-only clients is **deferred**. If built, it must be a policy-free byte forwarder (stdlib Python, no Node required).

## Alternatives
Separate Node server; official SDK in a separate process; raw-socket thread in TD. See [technology stack](../architecture/technology-stack.md#alternatives-considered).

## Consequences
- No extra runtime install. A single process holds the truth.
- Server availability equals TD availability: when TD is closed or stalled, MCP is down. Acceptable for a dev tool. A shim could report "TD unavailable" later.
- Unknowns to resolve in the Phase 1 spike S1: callback thread, maximum body size, ability to defer responses or stream SSE, behavior under heavy cook load, and bind behavior on TD < 2025.33060 (TDMCP notes its loopback bind fix requires 2025.33060+).

## Validation
Spike S1 ([Phase 1](../milestones/phase-1-core-server.md)) must show: a request/response round-trip with Claude Code against the local build, a verified loopback-only bind (a connection via a non-loopback interface is refused), and measured overhead.
