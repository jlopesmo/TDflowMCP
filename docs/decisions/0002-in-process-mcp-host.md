# ADR-0002 — Host the MCP server inside TouchDesigner (embedded loopback transport)

**Status:** Conditionally Accepted (owner, 2026-11-22). **Revised 2026-11-22** (owner direction D10, after spike S6): the default transport is an embedded stdlib Python HTTP server instead of the Web Server DAT, and **TouchDesigner 2025.32440 stays supported**. Other revisions: default port 9480 with conflict detection. Remaining gates are listed below.

## Context
- The owner asked not to assume a separate Node.js server is necessary.
- MCP 2026-07-28 Streamable HTTP is stateless per-POST JSON-RPC ([spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)).
- TDMCP (Derivative) and johnsabath/touchdesigner-mcp host MCP in-process; Envoy does too, via a uvicorn worker thread.
- The original plan used TouchDesigner's Web Server DAT, relying on its `localaddress` bind parameter ([docs](https://docs.derivative.ca/Web_Server_DAT)). Spike **S5** showed that on **2025.32440** the Web Server DAT has no `localaddress` and binds `0.0.0.0`, so it cannot meet the security model there ([S5](../research/2026-11-22-s5-compatibility-2025.32440.md)).
- The owner then decided that 2025.32440 must remain supported if technically feasible, without requiring an upgrade (D10).
- Spike **S6** showed that a stdlib HTTP server inside the component does meet the security model on 2025.32440 ([S6](../research/2026-11-22-s6-embedded-python-transport.md)).

## Decision
Run the MCP endpoint, the Setup Assistant ([ADR-0010](0010-setup-assistant-and-component-parameters.md)) and the later governance dashboard from **one loopback transport inside the TDflowMCP component**. It listens only after a user action and serves application requests only after the effective loopback-only binding is verified; failure closes the listener immediately ([bind-verification gate](../architecture/security-model.md#82-bind-verification-gate)). All policy and audit live in that process, next to the TD state they protect.

**Transport (interchangeable):**
1. **Default, all builds: the embedded Python transport** ([`src/tdflowmcp/net/pyhttp.py`](../../src/tdflowmcp/net/pyhttp.py)).
   - Built on `http.server` and `socketserver` from TouchDesigner's bundled Python. No extra dependency.
   - Binds only the literal `127.0.0.1`, exclusively (`SO_EXCLUSIVEADDRUSE` on Windows, never `SO_REUSEADDR`). It never shares the port with another process.
   - Handler threads parse HTTP, apply the request gate and authentication, and serve an immutable static snapshot. All other work goes through a **bounded queue** and is dispatched **only on the main thread**, by a per-frame `pump()` ([security model §8.3](../architecture/security-model.md#83-embedded-transport-threading-and-limits)).
2. **Optional: the Web Server DAT**, only on builds whose DAT has `localaddress`, only after it passes the same bind-verification gate, and only if spike S1 shows a measurable advantage (for example latency, WebSocket or TLS).
3. **One contract for both:** each transport sends every request through the same `gate.decide`, authenticator and kill switch before any dispatch, and exposes the same lifecycle (`start`, `begin_close`/`is_closed`, `pump`, `set_static`, `suspended`, `stats`, `drain_audit`). The controller, gate, policy and audit do not depend on which transport is used.

A stdio shim for stdio-only clients is **deferred**. If built, it must be a policy-free byte forwarder (stdlib Python, no Node required).

## Alternatives
- **Web Server DAT as the only transport.** This was the original decision. It is rejected as the default because it cannot bind loopback-only on 2025.32440 (S5); it is kept as the optional transport above.
- **"Official TDMCP + governance companion"**, as recommended by the owner-supplied bridge audit. It was rejected because TDMCP has no scopes, no plugin/hook API and no way to disable `execute_code`, and any local client can register for full control, so a companion cannot enforce policy. See the [bridge review §5](../research/2026-11-22-mcp-bridge-review.md#5-evaluation-tdmcp-as-foundation-vs-own-core). TDMCP remains a functional benchmark.
- **Separate Node server; official SDK in a separate process.** See the [technology stack](../architecture/technology-stack.md#alternatives-considered).

## Consequences
- **Supported builds:**
  - No extra runtime install, and a single process holds the truth.
  - **2025.32440 is supported;** the oldest tested build is now 2025.32440, not the provisional 2025.33230+ target.
- **Availability:** server availability equals TD availability. A stalled main thread delays every dispatch; clients get `504` after the job timeout, and a queued job that times out is never executed.
- **Measured costs (S6, 60 fps):**
  - Latency p50 is ≈ 17 ms for static files and ≈ 33 ms for MCP, because handler threads get the GIL about once per frame.
  - Throughput is 117 req/s with 8 clients.
  - The pump costs 0.01–0.06 ms per frame, and frame time is unchanged under load.
  - TDflowMCP owns one accept thread and up to 16 handler threads inside TouchDesigner.
- **What this transport lacks:** WebSocket, TLS and chunked request bodies. The Phase 2B live-event channel must use long-poll/SSE on this transport, or the Web Server DAT where it qualifies.
- **Responses can be deferred,** because a handler thread waits for its job. This was not available from the Web Server DAT on 2025.32440 (S5, inference).
- **Port sharing:** on Windows another process can still bind `0.0.0.0:<port>` beside an exclusive `127.0.0.1:<port>`. The pre-activation check and the verdict therefore treat any other process on the port as `PORT_IN_USE` (S6).

## Validation gates
- **S6 (executed 2026-11-22, passed; [findings](../research/2026-11-22-s6-embedded-python-transport.md), [review 4](../research/2026-11-22-s6-review.md)):** on 2025.32440 the embedded transport served a Setup page from DATs. It was loopback-only: the OS table showed only `127.0.0.1`, and the LAN address did not connect. It also passed:
  - 0 off-main-thread dispatches, and clean shutdown and restart;
  - timeouts, port conflict, overload, error recovery and the kill switch;
  - the full gate matrix.
- **S5 (executed 2026-11-22):** the Web Server DAT on 2025.32440 has no `localaddress` and binds `0.0.0.0`. The bind gate closed it in about 0.5 s without serving anything. The DAT transport is never activated on builds without `localaddress`.
- **Component integration (Phase 1A items 9–10):** the transport must run under the component controller with the bind gate, in in-TD tests on 2025.32440.
- **S1 (optional):** Web Server DAT benchmark on a build with `localaddress`. It only decides whether to offer the DAT transport.
- Port 9480 conflicts are detected and reported. The port is never silently changed.
- If integration fails, this ADR reopens. The fallback options are listed in the [technology stack](../architecture/technology-stack.md#alternatives-considered).
