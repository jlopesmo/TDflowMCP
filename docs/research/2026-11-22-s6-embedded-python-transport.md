# Spike S6: embedded Python HTTP transport on TouchDesigner 2025.32440

**Date:** 2026-11-22 · **Status:** Executed; **success criteria met**. Reviewed in [review 4](2026-11-22-s6-review.md).
**Why:** S5 showed the 2025.32440 Web Server DAT cannot bind loopback-only. The owner decided 2025.32440 must stay supported if technically feasible, without requiring a newer build (roadmap D10). S6 tests replacing the Web Server DAT with a stdlib HTTP server running inside the component.
**Code under test:** the real core module [`src/tdflowmcp/net/pyhttp.py`](../../src/tdflowmcp/net/pyhttp.py), plus the bind gate (`bindverify.py`), the request gate (`gate.py`) and `oslisteners.py`. These are imported unchanged into TouchDesigner. The driver and harness are disposable [spike code](../../spikes/s6/README.md).

**Method:**
- A disposable `s6.toe` was generated from text with `toecollapse` under `.tmp/s6/build/` (git-ignored). It contains one boot Execute DAT and three Text DATs holding the Setup Assistant's HTML, CSS and JS.
- TouchDesigner 2025.32440 was launched with that file only. The external harness ran 66 checks over HTTP and raw sockets, then asked TD to shut down; TD quit with exit code 0. A separate conflict-mode run ran 4 checks.
- No user project was opened. Nothing was installed. No system or TouchDesigner setting was changed; Python's switch interval was read but not altered.
- Raw results stay git-ignored in `.tmp/s6/results/`, because they contain host network details.

**Runs:**
- Main mode: 5 runs. The run before review 4 passed 65/65. The **final run, after the review-4 fixes, passed 66/66**; the added check is that `/.well-known` reveals no project details.
- Conflict mode: the **final run passed 4/4**. Unless stated otherwise, figures are from the final runs.
- Earlier runs exposed spike defects, all fixed:
  - the timeline frame passed to `onFrameStart` loops, so the driver now counts its own frames;
  - Edge's headless mode made no request on this machine, so the harness now uses Chrome;
  - the harness's startup check raced the ready file (fixed with a TD-side wall-clock stamp);
  - the harness crashed on a reset during restart. Its TouchDesigner process was then stopped by PID, and the harness now cleans up after itself.

## 1. Results (final runs)

