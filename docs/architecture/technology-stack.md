# Technology stack (proposed)

**Status: Proposed.** Selection is not final until the referenced ADRs are Accepted. Nothing listed as "selected" has been installed, built or tested.

## Proposed stack

| Layer | Proposed choice | Rationale | ADR |
|---|---|---|---|
| Host runtime | TouchDesigner's bundled Python 3.11 (verified present in local 2025.32440) | Zero install; same interpreter users already have | [0002](../decisions/0002-in-process-mcp-host.md) |
| HTTP/WebSocket server | Web Server DAT (built-in) | Native, documented `localaddress` bind, TLS available; no server thread of our own (callback thread still to be confirmed in S1) | [0002](../decisions/0002-in-process-mcp-host.md) |
| MCP protocol | Hand-rolled JSON-RPC 2.0 + MCP 2026-07-28 + legacy `initialize` | SDK's deps (pydantic, uvicorn, anyio…) not bundled; PyPI may be blocked; main-thread model conflicts | [0003](../decisions/0003-protocol-implementation.md) |
| Schema validation | `jsonschema` 4.23 (bundled with TD 2025.32440; pinned as a test-only dependency for CI) | Already present at runtime; no new runtime dependency | [0003](../decisions/0003-protocol-implementation.md) |
| Audit + approvals + policy store | `sqlite3` (stdlib, bundled `_sqlite3.pyd`) | Transactions, queryable reports, single file | [0006](../decisions/0006-audit-store.md) |
| Crypto primitives | `secrets`, `hashlib`, `hmac` (stdlib) | Token generation, hashing, constant-time compare | [0009](../decisions/0009-permission-model.md) |
| Dashboard | Static HTML + vanilla ES modules + CSS, served by the component; WebSocket for live events | No build step, no npm, works offline on locked-down PCs | [0005](../decisions/0005-dashboard.md) |
| Code Mode | Pattern only: `search_api`/`describe_api`/`execute_script` over a policy-checked Python facade | Cloudflare package is JS + Workers-bound; in-process cannot be sandboxed | [0004](../decisions/0004-code-mode-strategy.md) |
| Unit tests | stdlib `unittest` on CPython 3.11 (local Python 3.11.8 verified) | Matches TD's major/minor; no pytest dependency required | [0007](../decisions/0007-source-first-build.md) |
| In-TD tests | Custom runner COMP executing `tests/td/` inside TouchDesigner, emitting JSON results | Only way to test `td` behavior faithfully | [0007](../decisions/0007-source-first-build.md) |
| CI | GitHub Actions: docs check + unit tests on Python 3.11 (Windows + Ubuntu) | In-TD tests need a licensed TD → run locally / self-hosted runner later | [0007](../decisions/0007-source-first-build.md) |
| Build | `tools/build_tox` script run inside TD (`TouchDesigner.exe` + startup script) producing `build/TDflowMCP.tox` + manifest | Reproducible from source; exact mechanism to be proven in Phase 1 spike | [0007](../decisions/0007-source-first-build.md) |

## Alternatives considered

| Alternative | Why not chosen (now) | Revisit when |
|---|---|---|
| Separate Node.js MCP server + TD bridge (8beeeaaat, Pantani pattern) | Two processes, two protocols, Node install, extra attack surface. Policy would be split from TD state | A client needs stdio only → add a thin **policy-free** stdio shim |
| Official MCP Python SDK inside TD (Envoy pattern) | Heavy compiled deps not bundled; requires venv/PyPI; uvicorn worker thread + main-thread marshalling complexity | SDK offers a dependency-light, transport-agnostic core, or owner prefers spec-tracking over control |
| Official SDK in a separate Python process | Same split-brain policy issue as Node; still needs deps | stdio shim requirements grow |
| Raw sockets / `http.server` thread inside TD | Reimplements what Web Server DAT does; threads must not touch OPs | Web Server DAT spike fails (e.g. cannot stream/defer) |
| React/Vite dashboard | Requires npm and a build; work PC may block registry | Dashboard complexity outgrows vanilla modules (ADR) |
| JSONL audit files | Simpler, but harder to query/report and to keep consistent with approvals | — |
| OAuth 2.1 (TDMCP pattern) | Complex. Bearer tokens meet local single-user needs, and the spec permits | Clients require OAuth, or remote access is approved |
| RestrictedPython / QuickJS / Deno sandboxes for Code Mode | Not bundled; RestrictedPython is not a sandbox; out-of-process loses direct TD access | Code Mode must accept untrusted scripts without approval |
| Embody as dependency for source-first | Couples us to a fast-moving project. Open issue #194 (can delete `.git/`) | Host-project externalization is scheduled; evaluate TDXN interop |
