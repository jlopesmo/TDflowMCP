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

## Implementation notes (Phase 3, 2026-11-23)
What exists ([record](../research/2026-11-23-phase-3-code-mode.md)), and where it differs from the decision above:
- **Off by default:** a dashboard setting, stored in private per-user storage and audited when changed. While it is off, the tool is not listed and calls answer `CODE_MODE_OFF`.
- **Approval always**, not only by default: running a script is `execute` on a declared `scope` (an existing COMP), and the user approves the exact text (its SHA-256 is bound).
- **The engine enforces** the facade's tool allowlist (path-scoped operator tools only: no undo, memory, project or script tools), the scope, the call budget and the kill switch per call, and keeps the call log. A line tracer stops pure-Python loops on the time budget or the kill switch. Calls that need approval on their own are refused inside a script.
- **Deviation: no single undo block.** Facade calls are ordinary audited calls, each reversible on its own with `tdflow_undo`, newest first. One TouchDesigner undo block around a script would have to span many main-thread steps that other agents and the user can interleave with; this is not attempted.
- **Not built:** `tdflow_search_api` / `tdflow_describe_api`. The facade is the registered tools themselves (`tdflow.<tool>(**args)` with the same arguments), described in the tool's description and the Skill.
- **Benchmark measured** (gate below): the same 20 parameter changes took 20 calls, 1750 ms and 5613 bytes as separate calls, against 1 call, 1534 ms and 1978 bytes as one script, plus one approval ([record](../research/2026-11-23-phase-4-extensions.md)). Bytes are not model tokens; no further benefit is claimed.

## Validation gates
- Phase 1B enforcement acceptance passes first.
- Then the Phase 3 checks: facade calls are policy-checked, results contain no TD objects, approval is required by default, and the token benchmark is measured before any benefit is claimed.
