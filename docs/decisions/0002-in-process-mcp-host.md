# ADR-0002 — Host the MCP server inside TouchDesigner (Web Server DAT)

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to spike S1. Revisions: default port 9480 with conflict detection; provisional target TD 2025.33230+.

## Context
The owner asked not to assume a separate Node.js server is necessary. MCP 2026-07-28 Streamable HTTP is stateless per-POST JSON-RPC ([spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)). TouchDesigner's Web Server DAT serves HTTP and WebSocket with an `onHTTPRequest` callback and a `localaddress` bind parameter ([docs](https://docs.derivative.ca/Web_Server_DAT)). TDMCP (Derivative) and johnsabath/touchdesigner-mcp host MCP in-process. Envoy does too, via a uvicorn worker thread.

## Decision
Run the MCP endpoint, the Setup Assistant ([ADR-0010](0010-setup-assistant-and-component-parameters.md)) and the later governance dashboard from **one Web Server DAT inside the TDflowMCP component**. It listens only after a user action, bound to `127.0.0.1`, and serves application requests only after the effective loopback-only binding is verified. Failure closes the listener immediately ([bind-verification gate](../architecture/security-model.md#82-bind-verification-gate)). All policy and audit live in that process, next to the TD state they protect. A stdio shim for stdio-only clients is **deferred**. If built, it must be a policy-free byte forwarder (stdlib Python, no Node required).

## Alternatives
**Re-evaluated 2026-11-22: "official TDMCP + governance companion"**, as recommended by the owner-supplied bridge audit. It was rejected because TDMCP has no scopes, no plugin/hook API and no way to disable `execute_code`, and any local client can register for full control, so a companion cannot enforce policy. See the [bridge review §5](../research/2026-11-22-mcp-bridge-review.md#5-evaluation-tdmcp-as-foundation-vs-own-core). TDMCP remains a functional benchmark.

Separate Node server; official SDK in a separate process; raw-socket thread in TD. See [technology stack](../architecture/technology-stack.md#alternatives-considered).

## Consequences
- No extra runtime install. A single process holds the truth.
- Server availability equals TD availability: when TD is closed or stalled, MCP is down. Acceptable for a dev tool. A shim could report "TD unavailable" later.
- Unknowns to resolve in the Phase 1 spike S1: callback thread, maximum body size, ability to defer responses or stream SSE, behavior under heavy cook load, and bind behavior on TD < 2025.33060 (TDMCP notes its loopback bind fix requires 2025.33060+).


## Validation gates
- **S1** confirms the callback thread, body limits, response deferral/SSE behaviour, and a verifiable loopback-only bind on 2025.33230+.
- **S5 (executed 2026-11-22, [findings](../research/2026-11-22-s5-compatibility-2025.32440.md)):** on 2025.32440 the Web Server DAT has **no `localaddress`** and binds `0.0.0.0`. The bind gate detected this and closed the listener in about 0.5 s without serving anything. **Result: 2025.32440 cannot satisfy the security requirements.** TDflowMCP never activates the listener on builds without `localaddress` (pre-activation step 0). Callback execution on the main thread was confirmed. No deferred-HTTP API appears in the DAT's attribute listing (an inference, not a call test); WebSocket push methods exist.
- Port 9480 conflicts are detected and reported. The port is never silently changed.
- If S1 fails, this ADR reopens. The fallback options are listed in the [technology stack](../architecture/technology-stack.md#alternatives-considered).