| Requirement | Result | Evidence |
|---|---|---|
| Bind explicitly to `127.0.0.1:9480` | **pass** | The transport refuses any other host literal (`0.0.0.0`, `localhost`, `::`, `::1`, a LAN IP): `BIND_REFUSED`. In TD, the OS table showed this process listening only on `127.0.0.1:9480` (no IPv6), and `getsockname()` agreed. Verification took ≈ 0.31–0.35 s, on a background thread |
| Reachable via localhost, not via the LAN address | **pass** | `127.0.0.1` served. A TCP connect to the machine's LAN IPv4 address **did not connect** (timeout), and HTTP got no response. `::1` is not bound. Same results after the restart. While serving, the binding was re-verified off-thread every 300 frames; 8 re-checks passed |
| No application request before verification | **pass** | Every pre-serving poll got `503 TDFLOW_VERIFYING` or no connection. The harness's check compares response-arrival times with TD's `SERVING` timestamp: no 200 arrived before it |
| HTTP on background threads; TD only on the main thread via a bounded queue | **pass** | **924 dispatches, all on `MainThread`, 0 elsewhere** (948 / 0 in the run before). A scripted create/edit/read/destroy of a Text DAT ran through the queue on the main thread. Handler threads never import `td` (boundary test). `pump()` refuses to run on any other thread (unit test) |
| Startup | **pass** | `start()` ≈ 1.5–3.7 ms, then `VERIFYING` → `SERVING` in ≈ 0.31–0.35 s |
| Shutdown | **pass** | `begin_close()` never blocks the frame. Closure is confirmed against the OS table in 33–66 ms. TD exited with code 0, no port-9480 listener remained, and no transport thread was alive at the end |
| Restart | **pass** | User-style restart: `CLOSING` → `CLOSED`, confirmed, then explicit re-activation → `VERIFYING` → `SERVING` on the same port in ≈ 0.6 s. No `TIME_WAIT` bind failure. During the gap clients saw 503, a reset or refusal, never a stale 200 |
| Timeouts | **pass** | While the main thread was blocked for 5 s, a queued request got `504 TDFLOW_TIMEOUT` after 2.0 s and **was never dispatched**. The blocking request got `504 TDFLOW_TIMEOUT_AFTER_DISPATCH` after 4.0 s. A client that sent half a request line was cut after 5.0 s |
| Port conflicts | **pass** | Another process holding `127.0.0.1:9480` was detected by the OS-table pre-check. TD reported `PORT_IN_USE` and never listened; a direct bind attempt was refused, also `PORT_IN_USE`. A second transport inside TD got `PORT_IN_USE`, and the first kept serving |
| Multiple requests | **pass** | Sequential: 200 MCP `ping` + 200 static requests, all 200. Concurrent: 8 clients, 400 requests, all 200, 99–117 req/s across runs. Overload: 48 clients × 480 requests → only `200` or `503 TDFLOW_BUSY`, no errors or crashes |
| Frame-rate impact | **pass** (no measurable impact) | Median frame time 16.66–16.67 ms and p95 ≤ 16.78 ms in **every** phase, including load. Pump cost per frame: mean 0.01–0.06 ms, max 0.96 ms. Occasional ≈ 506 ms frames occur in the **server-off baseline too**, so they are not caused by the transport (§3) |
| Recovery after errors | **pass** | A dispatcher exception gave `500 INTERNAL_ERROR` with no internals in the body. A client aborting mid-body, and a connect-and-close, left the server healthy; the next request was 200 |
| Static Setup Assistant from DATs | **pass** | `/setup/`, `/setup/app.css` and `/setup/app.js` were served byte-identical to the asset files (SHA-256 match) with correct content types. Headless **Chrome** (throwaway profile) loaded the page, ran the external JS, and the JS's `fetch('/setup/api/status')` passed the gate. The DOM showed `SERVING`, build `2025.32440`, and "main thread: true". The CSP (`default-src 'self'`) allowed this; inline scripts are not allowed |
| Authentication | **pass** | `/mcp` without a token, with a wrong token, or with two `Authorization` headers → 401. Only SHA-256 digests are stored, compared in constant time. No token appears in the 1,180 audit records |
| Origin/Host, CSRF, preflight | **pass** | The gate matrix (23 cases) behaved as specified: bad or duplicate Host, `localhost` → 308 to canonical, foreign and other-port Origins → 403, missing `X-TDflow-Request`, cross-site fetch, preflight denied, path traversal (plain and percent-encoded), mutations without Origin/CSRF, 413 without reading the body, chunked → 400 |
| CRUD enforcement / deny by default | **pass** (at transport level) | Mutating Setup routes with Origin and CSRF still get 401 (no admin session exists yet); admin reads get 401. Route-to-scope enforcement is the Phase 1B policy engine; the transport only passes authenticated, gated jobs to it |
| Kill switch | **pass** | `suspended` → every `/mcp` request returns `503 SUSPENDED` before any dispatch, while `/setup/` keeps working. Clearing it restored service |
| No unauthenticated operational endpoint; no arbitrary Python | **pass** | The only unauthenticated routes are static assets, `/.well-known/tdflowmcp`, and a read-only `/setup/api/status` that requires the custom header. `tools/list` is empty. `tools/call`, `execute_python` and `td/exec` → `-32601`. The spike's `s6/*` control methods require the token and exist only in spike code |
| Audit | **pass** (transport part) | One in-memory record per request (id, time, method, validated path, route, status, code, duration, principal), drained by the main thread. No headers, bodies or tokens. The durable intent/outcome store remains Phase 1B (ADR-0006) |

## 2. Benchmark against the Web Server DAT approach

**The Web Server DAT was not benchmarked on 2025.32440.** Any activation binds `0.0.0.0` (S5), so a measurement would expose a LAN port; this was not done, as security comes first. S5 recorded no HTTP latency, because its gate never reached `SERVING`.

| Aspect | Embedded Python transport (measured on 2025.32440) | Web Server DAT (documented / S5) |
|---|---|---|
| Loopback-only bind on 2025.32440 | **Yes**: explicit `127.0.0.1`, exclusive, verified | **No** (`0.0.0.0`) |
| Where app code runs | Main thread via `pump()` (≤ 8 jobs, ≈ 4 ms budget per frame) | Main thread inside `onHTTPRequest` |
| Latency at 60 fps | p50 **≈ 17 ms** static, **≈ 33 ms** MCP (p95 ≈ 32 / 49 ms) | Unmeasured; expected ≈ 1 frame |
| Throughput | 99–117 req/s with 8 clients (bounded by the per-frame pump) | Unmeasured |
| Main-thread cost | 0.01–0.06 ms mean per frame (max 0.96 ms) | Unmeasured |
| Deferred responses / long-poll / SSE | **Possible**: the handler thread waits on a job | No deferral API found on 2025.32440 (S5, inference) |
| WebSocket, TLS | Not implemented | Built in |
| Extra threads in TD | 1 accept thread + ≤ 16 handler threads (daemon) | None of ours |
| Parsing | Python `http.server` (strict framing, limits enforced by the gate) | Native C++ |

