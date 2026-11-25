# ADR-0003 — Hand-rolled MCP protocol layer

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to the conformance and live-client gates below.

## Context
The official `mcp` Python SDK (2.3.0, MIT, Python ≥ 3.10) depends on pydantic, starlette, uvicorn, anyio, httpx, pyjwt and pywin32 ([PyPI](https://pypi.org/project/mcp/)). None are bundled with TD 2025.32440 (verified locally). The work PC may block package registries. Envoy solves this with a per-project `.venv` and a worker thread. johnsabath/touchdesigner-mcp shows a dependency-free in-DAT implementation is feasible.

## Decision
Implement a small protocol layer in `src/tdflowmcp/protocol/` using only the stdlib plus the bundled `jsonschema`:
- JSON-RPC 2.0 framing and error codes.
- MCP **2026-07-28** stateless requests: `_meta` protocol version and capabilities, and `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` header–body consistency (400 with `-32020` on mismatch).
- **Legacy compatibility:** the `initialize` / `notifications/initialized` flow for 2025-11-25 clients.
- Phase 1 methods: `tools/list`, `tools/call`, `ping`. Resources and prompts come later.
- A conformance test suite in `tests/unit/protocol/` derived from the specification's examples.
- `jsonschema` is bundled in TD but not in plain CPython. CI therefore installs `jsonschema` from `requirements-dev.txt` as a test-only dependency. The pin is **provisional** at 4.23.0 (observed in the local 2025.32440 bundle) and matches the version bundled with the minimum build 2025.32440 (owner D10). It must follow the bundle if the minimum build changes. Runtime code must not require any other package, and must work with the TD-bundled version on the minimum supported build. If the minimum build bundles a different version, the pin follows it.

## Alternatives
Vendor the SDK into a venv (Envoy approach); run the SDK in a sidecar process. Both remain fallbacks if conformance cost grows.

## Consequences
We own spec tracking. Mitigation: isolate the protocol layer behind an interface, and once implemented, keep a supported-versions table in `current-state.md`.


## Validation gates
- **Implemented in Phase 1B** ([record](../research/2026-11-22-phase-1b-mcp-core.md)): `src/tdflowmcp/protocol.py` (2026-07-28 stateless + legacy) and `schema.py`.
  - **Deviation from this ADR:** runtime validation uses a stdlib JSON Schema *subset* validator rather than `jsonschema`, because plain CPython here has no `jsonschema` and nothing may be installed. Under TouchDesigner's Python a unit test checks every tool schema with the bundled `jsonschema` 4.23 and confirms both validators agree.
- Protocol conformance unit tests pass locally on CPython 3.11.8 and TD Python 3.11.10, and both eras pass in the in-TD acceptance suite. CI runs are **not observed** (B3).
- A live Claude Code session works against a disposable project, and the result is recorded in `validation-index.md`. **Open: Phase 1C item 21.** Running a client needs its configuration, which TDflowMCP never writes.
- If the cost of tracking the spec becomes excessive, this ADR reopens and the SDK fallbacks are re-evaluated.
