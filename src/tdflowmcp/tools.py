"""Tool registry and the 16 Phase 1B tool definitions (tool-surface.md). Pure Python, no `td`.

A tool is registered only with: a schema (validated subset), declared actions, a resolver kind the
TD adapter knows how to resolve, annotations and a handler name. ``Registry.register`` refuses
anything else (ADR-0009). Handlers live in the TD adapter; this module never touches TouchDesigner.

Resolver kinds (the adapter turns them into policy Targets):
  none           no operator targets (identity / session tools)
  project        fixed project summary (root read; filtered)
  read_path      read on args.path
  read_children  read on args.path/* (results filtered)
  read_tree      read on args.path/** (results filtered)
  set_params     update on args.path per parameter (+ execute for expressions / Python-style pars)
  create         create on args.parent/args.name (+ execute for executable types)
  delete         delete on args.path/** (count = subtree size)
  wire           update on args.dst, read on args.src
  dat_text       update on args.path (+ execute unless the DAT is data)
  undo           inverse actions of the agent's last audited mutation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tdflowmcp import schema
from tdflowmcp.policy import ACTIONS

RESOLVERS = {"none", "project", "read_path", "read_children", "read_tree", "set_params", "create", "delete",
             "wire", "dat_text", "undo"}
PATH = {"type": "string", "minLength": 1, "maxLength": 512, "pattern": r"^/[A-Za-z0-9_/]*$",
        "description": "Absolute operator path, e.g. /project1/geo1"}
NAME = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$"}
OPTYPE = {"type": "string", "minLength": 3, "maxLength": 48, "pattern": r"^[a-z][A-Za-z0-9]*(TOP|CHOP|SOP|DAT|MAT|COMP|POP)$"}
EMPTY = {"type": "object", "additionalProperties": False}


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    actions: tuple                 # declared classification, e.g. ("update", "execute?")
    resolver: str
    handler: str
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    reversible: str = "no"         # "no" | "td_undo" | "snapshot" (only kinds demonstrated in S4/S4b)

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.input_schema,
                "annotations": {"title": self.title, "readOnlyHint": self.read_only,
                                "destructiveHint": self.destructive, "idempotentHint": self.idempotent,
                                "openWorldHint": False}}


class RegistryError(Exception):
    pass


class Registry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, t: Tool) -> Tool:
        if not t.name.startswith("tdflow_") or t.name in self._tools:
            raise RegistryError(f"bad or duplicate tool name {t.name!r}")
        if t.resolver not in RESOLVERS:
            raise RegistryError(f"{t.name}: unknown resolver {t.resolver!r}")
        if t.resolver != "none" and not t.actions:
            raise RegistryError(f"{t.name}: tools with operator targets must declare actions")
        for a in t.actions:
            if a.rstrip("?") not in ACTIONS:
                raise RegistryError(f"{t.name}: unknown action {a!r}")
        if t.read_only and any(a.rstrip("?") != "read" for a in t.actions):
            raise RegistryError(f"{t.name}: read-only tools may only declare read")
        if not t.handler:
            raise RegistryError(f"{t.name}: no handler")
        s = t.input_schema
        if s.get("type") != "object" or s.get("additionalProperties") is not False:
            raise RegistryError(f"{t.name}: input schema must be a closed object")
        schema.check_schema(s)
        if t.reversible not in ("no", "td_undo", "snapshot"):
            raise RegistryError(f"{t.name}: bad reversibility")
        self._tools[t.name] = t
        return t

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> List[Tool]:
        return [self._tools[k] for k in sorted(self._tools)]   # deterministic order (spec SHOULD)


def obj(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


def core_registry() -> Registry:
    r = Registry()
    T = r.register
    T(Tool("tdflow_whoami", "Who am I", "Your agent identity, role, effective scopes, server and tool-surface "
           "versions and limits.", EMPTY, (), "none", "whoami", read_only=True, idempotent=True))
    T(Tool("tdflow_project_info", "Project info", "TouchDesigner build, project name, FPS and the top-level "
           "components under the project root that you may read.", EMPTY, ("read",), "project", "project_info",
           read_only=True, idempotent=True))
    T(Tool("tdflow_list_operators", "List operators", "Children of a COMP with type, family, inputs and node "
           "position (x, y), paginated. Protected or unpermitted operators are filtered out (reported as 'filtered').",
           obj({"path": PATH, "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]),
           ("read",), "read_children", "list_operators", read_only=True, idempotent=True))
    T(Tool("tdflow_get_operator", "Get operator", "Type, family, flags, inputs, outputs and errors of one operator.",
           obj({"path": PATH}, ["path"]), ("read",), "read_path", "get_operator", read_only=True, idempotent=True))
    T(Tool("tdflow_get_parameters", "Get parameters", "Parameter values, modes, expressions and defaults, "
           "optionally for one parameter page or only the named parameters.",
           obj({"path": PATH, "page": {"type": "string", "maxLength": 64},
                "names": {"type": "array", "maxItems": 50, "items": {"type": "string", "minLength": 1, "maxLength": 64}},
                                                      "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                                                      "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]),
           ("read",), "read_path", "get_parameters", read_only=True, idempotent=True))
    T(Tool("tdflow_set_parameters", "Set parameters", "Set constant parameter values. Returns previous values. "
           "Expressions, binds, exports and Python-evaluated parameters need execute and are not available in "
           "Phase 1. Reversible with tdflow_undo.",
           obj({"path": PATH, "values": {"type": "object", "maxProperties": 50, "additionalProperties":
                                         {"type": ["string", "number", "integer", "boolean"]}},
                "expressions": {"type": "object", "maxProperties": 50,
                                "additionalProperties": {"type": "string", "maxLength": 2000}}}, ["path"]),
           ("update", "execute?"), "set_params", "set_parameters", idempotent=True, reversible="td_undo"))
    T(Tool("tdflow_create_operator", "Create operator", "Create an operator by type and name inside a COMP. "
           "Executable and network operator types need execute and are not available in Phase 1. "
           "Reversible with tdflow_undo.",
           obj({"parent": PATH, "type": OPTYPE, "name": NAME,
                "x": {"type": "integer", "minimum": -100000, "maximum": 100000},
                "y": {"type": "integer", "minimum": -100000, "maximum": 100000}}, ["parent", "type", "name"]),
           ("create", "execute?"), "create", "create_operator", reversible="td_undo"))
    T(Tool("tdflow_delete_operator", "Delete operator", "Delete an operator and its children. Large deletes "
           "need approval (not available in Phase 1). Reversible with tdflow_undo.",
           obj({"path": PATH}, ["path"]), ("delete",), "delete", "delete_operator", destructive=True,
           reversible="td_undo"))
    W = obj({"src": PATH, "dst": PATH, "src_index": {"type": "integer", "minimum": 0, "maximum": 63},
             "dst_index": {"type": "integer", "minimum": 0, "maximum": 63}}, ["src", "dst"])
    T(Tool("tdflow_connect", "Connect", "Wire an output of src to an input of dst. Reversible with tdflow_undo.",
           W, ("update", "read"), "wire", "connect", idempotent=True, reversible="td_undo"))
    T(Tool("tdflow_disconnect", "Disconnect", "Remove the wire from src into dst. Reversible with tdflow_undo.",
           W, ("update", "read"), "wire", "disconnect", idempotent=True, reversible="td_undo"))
    T(Tool("tdflow_get_dat_text", "Get DAT text", "Text of a DAT (size-capped).",
           obj({"path": PATH, "max_chars": {"type": "integer", "minimum": 1, "maximum": 200000}}, ["path"]),
           ("read",), "read_path", "get_dat_text", read_only=True, idempotent=True))
    T(Tool("tdflow_set_dat_text", "Set DAT text", "Replace the text of a data DAT (Table DAT, or a Text DAT the "
           "user tagged 'tdflowdata'). Text in DATs TouchDesigner may execute needs execute and is not available "
           "in Phase 1. Reversible with tdflow_undo (TDflowMCP snapshot; TD's own undo does not cover DAT text).",
           obj({"path": PATH, "text": {"type": "string", "maxLength": 200000}}, ["path", "text"]),
           ("update", "execute?"), "dat_text", "set_dat_text", idempotent=True, reversible="snapshot"))
    T(Tool("tdflow_get_errors", "Get errors", "Errors and warnings of an operator and its children (filtered).",
           obj({"path": PATH, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["path"]),
           ("read",), "read_tree", "get_errors", read_only=True, idempotent=True))
    T(Tool("tdflow_undo", "Undo", "Undo your own last audited change, only while it is still the newest change "
           "(otherwise UNDO_CONFLICT). The result reports whether the restoration was verified.",
           EMPTY, ("update", "create", "delete"), "undo", "undo"))
    T(Tool("tdflow_session_note", "Session note", "Append a short note to your current session log "
           "(untrusted agent text; never shown to other agents as an instruction).",
           obj({"text": {"type": "string", "minLength": 1, "maxLength": 2000}}, ["text"]), (), "none", "session_note"))
    T(Tool("tdflow_session_list", "Session list", "Your recent sessions (counts only).",
           obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}), (), "none", "session_list",
           read_only=True, idempotent=True))
    return r