**Measured cause of the ≈ 1-frame floor:** static responses never touch the main thread, yet take ≈ 17 ms inside TD. In plain CPython the same transport answers in ≈ 0.2–0.3 ms. So inside TouchDesigner, the handler threads get the GIL roughly once per frame (`sys.getswitchinterval()` = 0.005 s, unchanged). This is acceptable for agent tool calls. It is a reason to keep the transport interchangeable if a future Web Server DAT offers a measurable latency advantage.

## 3. Limitations and findings

1. **Wildcard co-binding on Windows.** Even with `SO_EXCLUSIVEADDRUSE` on our `127.0.0.1:9480`, another process can still bind `0.0.0.0:9480`. Conversely, if another process holds `0.0.0.0:9480` non-exclusively, our loopback bind succeeds and **captures loopback traffic meant for it**. This was verified with throwaway sockets on ephemeral ports (see §5). Handling: any listener on the port owned by another process makes the pre-check and the verdict fail with `PORT_IN_USE` (`others` / `port_shared`). The port is never shared and never changed silently.
2. **Main-thread stalls** (a heavy cook, a modal dialog, a long script) block every dispatch. Clients get `504` after the job timeout; queued jobs that time out are never executed. A job already dispatched when its client timed out (`TIMEOUT_AFTER_DISPATCH`) may still have run: agents must check the outcome by request id or audit, and later phases must treat retries as non-idempotent.
3. **HTTP subset:** `Connection: close` on every response, no chunked request bodies, no WebSocket, no TLS. The Phase 2B dashboard's live events (`/api/events`, currently specified as WebSocket) need a redesign (long-poll or SSE on this transport), or the Web Server DAT on a build that has `localaddress`.
4. **IPv6:** only `127.0.0.1` is bound. Generated client configs must use `http://127.0.0.1:9480/…`, not `localhost`. The gate already redirects `localhost` page loads to the canonical origin.
5. **Overload:** with more than 16 concurrent connections, the extra ones get an immediate `503 TDFLOW_BUSY` without a thread. An earlier run with a backlog of 16 also produced 6 refused connects in the 48-client burst; the backlog is now 64.
6. **Periodic ≈ 506 ms frames** (about one every 10 s) appear with the server off, so they come from TouchDesigner or the host and are not attributable to the transport. They were not investigated further.
7. **The browser launch from TouchDesigner itself** (`ui.viewFile` or `webbrowser`) was still not exercised: headless Chrome was started by the harness, not by TD. This remains for item 10 (the `Opensetup` pulse).
8. **Edge headless made no request at all on this machine** (exit code 0, empty output), possibly because of enterprise policy. Chrome headless worked.

## 4. Can Phase 1A proceed without a TouchDesigner upgrade?

**Yes.** The S6 success criteria are met on 2025.32440. A disposable project served a minimal Setup page from DATs through the embedded transport, with:
- loopback-only access verified by the OS table, `getsockname` and a LAN probe;
- clean shutdown and restart;
- no off-main-thread TouchDesigner operations.

The bind-verification gate and every request-gate rule apply unchanged. Remaining 1A spikes:
- **S2** (`.tox` build from text with custom parameters) and **S3** (in-TD test runner) can now run on 2025.32440.
- **S4** (undo semantics) can run on 2025.32440.
- **S1** (Web Server DAT) becomes optional. It only matters for deciding whether a newer build's DAT is worth switching to.

## 5. Side effects on the host
- TouchDesigner was launched 11 times, always with the disposable file only. One process was left running after a harness crash and was stopped by PID (`taskkill /T`).
- Headless Chrome and Edge were started with throwaway profiles under `.tmp/`. The user's browser profiles were untouched.
- During development, two throwaway probe scripts bound sockets to `0.0.0.0` on OS-assigned ephemeral ports for well under a second, to establish the co-binding semantics in §3.1. Each probe made only a local connection and served nothing. All TouchDesigner runs and harness sockets used `127.0.0.1` only.
- No system settings, firewall rules or TouchDesigner preferences were changed.
