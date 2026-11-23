"""Code Mode (ADR-0004, Phase 3): run a short Python script against a ``tdflow`` facade. Pure Python, no `td`.

What it is:
- The script runs on the HTTP handler thread, **not** on TouchDesigner's main thread, with restricted builtins and
  no ``import``. Its only way to reach TouchDesigner is the ``tdflow`` facade.
- Every facade call is a normal tool call made through the engine under the calling agent's identity: the same
  scopes, hard denies, kill switch, durable intent and outcome. The engine (not this module) enforces the script's
  scope, the call budget and the kill switch per call, and keeps the authoritative call log. Facade calls return
  plain data (dicts, lists, strings, numbers), never TouchDesigner objects.
- Budgets: at most ``MAX_CALLS`` facade calls and ``MAX_SECONDS`` of wall time. A line tracer on the script's own
  frames enforces the time limit and the kill switch inside pure-Python loops too; a single long-running builtin
  call (for example a huge ``sum(range(...))`` or allocation) cannot be interrupted until it returns.
- Static refusals (review 15): no ``import``, ``try``/``with`` (a script cannot catch the budget or a refusal), no
  classes, generators, lambdas or ``global``; no attribute starting with ``_``; no assignment to attributes; no
  names that reach the interpreter (``exec``, ``eval``, ``open``, ``getattr``, ...).

What it is NOT: a sandbox. Python introspection can escape any restricted namespace, and the script runs inside
TouchDesigner's process. That is why running a script is itself an ``execute`` action that needs the user's
approval of the exact script text (security-model §5), why Code Mode is off until the user turns it on, and why
the namespace is described as a mistake-reducer. Raw ``exec`` with full ``td`` access does not exist.
"""
from __future__ import annotations

import ast
import json
import sys
import time
from typing import Any, Callable, Dict, List, Optional

MAX_CALLS = 200
MAX_SECONDS = 20.0
MAX_SCRIPT_CHARS = 20000
MAX_OUTPUT_CHARS = 20000
PATH_KEYS = ("path", "parent", "src", "dst", "comp")
FILENAME = "<tdflow-script>"
# Review 15: only operator tools whose every target is named by a path argument (so the scope check is complete).
SCRIPT_TOOLS = ("tdflow_list_operators", "tdflow_get_operator", "tdflow_get_parameters", "tdflow_set_parameters",
                "tdflow_create_operator", "tdflow_delete_operator", "tdflow_connect", "tdflow_disconnect",
                "tdflow_get_dat_text", "tdflow_set_dat_text", "tdflow_get_errors", "tdflow_describe_network")

SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int", "isinstance", "len", "list", "map",
    "max", "min", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "ValueError")}
REFUSED_NAMES = {"exec", "eval", "open", "compile", "globals", "locals", "vars", "dir", "getattr", "setattr",
                 "delattr", "hasattr", "type", "object", "input", "breakpoint", "help", "memoryview", "super",
                 "classmethod", "staticmethod", "property"}
REFUSED_NODES = (ast.Import, ast.ImportFrom, ast.Try, getattr(ast, "TryStar", ast.Try), ast.With, ast.AsyncWith,
                 ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom, ast.Global, ast.Nonlocal,
                 ast.Lambda, ast.AsyncFor)


class ScriptError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


class Budget(BaseException):
    """Not an ``Exception``: nothing in a script can catch it (and ``try`` is refused anyway)."""


def check_script(code: str) -> None:
    """Static refusals before anything runs. Guard rails against mistakes, not a security boundary."""
    if not isinstance(code, str) or not code.strip():
        raise ScriptError("INVALID_ARGS", "the script is empty")
    if len(code) > MAX_SCRIPT_CHARS:
        raise ScriptError("INVALID_ARGS", f"the script is longer than {MAX_SCRIPT_CHARS} characters")
    try:
        tree = ast.parse(code, FILENAME)
    except SyntaxError as e:
        raise ScriptError("SCRIPT_SYNTAX", f"line {e.lineno}: {e.msg}") from None

    def refuse(node, what):
        raise ScriptError("SCRIPT_REFUSED", f"line {getattr(node, 'lineno', '?')}: scripts cannot use {what}; "
                                            "call tdflow.<tool>(...) with plain values")
    for node in ast.walk(tree):
        if isinstance(node, REFUSED_NODES):
            refuse(node, type(node).__name__.lower())
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                refuse(node, f"the attribute {node.attr!r}")
            if not isinstance(node.ctx, ast.Load):
                refuse(node, "assignment to an attribute")
        elif isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in REFUSED_NAMES):
            refuse(node, repr(node.id))
        elif isinstance(node, ast.FunctionDef) and node.decorator_list:
            refuse(node, "decorators")


