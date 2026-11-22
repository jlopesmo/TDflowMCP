# System design

**Status: Design, not implemented** (see [current-state](../icm/current-state.md)). Choices are backed by ADRs 0002–0007 and 0009 (Conditionally Accepted, subject to validation gates) and ADRs 0010–0013 (Proposed). See the [decision index](../decisions/README.md).

## 1. Component diagram

```mermaid
flowchart LR
  subgraph Clients["Coding agents (outside TDflowMCP)"]
    CC[Claude Code / Codex / Cursor]
    SH["stdio shim (deferred, optional)"]
  end
  subgraph Browser
    SA["Setup Assistant /setup<br/>(Phase 1A)"]
    DB["Governance Dashboard /ui<br/>(Phase 2B)"]
  end
  USER((TD user)) -- custom parameters --> CTRL
  subgraph TD["TouchDesigner process — host project .toe"]
    subgraph TOX["TDflowMCP.tox (self-contained)"]
      CTRL[Component controller<br/>Off · Setup · Enabled · Suspended · Error]
      WS[Loopback transport<br/>embedded Python HTTP (default)<br/>127.0.0.1:9480<br/>VERIFYING → SERVING gate]
      GATE[Request gate<br/>Origin · Host · size · rate]
      ADM[Admin session<br/>pairing · CSRF]
      ID[Agent identity<br/>token → agent]
      COMPAT[Compatibility +<br/>troubleshooting]
      INTEG["Agent integration<br/>clients · Skills · Tell Your Agent (1C)"]
      MCP[MCP protocol layer<br/>2026-07-28 + legacy]
      REG[Tool registry<br/>core + memory + extensions]
      POL[Policy engine<br/>deny-by-default]
      APR["Approvals (2B)"]
      EXE[Execution layer<br/>main thread · undo · budgets]
      CM["Code Mode facade (3, opt-in)"]
      CONT["Continuity<br/>sessions (1B) · checkpoints/handoffs (2A)"]
      MEM["Project memory adapter (2A)<br/>repo ⇆ embedded authority"]
      AUD[(Audit store<br/>SQLite, hash chain)]
      CFG[(Tokens + policy<br/>Datadir, outside repo/.toe)]
    end
    NET[Host project network]
    PROJ["tdflowProject COMP (optional, 2A)"]
  end
  REPO[(Host project Git repo<br/>optional)]
  CTRL --> WS
  CTRL --> COMPAT
  CC -- Streamable HTTP + bearer --> WS
  SH -.-> WS
  SA --> WS
  DB --> WS
  WS --> GATE
  GATE -- /setup /ui /api --> ADM
  GATE -- /mcp --> ID
  ADM --> COMPAT
  ADM --> INTEG
  ADM --> CFG
  ADM --> APR
  ID --> MCP --> REG --> POL
  POL -- allow --> EXE --> NET
  POL -- approval --> APR --> EXE
  POL -- deny --> AUD
  EXE --> AUD
  REG --> CM --> POL
  REG --> CONT --> AUD
  CONT --> MEM
  MEM -- authority: embedded --> PROJ
  MEM -- authority: repo --> REPO
```

ASCII summary:

```
 TD user ─parameters─▶ Controller (Off/Setup/Enabled/Suspended/Error) ─▶ transport 127.0.0.1:9480
 Browser ─/setup, /ui──┐                                                        │ gate (Origin/Host/size/rate)
 Coding agent ─/mcp────┴──────────────────────────────▶ admin session ◀─────────┤
                                                        identity ─▶ MCP ─▶ registry ─▶ POLICY ─▶ exec (undo) ─▶ network
                       compatibility · integration · continuity ─▶ audit (SQLite) │ memory adapter ─▶ tdflowProject OR repo
```

## 2. Components

