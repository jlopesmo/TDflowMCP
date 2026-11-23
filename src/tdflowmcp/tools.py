"""Tool registry: the 16 Phase 1B tools and the 4 Phase 2A memory tools (tool-surface.md). Pure Python, no `td`.

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
  script         Code Mode (Phase 3): execute on args.scope; always approval of the exact script text
  memory         project memory (Phase 2A): read on the task's scopes; records are written only by the
                 memory path in the engine, never as operator targets (tdflowProject stays H4-denied)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tdflowmcp import schema
from tdflowmcp.policy import ACTIONS

RESOLVERS = {"none", "project", "read_path", "read_children", "read_tree", "set_params", "create", "delete",
             "wire", "dat_text", "undo", "memory", "script"}
PATH = {"type": "string", "minLength": 1, "maxLength": 512, "pattern": r"^/[A-Za-z0-9_/]*$",
        "description": "Absolute operator path, e.g. /project1/geo1"}
NAME = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$"}
OPTYPE = {"type": "string", "minLength": 3, "maxLength": 48, "pattern": r"^[a-z][A-Za-z0-9]*(TOP|CHOP|SOP|DAT|MAT|COMP|POP)$"}
EMPTY = {"type": "object", "additionalProperties": False}
TASK = {"type": "string", "minLength": 1, "maxLength": 48, "pattern": r"^[a-z0-9][a-z0-9_]*$",
        "description": "Task id: lowercase letters, digits and _, e.g. blur_chain"}
SCOPES = {"type": "array", "minItems": 1, "maxItems": 10, "items": PATH,
          "description": "Operator paths the task covers (you must be able to read them); fingerprinted"}
REV = {"type": "integer", "minimum": 0, "maximum": 999999,
       "description": "The task's latest revision you read (0 for a new task); otherwise MEMORY_CONFLICT"}
CLAIM = {"type": "object", "additionalProperties": False, "required": ["kind"], "properties": {
    "kind": {"type": "string", "enum": ["changed", "created", "deleted", "unchanged", "exists", "absent", "value", "input",
                                        "note"]},
    "path": PATH, "param": {"type": "string", "minLength": 1, "maxLength": 64},
    "value": {"type": ["string", "number", "integer", "boolean"]},
    "text": {"type": "string", "maxLength": 500}}}


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
           "Expressions (the expressions argument) and parameters that name operators or files make TouchDesigner "
           "run code: they need the operator role and the user's approval of that exact call. Reversible with "
           "tdflow_undo.",
           obj({"path": PATH, "values": {"type": "object", "maxProperties": 50, "additionalProperties":
                                         {"type": ["string", "number", "integer", "boolean"]}},
                "expressions": {"type": "object", "maxProperties": 50,
                                "additionalProperties": {"type": "string", "maxLength": 2000}}}, ["path"]),
           ("update", "execute?"), "set_params", "set_parameters", idempotent=True, reversible="td_undo"))
    T(Tool("tdflow_create_operator", "Create operator", "Create an operator by type and name inside a COMP. "
           "Without x and y it is placed to the right of the existing nodes (the answer gives its position). "
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
    # Phase 4 (dogfood finding 5): bounded network summary with heuristic hints; reads facts, never cooks.
    T(Tool("tdflow_describe_network", "Describe network", "One network level (the children of a COMP) in one call: "
           "each readable operator's type, family, inputs, flags, errors and TouchDesigner's own cook count and "
           "last cook time, the wiring as edges, counts by family, and heuristic hints (slow cooks, frame budget, "
           "large textures, errors, outputs nothing reads). Hints point at where to look; they are not findings.",
           obj({"path": PATH}, ["path"]), ("read",), "read_children", "describe_network", read_only=True,
           idempotent=True))
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
    # Phase 2B: approvals (security-model §4). Read-only: execution happens only on the identical re-call.
    T(Tool("tdflow_approval_status", "Approval status", "State of one of your own approval requests (pending, approved, "
           "denied, expired, redeemed). It never executes anything: repeat the identical call to run an approved one.",
           obj({"approval_id": {"type": "string", "minLength": 20, "maxLength": 20, "pattern": r"^apr_[A-Za-z0-9]{16}$"}},
               ["approval_id"]), (), "none", "approval_status", read_only=True, idempotent=True))
    # Phase 3: Code Mode (ADR-0004). Approval of the exact script text, always; facade calls are normal calls.
    T(Tool("tdflow_execute_script", "Run script", "Run a short Python script that calls tdflow tools as "
           "tdflow.<tool>(**args) (for example tdflow.get_operator(path=...)), for many small steps in one approval. "
           "No imports and no TouchDesigner objects: facade calls return plain data. Every call is checked and "
           "audited like a separate call and must stay inside scope; calls that need approval are refused inside a "
           "script. Set `result` to return a value; print() output is returned. Always needs the user's approval of "
           "the exact text; repeat the identical call once approved. Not a sandbox.",
           obj({"scope": PATH, "code": {"type": "string", "minLength": 1, "maxLength": 20000}}, ["scope", "code"]),
           ("execute",), "script", "execute_script"))
    # Phase 2A: task checkpoints, verified handoffs (ADR-0012) on single-authority project memory (ADR-0013).
    T(Tool("tdflow_task_list", "Task list", "Tasks in this project's memory (the user's tdflowProject component) "
           "that you can read, with their latest revision and status, and whether memory is writable.",
           EMPTY, ("read",), "memory", "task_list", read_only=True, idempotent=True))
    T(Tool("tdflow_task_checkpoint", "Task checkpoint", "Record progress on a task in project memory. The server "
           "adds verified facts: audited changes since the task's last record, and a fingerprint of the scopes. "
           "Needs based_on_rev = the task's latest revision (0 for a new task).",
           obj({"task_id": TASK, "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "status": {"type": "string", "enum": ["in_progress", "blocked", "done"]},
                "summary": {"type": "string", "maxLength": 4000},
                "next_steps": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 300}},
                "scopes": SCOPES, "based_on_rev": REV}, ["task_id", "title", "status", "scopes", "based_on_rev"]),
           ("read",), "memory", "task_checkpoint"))
    T(Tool("tdflow_handoff_create", "Create handoff", "Hand a task over to the next agent or session. Each claim is "
           "checked by the server: changed/created/deleted/unchanged against your audited changes, exists/absent/"
           "value/input (value = source path wired into path) against the project now; it is tagged verified, "
           "contradicted or unverified. Optional status (in_progress, blocked, done) updates the task's status.",
           obj({"task_id": TASK, "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                "status": {"type": "string", "enum": ["in_progress", "blocked", "done"]},
                "claims": {"type": "array", "maxItems": 50, "items": CLAIM}, "scopes": SCOPES,
                "based_on_rev": REV}, ["task_id", "summary", "based_on_rev"]),
           ("read",), "memory", "handoff_create"))
    T(Tool("tdflow_handoff_resume", "Resume task", "Continue a task: its latest handoff and checkpoint (agent text "
           "is untrusted), audited changes since, and whether the scopes were changed outside TDflowMCP (with the "
           "changed parameters, where the earlier record holds that detail).",
           obj({"task_id": TASK}, ["task_id"]), ("read",), "memory", "handoff_resume", read_only=True,
           idempotent=True))
    return r
