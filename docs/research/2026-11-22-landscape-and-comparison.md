# Research: TouchDesigner MCP landscape, Code Mode and platform constraints

**Date:** 2026-11-22 · **Status:** Research record (evidence + analysis). It does not by itself approve any decision; see [ADRs](../decisions/README.md).
**Method:** Documentation review through github.com/api.github.com and vendor docs, plus local file inspection of the installed TouchDesigner. **No upstream code was cloned, installed or executed, and TouchDesigner was not launched.**

Evidence labels: **[D]** documented by the project/vendor (source linked) · **[L]** verified locally by file inspection on this machine · **[I]** inference/analysis by the author of this record · **[U]** could not be verified (reason given).

## 1. Original `mcp_bridge.tox` and its audit

**Update, 2026-11-22:** the owner supplied the `.tox`, its audit and the extracted source later the same day. They are reviewed in [mcp_bridge review](2026-11-22-mcp-bridge-review.md) and preserved in [reference/historical/mcp_bridge](../../reference/historical/mcp_bridge/README.md). The earlier local search had found nothing on `F:\` or in the user profile, because the files were not yet in the workspace.

## 2. Projects reviewed

### 2.1 Derivative TDMCP — https://github.com/TouchDesigner/TDMCP (default branch `2025.30000`)
- **Architecture [D]:** an MCP server *inside* TouchDesigner shipped as `TDMCP.tox` (extension files `mcp_server_ext.py`, `oauth_provider.py`). Streamable HTTP at `http://127.0.0.1:13316/mcp`. Claude Desktop connects through an `mcp-remote` (Node 18+) bridge. Sources: [README](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/README.md), [setup-advanced](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/setup-advanced.md), [claude-desktop](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/claude-desktop.md).
- **How HTTP is served [U]:** not documented (Web Server DAT vs. other mechanism).
- **Tools [D]:** 26, covering inspection, parameters, operator CRUD/wiring, DAT content, errors, performance, `view_operator` (image), `execute_code` and `save_project`. Read-only tools are annotated, and mutating tools are wrapped in undo blocks. [component.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/component.md)
- **Security [D]:** OAuth 2.1 + PKCE by default with an in-TD consent panel. 24 h tokens stored under `%LOCALAPPDATA%\Derivative\TDMCP`. Loopback only, with non-loopback requests getting 403 (binding to 127.0.0.1 requires TD 2025.33060+). Origin check is always on. `execute_code` is arbitrary Python, and the docs say to treat it "like shell access". A self-protection fence does *not* cover `execute_code`. No per-call approval, and "Auto-accept deletion" is on by default. Auth was briefly disabled by a parameter-reset bug (1.1.56–1.1.58). [security.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/security.md), [beta.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/docs/beta.md), [CHANGELOG](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/CHANGELOG.md)
- **License [D]:** "Shared Use License" + "TDMCP Tool Terms of Use" (revised 2026-04-14). Not OSI; GitHub reports `NOASSERTION`. Use and modification are restricted to "in conjunction with Derivative's TouchDesigner software". The license is "revocable" and adds AI-provider use restrictions. [LICENSE.md](https://github.com/TouchDesigner/TDMCP/blob/2025.30000/LICENSE.md)
- **Maturity [D]:** public beta. Requires TD **2025.33230+**. Releases 1.1.50→1.1.58 between 2026-09 and 2026-11. 112★, 5 open issues (e.g. #16 chunked HTTP with `MCP-Protocol-Version` header, #15 OAuth through the Desktop bridge). Claims 900+ tests.
- **Source-first [D]:** the repo contains docs, skills and a plugin. **No Python source is in the repo**, and the `.tox` ships via Releases. No externalized-source workflow is documented.

### 2.2 Embody / Envoy — https://github.com/dylanroscover/Embody
- **Embody [D]:** externalizes tagged operators to files: COMPs → `.tox`/`.tdxn` (YAML), DATs → `.py`/`.json`/`.glsl`/… Two modes: Export-on-Save (default, `.toe` stays truth) and experimental Roundtrip (rebuilds from `.tdxn` on open). Ships a git diff driver. [externalization.md](https://github.com/dylanroscover/Embody/blob/main/docs/embody/externalization.md), [supported-formats.md](https://github.com/dylanroscover/Embody/blob/main/docs/embody/supported-formats.md)
- **Envoy [D]:** MCP server embedded in TD. Uses the official **MCP Python SDK 2.x over uvicorn in a worker thread**, with TD calls marshalled to the main thread via Event+Queue. Endpoint `http://127.0.0.1:9870/mcp`. A stdlib **stdio bridge** answers the handshake while TD is down. It auto-writes client configs. ~70 tools including `execute_python`, TDXN diff/import/export, captures, multi-session leases, tests and self-update. Packages are installed into a per-project `.venv` built from TD's interpreter. [architecture.md](https://github.com/dylanroscover/Embody/blob/main/docs/envoy/architecture.md), [tools-reference.md](https://github.com/dylanroscover/Embody/blob/main/docs/envoy/tools-reference.md)
- **Security [D]:** loopback + Host/Origin checks. **No authentication**: "any process that can reach the port has full control". It writes `.claude/settings.local.json` permission postures, and the recommended default auto-approves all. LAN peers (Convoy) may run Python if enabled. [security.md](https://github.com/dylanroscover/Embody/blob/main/docs/envoy/security.md)
- **License [D]:** MIT © The Experiential Company, LLC. [LICENSE](https://github.com/dylanroscover/Embody/blob/main/LICENSE)
- **Maturity [D]:** v6.3.8 (2026-11-20), 223★. Requires TD **2025.33230+**. 5,767 tests run inside TD plus pytest CI. **Open issue #194: the disabled path can delete the project's `.git/`, `.venv`, `.claude/`, `AGENTS.md` on reopen.** [issue #194](https://github.com/dylanroscover/Embody/issues/194)

### 2.3 Other implementations (README-level review [D], stats from api.github.com 2026-11-22)

| Project | License | Architecture | Auth | Exec Python | Notes |
|---|---|---|---|---|---|
| [8beeeaaat/touchdesigner-mcp](https://github.com/8beeeaaat/touchdesigner-mcp) | MIT | Separate Node MCP server → Web Server DAT `.tox` on 127.0.0.1:9981 | none documented | yes | Most popular (639★). Versioned server↔tox API contract |
| [Pantani/tdmcp](https://github.com/Pantani/tdmcp) | MIT | Node server → Python REST bridge in TD :9980 | opt-in token | yes (can disable) | README admits binding all interfaces. Install via `exec(urlopen(...))` |
| [johnsabath/touchdesigner-mcp](https://github.com/johnsabath/touchdesigner-mcp) | none detected | **MCP Streamable HTTP hand-rolled inside a Web Server DAT**, no deps | none documented | yes | Closest to our proposed architecture (2025-03-26 spec era) |
| [twozero-team/twozero-td-mcp](https://github.com/twozero-team/twozero-td-mcp) | none detected | In-TD, commercial plugin, Streamable HTTP :40404 | none documented | — | Multi-instance routing |
| [r-spring/touchdesigner-mcp-server](https://github.com/r-spring/touchdesigner-mcp-server) | MIT | Docs-only MCP (no TD control) | n/a | no | Operator/Python API reference |

**[I] Common pattern:** the Web Server DAT is the universal bridge. Almost every project exposes arbitrary Python. None of the community projects enforce authentication by default, and none have server-side, scope-based permissions.

### 2.4 Cloudflare Code Mode
- **[U] Fetch limitation:** every developers.cloudflare.com page, including the target [codemode/mcp page](https://developers.cloudflare.com/agents/tools/codemode/mcp/), failed with a TLS-interception certificate error on this network. Certificate checks were not bypassed. The details of `codeMcpServer()`/`openApiMcpServer()` below come from search-result summaries and must be re-verified.
- **Concept [D]:** convert MCP tool schemas into a TypeScript API. The model writes one script that runs in a fresh V8 isolate (Worker Loader) and reaches tools only via bindings, with no network (`globalOutbound: null`), no filesystem and no env. [blog: Code Mode](https://blog.cloudflare.com/code-mode/), [blog: Code Mode MCP](https://blog.cloudflare.com/code-mode-mcp/)
- **Package [D]:** `@cloudflare/codemode` (MIT, experimental). Exports `createCodeTool`, `DynamicWorkerExecutor` ("requires Cloudflare Workers environment"), `IframeSandboxExecutor`, and `createCodemodeRuntime` (search/describe/execute/approve/reject/rollback). It has a pluggable `Executor` interface. [README](https://github.com/cloudflare/agents/blob/main/packages/codemode/README.md)
- **Claims (vendor) [D]:** ~99.9 % input-token reduction for a 2,500-endpoint API (Cloudflare). 98.7 % in an example workflow (Anthropic, [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)). These are **vendor claims, not tested here**.
- **Sandbox alternatives [D]:** RestrictedPython states it is "not sufficient as a sandbox environment" ([docs](https://restrictedpython.readthedocs.io/en/latest/idea.html)). Anthropic programmatic tool calling is an API-side feature, and `allowed_callers` "is not a hard … security boundary" ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)).
- **[I] Implication:** TD's Python object model already *is* a code API that models know. In-process execution inside TD cannot be truly sandboxed. The useful parts to adopt are the **pattern** (search/describe/execute, approvals, rollback, a curated typed facade) and enforcing policy at the facade. Copying the package is not useful: it is JS and Workers-bound.

## 3. Platform facts

| Fact | Evidence |
|---|---|
| Local TD 2025.32440 bundles Python 3.11 (`python311.dll`), plus `sqlite3`, `ssl`, `hmac`, `secrets`, `asyncio`, `unittest`, `jsonschema` 4.23, `pip` 24.2. No `pydantic`/`starlette`/`uvicorn`/`anyio`/`mcp` | **[L]** `C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\` |
| A legacy local install bundles Python 3.9 | **[L]** `C:\Program Files\Derivative\TouchDesigner\bin\python39.dll` |
| Python upgraded to 3.11.15 in build 2025.33070. Latest listed build 2025.33230 | **[D]** [Release notes](https://docs.derivative.ca/Release_Notes) |
| External packages: install with a matching Python and add to search path / `sys.path` | **[D]** [Python](https://docs.derivative.ca/Python) |
| Web Server DAT: HTTP + WebSocket. Callbacks `onHTTPRequest`, `onWebSocket*`, `onServerStart/Stop`. `localaddress` (blank = all interfaces). TLS + mTLS. Basic auth helper | **[D]** [Web Server DAT](https://docs.derivative.ca/Web_Server_DAT) |
| Callback thread and whether responses can stream (SSE) | **[U]** not stated; must be tested in Phase 1 spike |
| Don't access OPs/COMPs/Parameters off the main thread. Thread Manager exists (`op.TDResources.ThreadManager`) | **[D]** [Thread Manager](https://docs.derivative.ca/Thread_Manager) |
| Undo: `ui.undo.startBlock(name)`, `endBlock()`, `addCallback(cb, info)`, `undo()`, `redo()` | **[D]** [Undo Class](https://docs.derivative.ca/Undo_Class) |
| MCP current spec **2026-07-28**: stateless Streamable HTTP. No `initialize`/sessions; version + capabilities per request in `_meta`. Headers `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`. Legacy `initialize` fallback allowed | **[D]** [transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) |
| Spec security: servers **MUST** validate `Origin` (403 on invalid), **SHOULD** bind 127.0.0.1, **SHOULD** authenticate | **[D]** same page (Streamable HTTP) |
| MCP Python SDK `mcp` 2.3.0, MIT, Python ≥3.10. Deps include pydantic, starlette, uvicorn, anyio, httpx, pyjwt, opentelemetry, pywin32 | **[D]** [python-sdk](https://github.com/modelcontextprotocol/python-sdk), [PyPI](https://pypi.org/project/mcp/) |

## 4. Comparison matrix

Legend: ✔ documented · ✘ documented absent · ? unknown · ◐ partial. TDflowMCP column = **proposed target, not implemented**.

| Dimension | Derivative TDMCP | Embody/Envoy | 8beeeaaat | johnsabath | **TDflowMCP (proposed)** |
|---|---|---|---|---|---|
| MCP host location | in TD | in TD (worker thread) | separate Node | in TD (Web Server DAT) | in TD (Web Server DAT) |
| Extra runtime needed | Node only for Desktop | per-project `.venv` + SDK deps | Node | none | **none** (stdlib + bundled) |
| Transport | Streamable HTTP | Streamable HTTP + stdio bridge | ? (likely stdio) | Streamable HTTP | Streamable HTTP (2026-07-28 + legacy); stdio shim deferred |
| Authentication | OAuth 2.1/PKCE | ✘ | ✘ | ✘ | per-agent bearer tokens (OAuth later, ADR) |
| Agent identity | per-client OAuth | ✘ | ✘ | ✘ | ✔ token → agent id/role |
| Server-side scoped CRUD permissions | ✘ (read-only annotations only) | ✘ (client-side postures) | ✘ | ✘ | ✔ deny-by-default, path scopes × CRUD+execute |
| Human approvals | consent once per client | client-side | ✘ | ✘ | per-call approval rules, dashboard queue |
| Audit trail | ? | ring buffer + daily logs | ? | ? | append-only SQLite, hash-chained, exportable |
| Arbitrary Python exec | ✔ (on) | ✔ (on) | ✔ | ✔ | **off by default**, separate `execute` permission + approval |
| Undo | ✔ undo blocks | ? | ? | ? | ✔ undo block per mutating call + audit-linked undo |
| Experiments/sandbox COMP | ✘ | ◐ (test sandboxes) | ✘ | ✘ | Phase 3 |
| Browser dashboard | ✘ (in-TD panel) | ✘ | ✘ | ✘ | ✔ local, served by the component |
| Code Mode | ✘ | ✘ (`batch_operations`) | ◐ (`describe_td_tools`) | ✘ | optional, policy-checked facade (Phase 3) |
| Source-first / diffable | ✘ (binary distribution) | ✔ (TDXN, diff driver) | ✘ | ? | ✔ text source → built `.tox` |
| Automated tests | 900+ claimed | 5,767 claimed + CI | ? | ? | in-TD runner + pure-Python unit tests in CI |
| Performance characteristics | ? (no published benchmarks; large-project behaviour listed as untested) | ◐ checkpoints ~3–6 ms; `save_project` blocks TD 15 s+; main-thread stall flag | ? | ? | targets defined, unmeasured (system design §6) |
| Deployment | `.tox` from Releases + in-component updater (version/checksum JSON) | `.tox` + self-update (sha256, no downgrade, backup) + per-project `.venv` | npm package + `.tox` | clone + `.tox` | `.tox` + sha256 manifest from GitHub Releases; no updater planned yet |
| License | restrictive, non-OSI | MIT | MIT | none detected | **owner decision** |
| Min TD | 2025.33230 | 2025.33230 | ? | ? | **2025.32440** (owner D10; embedded transport, S6) |

## 5. Key conclusions [I]

1. **A separate Node.js server is likely unnecessary (hypothesis, to be confirmed by spike S1).** The spec's stateless Streamable HTTP maps naturally onto the Web Server DAT's `onHTTPRequest`. johnsabath's README *documents* such an implementation (not run here), and TDMCP documents an in-TD server, though its serving mechanism is [U]. Node remains relevant only as an optional client-side bridge for clients without HTTP support.
2. **The official Python SDK is a poor fit for in-process use.** Its heavy compiled dependencies are not bundled, the work PC may block PyPI, and uvicorn/anyio conflicts with TD's main-thread model. A small hand-rolled protocol layer over the bundled `jsonschema` is lower risk, with conformance tests against the spec. Re-evaluate if the SDK gains a transport-agnostic, dependency-light core.
3. **The differentiator is enforceable security and governance.** No reviewed project does server-side scoped permissions, per-agent identity, per-call approvals and tamper-evident audit together.
4. **Source-first is solved best by Embody (MIT).** TDflowMCP must not install Embody into its own repo (see issue #194). Interop with TDXN is a candidate for later evaluation, not a dependency.
5. **Code Mode is a pattern, not a dependency.** In-process code cannot be sandboxed. Treat Code Mode as a policy-checked facade plus approvals, not as a security boundary.
6. **Do not reuse TDMCP code.** Its license is revocable, TD-bound and non-OSI. Reference only, from public documentation.