| Component | Responsibility | Key constraints |
|---|---|---|
| **Component controller** | Owns state transitions and custom parameters ([setup assistant §1–2](setup-assistant.md#1-component-states)) | Never listens before a user action. MCP is off by default |
| **Transport** | Single listener for `/setup`, `/mcp`, `/api`, `/ui`. **Default: embedded Python HTTP server** (`pyhttp`, handler threads + a main-thread pump). **Optional:** Web Server DAT on builds that have `localaddress`, if it shows a measurable advantage ([ADR-0002](../decisions/0002-in-process-mcp-host.md)) | Binds the literal `127.0.0.1` exclusively. Starts `VERIFYING` (503 to all), serves only after the effective bind is verified loopback-only, and closes immediately on failure ([security model §8.2](security-model.md#82-bind-verification-gate), [§8.3](security-model.md#83-embedded-transport-threading-and-limits)). Default port 9480 with conflict detection; never re-ports silently; never shares the port |
| **Request gate** | Rejects bad `Origin`/`Host` (403), oversized bodies (413), malformed JSON (400) and floods **before** any TD access | Origin validation is mandatory per spec |
| **Admin session** | Pairing code → session cookie; exact Origin + CSRF for every `/setup`/`/ui`/`/api` mutation | Agent tokens never grant admin access |
| **Compatibility + troubleshooting** | Checks and remediation ([setup assistant §3–5](setup-assistant.md#3-compatibility-checks)) | Diagnose and instruct only; no installs or config changes |
| **Agent integration** | Client registry, connection text, Skills bundle install/update ([agent integration](agent-integration.md)) | Instruction mode by default; confirmed, previewed, hash-manifested writes only |
| **Agent identity** | `Authorization: Bearer tdf_<id>.<secret>` → agent record | Salted hash, constant-time compare |
| **MCP protocol layer** | JSON-RPC 2.0; spec 2026-07-28 + legacy `initialize` | Hand-rolled ([ADR-0003](../decisions/0003-protocol-implementation.md)) |
| **Tool registry** | Tool definitions with **required actions**, **classification**, **target resolver**, handler | Extensions and memory tools use the same contract |
| **Policy engine** | `(agent, action, targets, tool, args)` → allow / deny / require_approval | Pure Python, no `td` ([security model](security-model.md)) |
| **Approvals** (2B) | Pending calls, redemption by identical re-call | Phase 1: approval ⇒ deny |
| **Execution layer** | Main-thread execution, undo blocks, cooperative budgets | Never touches OPs off the main thread |
| **Continuity** | Session logs (1B); checkpoints and verified handoffs (2A) ([continuity](continuity-and-memory.md)) | Facts derived from audit; agent claims labelled |
| **Project memory adapter** (2A) | Reads/writes project memory in the single authoritative home | `MEMORY_CONFLICT` on stale writes; `Projectid` match |
| **Audit store** | Append-only SQLite; intent/outcome/deny events; keyed checkpoints | In `Datadir` only ([ADR-0006](../decisions/0006-audit-store.md)) |
| **Experiments / Code Mode** (3) | Sandboxed child COMPs; policy-checked script facade | Not before 1B enforcement gates pass |

## 3. Request lifecycle (MCP `tools/call`)

1. A transport handler thread receives a POST to `/mcp` (no TD access on this thread).
2. Gate: Host is loopback, Origin is absent or canonical, body ≤ limit, `Content-Type` is JSON. Otherwise 400/403/413, with aggregated `gate_reject` logging.
3. Identity: resolve the bearer token. Missing or invalid → 401 + aggregated `auth_fail`. If Suspended → `SUSPENDED`. The request is then queued; steps 4–11 run on the main thread via `pump()`. If the state is not Enabled, the dispatcher returns 503 `MCP_DISABLED`.
4. Protocol: parse JSON-RPC, check the protocol version header against the body, and route the method.
5. Registry: find the tool, then validate arguments against its input schema.
6. Target resolution: absolute, normalized operator paths plus actions.
7. Policy: evaluate each `(action, path)` per the [action classification](security-model.md#33-action-classification-of-tool-effects-security-critical). Deny → tool error + `deny` audit event. Approval needed → pending result (§4).
8. Audit `intent` event; fail closed if the store is unavailable.
9. Execute on the main thread inside an undo block, with cooperative budget checks ([security model §7](security-model.md#7-resource-limits-cooperative)).
10. Audit `outcome` event; push it to subscribers (2B).
11. Respond with `content` + `structuredContent`.

## 4. Approvals without blocking TouchDesigner

The embedded transport could hold a response open (the handler thread waits on its job), but a waiting human must never hold a job on the main thread. The design therefore never blocks the cook thread waiting for a human:
- `require_approval` → record a pending call (canonical args digest, agent, resolved targets, policy version, expiry) → respond immediately `{"status":"pending_approval","approval_id":…}`.
- The human decides in the dashboard, which shows the full raw arguments.
- `tdflow_approval_status` is read-only. **Execution happens only on the identical re-call** with `_meta.tdflow.approval_id`, after the targets are re-resolved and policy re-evaluated ([security model §4](security-model.md#4-approvals)).
- **Phases 1–2A have no approval surface:** `require_approval` ⇒ deny (`APPROVAL_UNAVAILABLE`).

## 5. Extension contract

Extensions live in `src/extensions/<name>/` and reach `td` only through `src/td_adapter/`. They register tools through the registry API:

```python
# proposed shape — not implemented
@tool(name="tdflow_x_capture_top", actions={"read"}, targets=arg_path("path"),
      annotations={"readOnlyHint": True}, budget_ms=200)
def capture_top(ctx, path: str, max_px: int = 512) -> ToolResult: ...
```

Extensions cannot bypass policy, register routes outside `/mcp`, or access tokens or admin sessions. They are disabled until enabled in policy.

## 6. Threading and performance

- All TD object access happens on the main thread. The embedded transport's handler threads do only HTTP parsing, gate, authentication and static serving; everything else is dispatched by the per-frame `pump()` on the main thread.
- **Measured in S6 on 2025.32440 at 60 fps:**
  - pump cost 0.01–0.06 ms mean per frame (max 0.26 ms);
  - frame-time p50/p95 unchanged under load (16.67 / ≤ 16.78 ms);
  - latency p50 ≈ 17 ms static, ≈ 33 ms MCP `ping`;
  - 117 req/s with 8 clients.
- Policy and protocol work also run on the main thread in Phase 1. Measure before moving anything to the Thread Manager.
- Targets: p95 server overhead ≤ 5 ms per read call excluding the handler; response cap 1 MiB (enforced); 250 ms per-call budget for core tools, enforced **cooperatively**. Runaway Python cannot be pre-empted ([security model §7](security-model.md#7-resource-limits-cooperative)).
- Compatibility checks must finish in < 1 s and must never block cooking for more than one frame per step (chunked).

## 7. Deployment

- **Distribution:** a self-contained `TDflowMCP.tox` (all Python, Setup Assistant HTML/JS, Skills bundle and troubleshooting data embedded) plus `TDflowMCP.json` (version, TD build, sha256), built from `src/` on the declared minimum build ([ADR-0007](../decisions/0007-source-first-build.md)). `tdflowProject.tox` (2A) ships separately.
- **Runtime state** (tokens, policy, audit, session logs): `Datadir` = per-user `%LOCALAPPDATA%\TDflowMCP\<project-id>\` on Windows (macOS TBD). Never in the `.toe`, the `.tox` or Git.
- **Ports:** default 9480 (avoids TDMCP 13316, Envoy 9870, 8beeeaaat 9981, Pantani 9980, johnsabath 9988). On a conflict the component reports the owner (another TDflowMCP instance is identified via `/.well-known/tdflowmcp`) and waits for the user to choose.
- **Client setup:** generated by the Setup Assistant / "Tell Your Coding Agent" with a `${TDFLOW_TOKEN}` placeholder (env-var expansion to be verified per client in 1C). Tokens are never written into repo files.
- **Claude Desktop:** not supported until the stdio shim or OAuth (deferred). Phase 1 targets Claude Code.
- **TD builds:** **2025.32440 is the oldest tested and supported build** (owner D10). The embedded transport meets the security model there (S6). Newer builds use the same transport by default. The Web Server DAT is only an option on builds whose DAT has `localaddress`, and only after it passes the same gate.
