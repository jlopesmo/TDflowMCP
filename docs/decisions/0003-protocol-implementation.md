# ADR-0003 — Hand-rolled MCP protocol layer

**Status:** Proposed (2026-11-22).

## Context
The official `mcp` Python SDK (2.3.0, MIT, Python ≥ 3.10) depends on pydantic, starlette, uvicorn, anyio, httpx, pyjwt and pywin32 ([PyPI](https://pypi.org/project/mcp/)). None are bundled with TD 2025.32440 (verified locally). The work PC may block package registries. Envoy solves this with a per-project `.venv` and a worker thread. johnsabath/touchdesigner-mcp shows a dependency-free in-DAT implementation is feasible.

## Decision
Implement a small protocol layer in `src/tdflowmcp/protocol/` using only the stdlib plus the bundled `jsonschema`:
- JSON-RPC 2.0 framing and error codes.
- MCP **2026-07-28** stateless requests: `_meta` protocol version and capabilities, and `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` header–body consistency (400 with `-32020` on mismatch).
- **Legacy compatibility:** the `initialize` / `notifications/initialized` flow for 2025-11-25 clients.
- Phase 1 methods: `tools/list`, `tools/call`, `ping`. Resources and prompts come later.
- A conformance test suite in `tests/unit/protocol/` derived from the specification's examples.
- `jsonschema` is bundled in TD but not in plain CPython. CI therefore installs `jsonschema==4.23.0` from `requirements-dev.txt` (test-only, pinned to the TD 2025.32440 bundle). Runtime code must not require any other package, and must work with the TD-bundled version on the minimum supported build. If the minimum build bundles a different version, the pin follows it.

## Alternatives
Vendor the SDK into a venv (Envoy approach); run the SDK in a sidecar process. Both remain fallbacks if conformance cost grows.

## Consequences
We own spec tracking. Mitigation: isolate the protocol layer behind an interface, and once implemented, keep a supported-versions table in `current-state.md`.

## Validation
Phase 1 acceptance: protocol unit tests pass on CPython 3.11, and a live session with at least Claude Code (plus one legacy-era client if available) is recorded in `validation-index.md`.
