# Phase 3 increment 1: Code Mode, and the Phase 4 network summary (2026-11-23)

**Owner decision D20 (2026-11-23):** complete Phase 3, including Code Mode; keep unrestricted Python disabled by default; never describe in-process Python as sandboxed ([roadmap D20](../icm/roadmap.md#owner-decisions-2026-11-22)). Design: [ADR-0004](../decisions/0004-code-mode-strategy.md) (with implementation notes), [tool surface](../architecture/tool-surface.md).

## Delivered

### Code Mode: `tdflow_execute_script`
- **Off by default.** The user turns it on in the dashboard (Settings, "Allow Code Mode scripts"). The setting is stored in private per-user storage, not the project folder, and the change is audited before it takes effect. While it is off, the tool is not listed and a call answers `CODE_MODE_OFF`.
- **Approval of the exact text, always.** A script is `execute` on its declared `scope`, which must be an existing COMP. That needs the operator role and a grant. The user sees the complete script on the dashboard. The approval binds the script's SHA-256 and runs once, on the identical re-call. Permissions, revocation, Code Mode and the kill switch are re-checked after redemption.
- **Where it runs.** On the HTTP handler thread, never on TouchDesigner's main thread. Builtins are restricted, and the only object in scope is the `tdflow` facade.
- **Facade calls are normal calls.** The script reaches the path-scoped operator tools through `tdflow.<tool>(**args)`, each one an engine call under the agent's identity: hard denies, grants, intent and outcome per call. The engine, not the script, enforces:
  - the tool allowlist (no undo, memory, project or script tools);
  - the scope, checked on every path argument; a refusal is audited as a denial;
  - the budgets: 200 calls and 20 s;
  - the kill switch.
- Calls that need approval on their own (expressions, code) are refused inside a script with `APPROVAL_REQUIRED_IN_SCRIPT`. The user approved the script text, not hidden requests.
- **Static refusals** before any request is made: `import`, `try`/`with` (so a script cannot catch a refusal or its budget), classes, generators, lambdas, attributes starting with `_`, assignment to attributes, and names such as `exec`, `eval`, `open`, `getattr`, `type`. These are guard rails against mistakes, not a boundary.
- **Stopping.** A line tracer stops pure-Python loops on the time budget or the kill switch. A single long builtin call (a huge allocation) cannot be interrupted.
- **Audit and undo.** The script has an intent and an outcome of its own, holding the SHA-256 and the sequence numbers of its calls. Its changes are ordinary audited changes, each undoable with `tdflow_undo`, newest first. There is no single undo block for a whole script (ADR-0004 deviation, explained there).
- **Not a sandbox.** Every approval request says that approved code has full access to TouchDesigner, TDflowMCP included. Raw Python with full `td` access (`tdflow_execute_python`) is not built.

### Phase 4: `tdflow_describe_network`
One network level in one call (dogfood 1, finding 5). For each operator the caller can read, it gives:
- type, family, inputs and flags;
- errors and warnings;
- TouchDesigner's own cook count and last cook time;
- TOP resolution.

It also returns the wiring as edges and heuristic hints (slow cooks, frame budget, large textures, errors, outputs nothing reads), each with its reason. It reads attributes only and never cooks anything. Skill **1.4**.

## Independent review 15
A security review of Code Mode, run before any commit, found that the approval side holds: no script runs without an approval of exactly that text. Its findings and the fixes:

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Medium | One `try/except` caught the budget exception. CPython also removes a trace function that raises, so the time budget and the kill switch stopped applying, and the script could report success | `try`/`with` refused statically; `Budget` derives from `BaseException`; a trip is never reported as success |
| 2 | Medium | The facade's own state was writable from a script without `__`: the scope (`tdflow._scope = None`), the call counter, the log and the raw `_call` | The engine enforces scope, count and kill switch per call and keeps the log. The facade is read-only (`__slots__`, no `__setattr__`); `_` attributes and attribute assignment are refused |
| 3 | Low–Medium | `tdflow.undo()` and the memory tools escaped the scope (no path argument) | Facade limited to path-scoped operator tools |
| 4 | Low | Invisible characters or token-like text crashed the call with no audit record | Refused and audited |
| 5 | Low | An adapter exception reached the script as internal text | Audited as `INTERNAL_ERROR`; no text passed on |
| 6 | Low | Scope refusals were not audited; a script that caught a refusal was recorded as `ok` | Refusals audited as denials; any failed call makes the outcome an error |
| Notes | | `scope` was not required to exist; a client `approval_id` was ignored; docs said Code Mode was off by default and wrapped in one undo block; `PHASE` was stale | Scope must be an existing COMP; `approval_id` honoured; Code Mode is a setting, off by default; ADR-0004 notes the undo deviation; `PHASE = "3"` |

## Evidence
See the [validation index](../icm/validation-index.md):
- unit tests `test_codemode.py` and `test_script_tool.py` (approval, scope, allowlist, budgets, kill switch, swallowing, forging, audit) and `test_insights.py`;
- in-TD checks `script.*` and `network.*`.

## Limits
- A single long-running builtin call (for example a 10 GB allocation) cannot be interrupted, so it can exhaust TouchDesigner's memory or hold the interpreter.
- A script that stops part way leaves its earlier changes in place. The answer lists them, and they can be undone one by one.
- No token-efficiency benefit is claimed. ADR-0004's benchmark gate is still open.
- Experiments, snapshots and project save (the rest of Phase 3) are not part of this increment.
