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
  compare_tops   read on both args.left and args.right
  set_params     update on args.path per parameter; execute for code effects; read on literal op() references
  create         create on args.parent/args.name (+ execute for executable types)
  delete         delete on args.path/** (count = subtree size)
  wire           update on args.dst, read on args.src
  dat_text       update on args.path (+ execute unless the DAT is data)
  undo           inverse actions of the agent's last audited mutation
  script         Code Mode (Phase 3): execute on args.scope; always approval of the exact script text
  copy           Phase 3 experiment: read on args.path, create per copied operator (+ execute for code in it)
  promote        delete of the original + update (rename) of the experiment args.path
  discard        delete of the experiment args.path/** (only an experiment)
  save           execute on the project root (approval only)
  lease          Phase 4 scope lease: needs update on args.scope; other agents' changes there are refused (L2)
  memory         project memory (Phase 2A): read on the task's scopes; records are written only by the
                 memory path in the engine, never as operator targets (tdflowProject stays H4-denied)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tdflowmcp import schema
from tdflowmcp.policy import ACTIONS

RESOLVERS = {"none", "project", "read_path", "read_children", "read_tree", "set_params", "create", "delete",
             "wire", "dat_text", "undo", "memory", "script", "copy", "promote", "discard", "save", "lease",
             "custom_params", "compare_tops"}
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
    # Owner D25: the caller's own effective capabilities, kept apart from what TDflowMCP supports, and the address
    # of the dashboard's Capabilities page (no credential: the browser must be paired in TouchDesigner).
    T(Tool("tdflow_capabilities", "My capabilities", "What YOU may do in this project right now, as the server "
           "enforces it: available, approval required (you may ask; the user approves each call), and what is not "
           "available to you (denied by your role, scopes or a setting; not implemented; protected for everyone). "
           "Also says how the user opens the dashboard's Capabilities page (Open Dashboard in TouchDesigner), where they "
           "see every capability and can change your permissions. Use it when asked what you can do.", EMPTY, (), "none", "capabilities",
           read_only=True, idempotent=True))
    T(Tool("tdflow_project_info", "Project info", "TouchDesigner build, project name, FPS and the top-level "
           "components under the project root that you may read.", EMPTY, ("read",), "project", "project_info",
           read_only=True, idempotent=True))
    T(Tool("tdflow_list_operators", "List operators", "Children of a COMP with type, family, inputs and node "
           "position (x, y), paginated. Protected or unpermitted operators are filtered out (reported as 'filtered').",
           obj({"path": PATH, "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]),
           ("read",), "read_children", "list_operators", read_only=True, idempotent=True))
    T(Tool("tdflow_get_operator", "Get operator", "Type, family, flags, inputs, outputs and errors of one operator. "
           "Request connectors for scoped port indexes, peer connectors and a Base COMP's exposed Out operator.",
           obj({"path": PATH, "connectors": {"type": "boolean"}}, ["path"]),
           ("read",), "read_path", "get_operator", read_only=True, idempotent=True))
    T(Tool("tdflow_list_operator_types", "Find operator types", "Search the local, TDflowMCP-supported operator "
           "catalog by family, exact or partial name, or purpose. Request guidance for selected parameter names, "
           "styles and uses on the verified runtime. Read the actual parameters on an instance with "
           "tdflow_get_parameters. Creation still checks your permissions and the destination.",
           obj({"path": PATH, "query": {"type": "string", "maxLength": 80},
                "guidance": {"type": "boolean"},
                "family": {"type": "string", "enum": ["TOP", "CHOP", "SOP", "DAT", "MAT", "COMP", "POP"]},
                "offset": {"type": "integer", "minimum": 0, "maximum": 1000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 60}}, ["path"]),
           ("read",), "read_children", "list_operator_types", read_only=True, idempotent=True))
    T(Tool("tdflow_get_parameters", "Get parameters", "Parameter constants, modes, expression source and defaults, "
           "optionally for one parameter page or only the named parameters. Expression, bind and export values "
           "are not evaluated. Use tdflow_get_errors for reported runtime errors.",
           obj({"path": PATH, "page": {"type": "string", "maxLength": 64},
                "names": {"type": "array", "maxItems": 50, "items": {"type": "string", "minLength": 1, "maxLength": 64}},
                                                      "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                                                      "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]),
           ("read",), "read_path", "get_parameters", read_only=True, idempotent=True))
    T(Tool("tdflow_set_parameters", "Set parameters", "Use values to set constants or replace expressions with "
           "constants. Use expressions to set Python expression source. It checks syntax and simple literal op() "
           "references before requesting approval. Expression changes, including replacement with a constant, "
           "reference parameters and Execute DAT settings "
           "require scoped execute and approval of the exact call. Repeat the identical call after approval. "
           "Check tdflow_get_errors and the resulting output. Reversible with tdflow_undo.",
           obj({"path": PATH, "values": {"type": "object", "maxProperties": 50, "additionalProperties":
                                         {"type": ["string", "number", "integer", "boolean"]}},
                "expressions": {"type": "object", "maxProperties": 50,
                                "additionalProperties": {"type": "string", "maxLength": 2000}}}, ["path"]),
           ("update", "execute?", "read?"), "set_params", "set_parameters", idempotent=True, reversible="td_undo"))
    custom_name = {"type": "string", "maxLength": 32, "pattern": r"^[A-Z][a-z0-9]*$"}
    short_text = {"type": "string", "minLength": 1, "maxLength": 64}
    choice = obj({"name": {"type": "string", "maxLength": 32, "pattern": r"^[a-z][a-z0-9_]*$"},
                  "label": short_text}, ["name"])
    definition = obj({"name": custom_name, "label": short_text,
                      "type": {"type": "string", "enum": ["float", "int", "toggle", "menu", "string", "pulse"]},
                      "default": {"type": ["boolean", "string", "number"], "maxLength": 512},
                      "range": {"type": "array", "minItems": 2, "maxItems": 2,
                                "items": {"type": "number", "minimum": -1e9, "maximum": 1e9}},
                      "choices": {"type": "array", "minItems": 1, "maxItems": 16, "items": choice}}, ["name", "type"])
    T(Tool("tdflow_edit_custom_parameters", "Edit custom controls", "Define or update constant scalar controls "
           "on one Base or Container COMP parameter page. Provide definitions in display order or a complete "
           "order for that page. Existing current values are kept. This requires scoped update, execute and exact-call "
           "approval. Use approved expressions to connect behavior. Pulse controls are defined here and pulsed by the owner.",
           obj({"path": PATH, "page": {"type": "string", "maxLength": 32, "pattern": r"^[A-Za-z][A-Za-z0-9 ]*$"},
                "definitions": {"type": "array", "maxItems": 16, "items": definition},
                "order": {"type": "array", "minItems": 1, "maxItems": 80, "items": custom_name}}, ["path", "page"]),
           ("read", "update", "execute"), "custom_params", "edit_custom_parameters", reversible="snapshot"))
    T(Tool("tdflow_create_operator", "Create operator", "Create an operator by type and name inside a COMP. "
           "Without x and y it is placed to the right of the existing nodes (the answer gives its position). "
           "The six supported Execute DAT types need scoped execute and approval and start inactive. "
           "Script CHOP, SOP and TOP and network operator creation are unavailable. "
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
    T(Tool("tdflow_get_dat_text", "Get DAT text", "Read authored Table, Text or Execute DAT source (size-capped) "
           "and its exact source_sha256. Computed, wired and file-linked DATs are refused. "
           "Supply proposed_text to validate Python syntax without running it and get a bounded unified diff.",
           obj({"path": PATH, "max_chars": {"type": "integer", "minimum": 1, "maximum": 200000},
                "proposed_text": {"type": "string", "maxLength": 200000}}, ["path"]),
           ("read",), "read_path", "get_dat_text", read_only=True, idempotent=True))
    T(Tool("tdflow_set_dat_text", "Set DAT text", "Replace DAT text. For a Python Text DAT module, use language: "
           "python to check syntax. Execute DAT source is always checked as Python and the callback must be inactive. "
           "Pass expected_sha256 from tdflow_get_dat_text's source_sha256 to require the exact prior source. "
           "Code writes require scoped execute and approval of the exact call. Repeat the identical call after "
           "approval. Reversible with tdflow_undo using a durable TDflowMCP snapshot.",
           obj({"path": PATH, "text": {"type": "string", "maxLength": 200000},
                "language": {"type": "string", "enum": ["python"]},
                "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, ["path", "text"]),
           ("update", "execute?"), "dat_text", "set_dat_text", idempotent=True, reversible="snapshot"))
    # Phase 4: scope leases for several agents working in one project.
    T(Tool("tdflow_lease_scope", "Lease scope", "Reserve a scope you can change (not the whole project), so other "
           "agents' changes inside it, and changes to components containing it, are refused (LEASED) while you work; "
           "reads stay open. The user can end a lease on the dashboard; renewing works for at most an hour. Leases end after ttl_s (default 600, at most "
           "1800) or when you release them (release: true). Leases do not survive a restart.",
           obj({"scope": PATH, "ttl_s": {"type": "integer", "minimum": 30, "maximum": 1800},
                "release": {"type": "boolean"}}, ["scope"]), ("update",), "lease", "lease_scope"))
    T(Tool("tdflow_capture_network", "Diagram network", "Render a scoped schematic of an exact component's readable "
           "direct children. The PNG labels the returned nodes by ID. Structured facts include their positions "
           "and exact ports for wires between shown nodes. One diagram per second per component. "
           "Verified on Windows with TouchDesigner 2025.32440.",
           obj({"path": PATH}, ["path"]), ("read",), "read_children", "capture_network",
           read_only=True, idempotent=True))
    # Phase 4: visual analysis. The adapter gates access to the frozen output.
    T(Tool("tdflow_capture_top", "Capture TOP", "Read an owner-locked TOP image (PNG or JPEG, up to 1920x1080, "
           "one per second). The TOP and enclosing components must have bypass off. "
           "Available on Windows with TouchDesigner 2025.32440.",
           obj({"path": PATH, "format": {"type": "string", "enum": ["png", "jpg"]}}, ["path"]), ("read",),
           "read_path", "capture_top", read_only=True, idempotent=True))
    T(Tool("tdflow_compare_tops", "Compare locked TOPs", "Compare two readable owner-locked cached TOPs. "
           "Reports dimensions and aggregate RGB differences for aligned samples up to 262144 pixels per input. "
           "Shares capture's one-call-per-second limit. Available on Windows with TouchDesigner 2025.32440.",
           obj({"left": PATH, "right": PATH}, ["left", "right"]), ("read",), "compare_tops", "compare_tops",
           read_only=True, idempotent=True))
    # Phase 4 (dogfood finding 5): bounded network summary with heuristic hints; reads facts, never cooks.
    T(Tool("tdflow_describe_network", "Describe network", "One network level (the children of a COMP) in one call: "
           "each readable operator's type, family, inputs, flags, errors and TouchDesigner's own cook count and "
           "last cook time, the wiring as edges, counts by family, and heuristic hints (slow cooks, frame budget, "
           "large locked textures, errors, outputs nothing reads). Set diagnostics to get observed faults, scoped "
           "upstream leads and ranked last CPU cook samples without cooking or evaluating parameters.",
           obj({"path": PATH, "diagnostics": {"type": "boolean"}}, ["path"]), ("read",), "read_children", "describe_network", read_only=True,
           idempotent=True))
    T(Tool("tdflow_get_errors", "Get errors", "Errors and warnings of an operator and its children (filtered).",
           obj({"path": PATH, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["path"]),
           ("read",), "read_tree", "get_errors", read_only=True, idempotent=True))
    T(Tool("tdflow_undo", "Undo", "Undo your own newest audited reversible change. Pass its audit_seq as "
           "expected_audit_seq to refuse recovery if a different change would be selected. Check verified in "
           "the result and inspect the affected state again.",
           obj({"expected_audit_seq": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807}}),
           ("update", "create", "delete"), "undo", "undo"))
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
    # Phase 3: experiments (a copy to try changes on) and approval-only project save.
    T(Tool("tdflow_experiment_create", "Create experiment", "Copy a component next to itself as an experiment to "
           "try changes on, leaving the original untouched. The copy is classified like creating each operator in "
           "it (at most about 50 operators per call); code inside it (executable operators, expressions, references) "
           "needs the user's approval, and copied executable operators are switched off. Undo with tdflow_undo.",
           obj({"path": PATH, "name": NAME, "inert": {"type": "boolean", "description": "true: a cook-disabled "
                "checkpoint for an isolated constant TOP component in the current running project"}},
               ["path", "name"]), ("read", "create", "execute?"), "copy",
           "experiment_create", reversible="td_undo"))
    T(Tool("tdflow_experiment_promote", "Promote experiment", "Replace the verified original with its own "
           "experiment in the same running project. Checkpoint recovery requires unchanged checkpoint state, "
           "matching operator shape and grants on the replaced operators and changing parameters and inputs. "
           "Check verified for checkpoint recovery. Undo with tdflow_undo.",
           obj({"path": PATH}, ["path"]), ("delete", "update"), "promote", "experiment_promote",
           destructive=True, reversible="td_undo"))
    T(Tool("tdflow_experiment_discard", "Discard experiment", "Delete an experiment made by "
           "tdflow_experiment_create (nothing else). Undo with tdflow_undo.", obj({"path": PATH}, ["path"]),
           ("delete",), "discard", "experiment_discard", destructive=True, reversible="td_undo"))
    T(Tool("tdflow_save_project", "Save project", "Save the open project to its file, like Ctrl+S. Always needs the "
           "user's approval. Not reversible.", EMPTY, ("execute",), "save", "save_project", destructive=True))
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
