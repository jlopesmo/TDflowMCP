"""Code Mode (ADR-0004, Phase 3): run a short Python script against a ``tdflow`` facade. Pure Python, no `td`.

What it is:
- The script runs on the HTTP handler thread, **not** on TouchDesigner's main thread, with restricted builtins and
  no ``import``. Its only way to reach TouchDesigner is the ``tdflow`` facade.
- Every facade call is a normal tool call made through the engine under the calling agent's identity: the same
  scopes, hard denies, approvals, kill switch, durable intent and outcome. Facade calls return plain data (dicts,
  lists, strings, numbers), never TouchDesigner objects.
- Budgets: at most ``MAX_CALLS`` facade calls and ``MAX_SECONDS`` of wall time; checked between calls.

What it is NOT: a sandbox. Python introspection can escape any restricted namespace, and the script runs inside
TouchDesigner's process. That is why running a script is itself an ``execute`` action that needs the user's
approval of the exact script text (security-model §5), and why the namespace is described as a mistake-reducer.
Raw ``exec`` with full ``td`` access does not exist in this version.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

MAX_CALLS = 200
MAX_SECONDS = 20.0
MAX_SCRIPT_CHARS = 20000
MAX_OUTPUT_CHARS = 20000

SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list", "map",
    "max", "min", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "Exception",
    "ValueError", "KeyError", "IndexError", "TypeError", "True", "False", "None") if n not in ("True", "False", "None")}


class ScriptError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


class Budget(Exception):
    pass


def check_script(code: str) -> None:
    """Static refusals before anything runs. These are guard rails, not a security boundary."""
    if not isinstance(code, str) or not code.strip():
        raise ScriptError("INVALID_ARGS", "the script is empty")
    if len(code) > MAX_SCRIPT_CHARS:
        raise ScriptError("INVALID_ARGS", f"the script is longer than {MAX_SCRIPT_CHARS} characters")
    try:
        compile(code, "<tdflow-script>", "exec")
    except SyntaxError as e:
        raise ScriptError("SCRIPT_SYNTAX", f"line {e.lineno}: {e.msg}") from None
    for word in ("import", "__", "exec(", "eval(", "open(", "globals", "locals", "getattr", "setattr", "compile("):
        if word in code:
            raise ScriptError("SCRIPT_REFUSED", f"scripts cannot use {word.strip('(')!r}; use the tdflow facade")


class Facade:
    """``tdflow.<tool>(**args)`` for every tool the agent may call, minus the script tool itself. Each call goes
    through ``call(tool, args)`` (the engine) and returns the tool's structured result as plain data."""

    def __init__(self, call: Callable[[str, dict], dict], tools: List[str], clock=time.monotonic):
        self._call, self._tools, self._clock = call, set(tools), clock
        self._t0, self.calls, self.log = clock(), 0, []

    def __getattr__(self, name: str):
        tool = "tdflow_" + name
        if name.startswith("_") or tool not in self._tools or tool == "tdflow_execute_script":
            raise AttributeError(f"tdflow has no {name}")

        def invoke(**kwargs):
            if self.calls >= MAX_CALLS:
                raise Budget(f"more than {MAX_CALLS} tdflow calls")
            if self._clock() - self._t0 > MAX_SECONDS:
                raise Budget(f"the script ran longer than {MAX_SECONDS:.0f} s")
            self.calls += 1
            res = self._call(tool, json.loads(json.dumps(kwargs, default=str)))   # plain data in
            sc = dict((res or {}).get("structuredContent") or {})
            self.log.append({"tool": tool, "ok": not (res or {}).get("isError"), "code": sc.get("code"),
                             "audit_seq": sc.get("audit_seq")})
            if (res or {}).get("isError"):
                raise ScriptError(str(sc.get("code") or "TOOL_ERROR"), str(sc.get("message") or "")[:300])
            return json.loads(json.dumps(sc, default=str))                          # plain data out
        return invoke

    def __dir__(self):
        return sorted(t[len("tdflow_"):] for t in self._tools if t != "tdflow_execute_script")


def run(code: str, call: Callable[[str, dict], dict], tools: List[str]) -> Dict[str, Any]:
    """Run an (approved) script. Returns {"ok", "result", "output", "calls", "log", "error"?}."""
    check_script(code)
    fac = Facade(call, tools)
    out: List[str] = []

    def emit(*parts):
        text = " ".join(str(p) for p in parts)
        if sum(len(x) for x in out) < MAX_OUTPUT_CHARS:
            out.append(text[:MAX_OUTPUT_CHARS])
    env = {"__builtins__": dict(SAFE_BUILTINS, print=emit), "tdflow": fac}
    result: Optional[Any] = None
    err: Optional[dict] = None
    try:
        exec(compile(code, "<tdflow-script>", "exec"), env, env)   # noqa: S102 - approved, facade-only script
        result = env.get("result")
    except ScriptError as e:
        err = {"code": e.code, "message": e.message}
    except Budget as e:
        err = {"code": "BUDGET_EXCEEDED", "message": str(e)}
    except Exception as e:                                    # the script's own error; never TD internals
        err = {"code": "SCRIPT_ERROR", "message": f"{type(e).__name__}: {str(e)[:300]}"}
    try:
        result = json.loads(json.dumps(result, default=str))
    except (TypeError, ValueError):
        result = str(result)[:2000]
    rep = {"ok": err is None, "result": result, "output": "\n".join(out)[:MAX_OUTPUT_CHARS], "calls": fac.calls,
           "log": fac.log[:MAX_CALLS]}
    if err:
        rep["error"] = err
    return rep
