# Technology stack (proposed)

**Status:** The rows backed by ADRs 0002–0007 and 0009 are Conditionally Accepted (owner, 2026-11-22), subject to validation gates. The rows backed by ADRs 0010–0013 are Proposed. Nothing listed here has been installed, built or tested.

## Proposed stack

| Layer | Proposed choice | Rationale | ADR |
|---|---|---|---|
| Host runtime | TouchDesigner's bundled Python 3.11 (verified present in local 2025.32440) | Zero install; same interpreter users already have | [0002](../decisions/0002-in-process-mcp-host.md) |
| HTTP server (transport) | **Embedded stdlib Python HTTP server** (`http.server` + `socketserver` threads, bounded queue, main-thread `pump`) by default; Web Server DAT optional on builds with `localaddress` | Only option that binds loopback-only on 2025.32440 (S5/S6); no new dependency; responses can be deferred. Costs ≈ 1–2 frames of latency, own threads in TD; no WebSocket/TLS | [0002](../decisions/0002-in-process-mcp-host.md) |
| MCP protocol | Hand-rolled JSON-RPC 2.0 + MCP 2026-07-28 + legacy `initialize` | SDK's deps (pydantic, uvicorn, anyio…) not bundled; PyPI may be blocked; main-thread model conflicts | [0003](../decisions/0003-protocol-implementation.md) |
| Schema validation | `jsonschema` (4.23 observed in the local 2025.32440 bundle; test-only CI pin matching the minimum build 2025.32440) | Already present at runtime; no new runtime dependency | [0003](../decisions/0003-protocol-implementation.md) |
| Audit + approvals + policy store | `sqlite3` (stdlib, bundled `_sqlite3.pyd`) | Transactions, queryable reports, single file | [0006](../decisions/0006-audit-store.md) |
| Bind evidence | OS listening-socket table via `GetExtendedTcpTable` (`ctypes`, stdlib) plus non-loopback connection probes | Validated against `netstat` in S5; no subprocess or third-party package | [0002](../decisions/0002-in-process-mcp-host.md) |
| Crypto primitives | `secrets`, `hashlib`, `hmac` (stdlib) | Token generation, hashing, constant-time compare | [0009](../decisions/0009-permission-model.md) |
| Setup Assistant | One static HTML page + vanilla JS served at `/setup/` from embedded DATs; JSON endpoints | Available before MCP; no build, no CDN; same gate as MCP | [0010](../decisions/0010-setup-assistant-and-component-parameters.md) |
| Component UI | TouchDesigner custom parameters (pulse/toggle/str/int/menu) | Native, self-contained; no external files at runtime | [0010](../decisions/0010-setup-assistant-and-component-parameters.md) |
| Governance dashboard | Static HTML + vanilla ES modules + CSS, served by the component; WebSocket for live events | No build step, no npm, works offline on locked-down PCs | [0005](../decisions/0005-dashboard.md) |
| Skills + client registry | Markdown `SKILL.md` bundles + `clients.json`, embedded in the `.tox`, written only on confirmed assisted install with sha256 manifest | No installer, no network; version tied to tool surface | [0011](../decisions/0011-agent-integration-and-skills.md) |
| Project memory | Markdown/JSON records with rev + sha256; files (repo mode) or DATs (embedded mode) | Single authority, diffable, Git-optional | [0013](../decisions/0013-project-memory-model.md) |
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
| Web Server DAT as the only transport | Cannot bind loopback-only on 2025.32440 (S5); no deferral API found | Kept as an optional transport on builds with `localaddress` if S1 shows a measurable advantage |
| React/Vite dashboard | Requires npm and a build; work PC may block registry | Dashboard complexity outgrows vanilla modules (ADR) |
| JSONL audit files | Simpler, but harder to query/report and to keep consistent with approvals | — |
| OAuth 2.1 (TDMCP pattern) | Complex. Bearer tokens meet local single-user needs, and the spec permits | Clients require OAuth, or remote access is approved |
| RestrictedPython / QuickJS / Deno sandboxes for Code Mode | Not bundled; RestrictedPython is not a sandbox; out-of-process loses direct TD access | Code Mode must accept untrusted scripts without approval |
| Embody as dependency for source-first | Couples us to a fast-moving project. Open issue #194 (can delete `.git/`) | Host-project externalization is scheduled; evaluate TDXN interop |
