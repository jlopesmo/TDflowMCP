# ADR-0004 — Code Mode strategy

**Status:** Conditionally Accepted (owner, 2026-11-22) as direction. Delivery is Phase 3 and must not start before the Phase 1B enforcement gates pass.

## Context
Cloudflare Code Mode converts MCP tools into a typed API that models script against. Scripts execute in V8 isolates with no network, reaching tools only through bindings ([blog](https://blog.cloudflare.com/code-mode/), [package README](https://github.com/cloudflare/agents/blob/main/packages/codemode/README.md)). Its `DynamicWorkerExecutor` requires Cloudflare Workers. The target page ([codemode/mcp](https://developers.cloudflare.com/agents/tools/codemode/mcp/)) could not be fetched from this network because of TLS interception, so its details need re-verification. Vendor token-reduction claims (≈99.9 %, 98.7 %) are untested here. In-process Python inside TD cannot be sandboxed, and RestrictedPython disclaims being a sandbox.

## Decision
Provide Code Mode as an **optional, off-by-default** capability:
1. `tdflow_search_api` / `tdflow_describe_api`: progressive disclosure of the `tdflow` facade (Python signatures and docstrings generated from the same tool registry).
2. `tdflow_execute_script(code)`: runs Python with **only** the `tdflow` facade object and a restricted builtins set in scope. Every facade call goes through the policy engine under the calling agent's identity. A single undo block wraps the script, with op-count and time budgets.
3. Requires the `execute` action and, by default, approval. Documented as **not a security boundary**.
4. Raw `tdflow_execute_python` (full `td` access) is a separate tool, disabled by default.

Adopt Cloudflare's **pattern** (search/describe/execute, approve/reject/rollback), not its code.

## Alternatives
Out-of-process sandbox (Deno/QuickJS/Pyodide) with RPC back to TD: better isolation, but not bundled and slower. Revisit if unattended Code Mode is required.

## Consequences
Token-efficiency benefits must be measured by our own benchmark (a Phase 3 acceptance criterion) before being claimed.

## Validation gates
- Phase 1B enforcement acceptance passes first.
- Then the Phase 3 checks: facade calls are policy-checked, results contain no TD objects, approval is required by default, and the token benchmark is measured before any benefit is claimed.