class Facade:
    """``tdflow.<tool>(**args)`` for the script tools. Each call goes through ``call(tool, args)`` (the engine, which
    enforces scope, budget and kill switch) and returns the tool's structured result as plain data. The facade's own
    state cannot be changed from a script (``__slots__``, no ``__setattr__``; the static check refuses ``._``)."""
    __slots__ = ("_call", "_tools", "_clock", "_t0", "calls", "log", "tripped")

    def __init__(self, call: Callable[[str, dict], dict], tools: List[str], clock=time.monotonic):
        for k, v in (("_call", call), ("_tools", frozenset(t for t in tools if t in SCRIPT_TOOLS)), ("_clock", clock),
                     ("_t0", clock()), ("calls", 0), ("log", []), ("tripped", None)):
            object.__setattr__(self, k, v)

    def __setattr__(self, name, value):
        raise AttributeError("the tdflow facade is read-only")

    def _trip(self, why: str):
        object.__setattr__(self, "tripped", why)
        raise Budget(why)

    def __getattr__(self, name: str):
        tool = "tdflow_" + name
        if name.startswith("_") or tool not in self._tools:
            raise AttributeError(f"tdflow has no {name}")

        def invoke(**kwargs):
            if self.tripped is not None:
                raise Budget(self.tripped)
            if self.calls >= MAX_CALLS:
                self._trip(f"more than {MAX_CALLS} tdflow calls")
            if self._clock() - self._t0 > MAX_SECONDS:
                self._trip(f"the script ran longer than {MAX_SECONDS:.0f} s")
            args = json.loads(json.dumps(kwargs, default=str))                       # plain data in
            object.__setattr__(self, "calls", self.calls + 1)
            res = self._call(tool, args) or {}
            sc = dict(res.get("structuredContent") or {})
            self.log.append({"tool": tool, "ok": not res.get("isError"), "code": sc.get("code"),
                             "audit_seq": sc.get("audit_seq")})
            if res.get("isError"):
                raise ScriptError(str(sc.get("code") or "TOOL_ERROR"), str(sc.get("message") or "")[:300])
            return json.loads(json.dumps(sc, default=str))                          # plain data out
        return invoke

    def __dir__(self):
        return sorted(t[len("tdflow_"):] for t in self._tools)


def inside(scope: str, path: str) -> bool:
    s = scope.rstrip("/") or "/"
    return s == "/" or path == s or path.startswith(s + "/")


def run(code: str, call: Callable[[str, dict], dict], tools: List[str], stop: Callable[[], bool] = lambda: False,
        clock=time.monotonic) -> Dict[str, Any]:
    """Run an (approved) script on the calling thread. ``stop``: the kill switch, checked between lines.
    Returns {"ok", "result", "output", "calls", "log", "error"?}. The engine keeps its own authoritative log."""
    check_script(code)
    fac = Facade(call, tools, clock=clock)
    t0, lines, tripped = clock(), [0], []

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != FILENAME:
            return None                               # never trace the engine or TDflowMCP itself
        if event == "line":
            lines[0] += 1
            if not tripped and lines[0] % 64 == 0:
                if stop():
                    tripped.append("stopped: all agents were suspended")
                elif clock() - t0 > MAX_SECONDS:
                    tripped.append(f"the script ran longer than {MAX_SECONDS:.0f} s")
            if tripped:
                raise Budget(tripped[0])
        return tracer
    out: List[str] = []

    def emit(*parts):
        text = " ".join(str(p) for p in parts)
        if sum(len(x) for x in out) < MAX_OUTPUT_CHARS:
            out.append(text[:MAX_OUTPUT_CHARS])
    env = {"__builtins__": dict(SAFE_BUILTINS, print=emit), "tdflow": fac}
    result: Optional[Any] = None
    err: Optional[dict] = None
    prev = sys.gettrace()
    try:
        sys.settrace(tracer)
        try:
            exec(compile(code, FILENAME, "exec"), env, env)   # noqa: S102 - approved, facade-only script
        finally:
            sys.settrace(prev)
        result = env.get("result")
    except ScriptError as e:
        err = {"code": e.code, "message": e.message}
    except Budget as e:
        err = {"code": "BUDGET_EXCEEDED", "message": str(e)}
    except Exception as e:                                    # the script's own error; never TD internals
        err = {"code": "SCRIPT_ERROR", "message": f"{type(e).__name__}: {str(e)[:300]}"}
    if err is None and (tripped or fac.tripped):              # belt and braces: a trip is never a success
        err = {"code": "BUDGET_EXCEEDED", "message": tripped[0] if tripped else fac.tripped}
    if err is not None:
        result = None
    try:
        result = json.loads(json.dumps(result, default=str))
    except (TypeError, ValueError):
        result = str(result)[:2000]
    rep = {"ok": err is None, "result": result, "output": "\n".join(out)[:MAX_OUTPUT_CHARS], "calls": fac.calls,
           "log": list(fac.log)[:MAX_CALLS]}
    if err:
        rep["error"] = err
    return rep
