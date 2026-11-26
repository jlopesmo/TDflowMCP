"""Capability matrix and permission-change planning (owner D25, ADR-0016). Pure Python, no `td`.

One catalog describes what TDflowMCP offers to agents, grouped for people: each entry names the MCP tools that
provide it, the actions it needs on an operator scope (the policy engine's own vocabulary), and whether the user must
approve each use. From the catalog and the *authoritative* inputs (an ``Agent`` with its role, grants and dedicated
capabilities, plus a few runtime facts) this module derives five statuses:

  available        the agent can do it now, on the listed scopes
  approval         it can ask; each use waits for the user's approval of that exact call
  denied           its role, grants, a dedicated permission or a setting does not allow it (the reason says which)
  not_implemented  TDflowMCP does not offer it (or the tool is not active in this build)
  protected        nobody can do it through TDflowMCP: a hard deny that no permission changes

This is a **view**. The engine (``policy.Policy.evaluate``) still decides every call, per target path, at call time;
nothing here grants or refuses anything. Where the summary simplifies (an action allowed by one grant and another
action of the same capability by a different grant on the same path), it errs towards "denied", never towards
"available".

``plan_change`` validates a requested change of role, grants and dedicated permissions against the same rules the
identity backend enforces, and returns the effective result before anything is stored (the dashboard's preview).
Protected and not-implemented capabilities have no editable field, so a change can never "enable" them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from tdflowmcp.policy import ACTIONS, CAP_MEMORY_REPO_WRITE, CAPABILITIES, ROLES, Agent, Grant, scope_matches

STATUSES = ("available", "approval", "denied", "not_implemented", "protected")
LABELS = {"available": "Available", "approval": "Approval Required", "denied": "Denied",
          "not_implemented": "Not Implemented", "protected": "Protected"}
RANK = {"not_implemented": 0, "protected": 0, "denied": 0, "approval": 1, "available": 2}
ROLE_ORDER = ("observer", "builder", "operator")
ROLE_LABELS = {"observer": "Read Only", "builder": "Builder", "operator": "Operator"}
CAP_LABELS = {CAP_MEMORY_REPO_WRITE: "Write task records into the linked repository folder"}
GROUPS = (
    ("inspect", "Inspection"), ("edit", "Editing"), ("create", "Operator creation"), ("delete", "Deleting"),
    ("code", "Python and code"), ("experiments", "Experiments"), ("undo", "Undo"), ("save", "Project saving"),
    ("memory", "Project memory"), ("coord", "Multi-agent coordination"), ("protected", "Always protected"),
    ("future", "Not implemented"),
)
MAX_ADD, MAX_REMOVE, MAX_GRANTS = 10, 50, 50


@dataclass(frozen=True)
class Cap:
    id: str
    group: str
    title: str
    tools: Tuple[str, ...] = ()
    needs: Tuple[str, ...] = ()          # actions needed on a scope (all of them)
    role_needs: Tuple[str, ...] = ()     # actions the role must have, beyond the scope (memory writes)
    approval: str = ""                   # "" | "always"
    kind: str = "permission"             # permission | identity | protected | not_implemented
    capability: str = ""                 # a dedicated, owner-issued permission it needs
    setting: str = ""                    # a project setting it needs ("code_mode")
    on_root: bool = False                # the scope must cover the project root itself
    note: str = ""                       # what people should know (shown as the explanation)


CATALOG: Tuple[Cap, ...] = (
    # ---- inspection -------------------------------------------------------------------------------------------
    Cap("identity", "inspect", "See its own identity, role and permissions",
        ("tdflow_whoami", "tdflow_capabilities"), kind="identity",
        note="Every enabled agent can ask who it is and what it may do."),
    Cap("inspect", "inspect", "Read the project, operators, parameters, DAT text and errors",
        ("tdflow_project_info", "tdflow_list_operators", "tdflow_get_operator", "tdflow_get_parameters",
         "tdflow_describe_network", "tdflow_get_errors", "tdflow_get_dat_text"), ("read",),
        note="Only inside the agent's scopes; anything else is filtered out of the answers."),
    Cap("capture", "inspect", "Look at TOP images", ("tdflow_capture_top",), ("read",),
        note="Up to 1920x1080, one image per second."),
    # ---- editing ----------------------------------------------------------------------------------------------
    Cap("set_values", "edit", "Set constant parameter values", ("tdflow_set_parameters",), ("update",),
        note="Parameters that run code or name files are never plain values (see below)."),
    Cap("wire", "edit", "Wire and unwire operators", ("tdflow_connect", "tdflow_disconnect"), ("update", "read")),
    Cap("dat_data", "edit", "Write text of data DATs", ("tdflow_set_dat_text",), ("update",),
        note="Table and Text DATs the user tagged 'tdflowdata'."),
    Cap("expressions", "edit", "Set expressions and parameters that name operators or files",
        ("tdflow_set_parameters",), ("update", "execute"), approval="always",
        note="Checks expression syntax and simple operator references before requesting approval."),
    Cap("exec_text", "edit", "Write Python Text DAT modules and Execute DAT callbacks", ("tdflow_set_dat_text",),
        ("update", "execute"), approval="always", note="Preview and check Python before you approve. Execute DATs must be inactive to edit source."),
    # ---- creation ---------------------------------------------------------------------------------------------
    Cap("create_plain", "create", "Create plain operators (TOPs, CHOPs, SOPs, MATs, COMPs, data DATs)",
        ("tdflow_create_operator",), ("create",),
        note="Only types on the creation allowlist: no code, network or file access."),
    Cap("create_exec", "create", "Create Execute DATs (created switched off)", ("tdflow_create_operator",),
        ("create", "execute"), approval="always", note="Code: each one waits for your approval."),
    Cap("activate_exec", "create", "Switch an Execute DAT on or change when it runs", ("tdflow_set_parameters",),
        ("update", "execute"), approval="always", note="Review the callback source and current active setting before approving."),
    # ---- deleting ---------------------------------------------------------------------------------------------
    Cap("delete_small", "delete", "Delete operators (up to 50 at a time)", ("tdflow_delete_operator",), ("delete",)),
    Cap("delete_large", "delete", "Delete more than 50 operators at once", ("tdflow_delete_operator",), ("delete",),
        approval="always", note="Large deletes wait for your approval."),
    # ---- code -------------------------------------------------------------------------------------------------
    Cap("code_mode", "code", "Run Code Mode scripts (many tdflow calls under one approval)",
        ("tdflow_execute_script",), ("execute",), approval="always", setting="code_mode",
        note="Off until you turn on Code Mode (Settings). You approve the exact script text every time."),
    # ---- experiments ------------------------------------------------------------------------------------------
    Cap("exp_create", "experiments", "Copy a component as an experiment", ("tdflow_experiment_create",),
        ("read", "create"), note="Code inside the copy needs your approval; copied Execute DATs are switched off."),
    Cap("exp_promote", "experiments", "Replace the original with an experiment", ("tdflow_experiment_promote",),
        ("delete", "update")),
    Cap("exp_discard", "experiments", "Discard an experiment", ("tdflow_experiment_discard",), ("delete",)),
    # ---- undo -------------------------------------------------------------------------------------------------
    Cap("undo", "undo", "Undo its own last change", ("tdflow_undo",), ("update",),
        note="Only its own newest change, and only while nothing else changed since."),
    # ---- saving -----------------------------------------------------------------------------------------------
    Cap("save", "save", "Save the project file (like Ctrl+S)", ("tdflow_save_project",), ("execute",),
        approval="always", on_root=True,
        note="Needs execute on the whole project root, and your approval every time."),
    # ---- project memory ---------------------------------------------------------------------------------------
    Cap("mem_read", "memory", "Read tasks and resume handoffs", ("tdflow_task_list", "tdflow_handoff_resume"),
        ("read",), note="When the project has project memory (Advanced page)."),
    Cap("mem_write", "memory", "Record task checkpoints and handoffs",
        ("tdflow_task_checkpoint", "tdflow_handoff_create"), ("read",), role_needs=("update",),
        note="When the project has project memory. Memory text is kept as untrusted notes."),
    Cap("mem_repo", "memory", "Record tasks into a linked repository folder",
        ("tdflow_task_checkpoint", "tdflow_handoff_create"), ("read",), role_needs=("update",),
        capability=CAP_MEMORY_REPO_WRITE, note="Only when project memory lives in a folder; a dedicated permission."),
    # ---- coordination -----------------------------------------------------------------------------------------
    Cap("lease", "coord", "Reserve a scope so other agents' changes there are refused", ("tdflow_lease_scope",),
        ("update",), note="At most 30 minutes at a time; you can end any lease on the dashboard."),
    Cap("sessions", "coord", "Keep session notes and list its sessions",
        ("tdflow_session_note", "tdflow_session_list"), kind="identity"),
    Cap("approval_status", "coord", "Follow its own approval requests", ("tdflow_approval_status",), kind="identity"),
    # ---- protected (hard denies: no permission changes them) ---------------------------------------------------
    Cap("p_system", "protected", "Change /sys, /local or /ui", kind="protected", note="System areas (rule H1)."),
    Cap("p_outside", "protected", "Work outside the project root", kind="protected",
        note="Rule H2. The Project Root parameter (Advanced page) sets where agents may work."),
    Cap("p_component", "protected", "Change TDflowMCP components", kind="protected", note="Rule H3."),
    Cap("p_memory", "protected", "Change the project memory component directly", kind="protected",
        note="Rule H4: only through the memory tools, with revision checks."),
    Cap("p_ancestors", "protected", "Delete, rename or re-flag components that contain TDflowMCP", kind="protected",
        note="Rule H5."),
    Cap("p_root", "protected", "Delete the project root", kind="protected", note="Rule H6."),
    Cap("p_params", "protected", "Change extension, callback, file, cloning or shortcut parameters, or reserved tags",
        kind="protected", note="Parameter deny list (rules G3, H7)."),
    Cap("p_netops", "protected", "Create network, file-system or Script operators", kind="protected",
        note="Not on the creation allowlist and not approvable (rule G2)."),
    Cap("p_admin", "protected", "Change its own permissions or decide approvals", kind="protected",
        note="Only you can, on this dashboard or in TouchDesigner."),
    # ---- not implemented --------------------------------------------------------------------------------------
    Cap("n_python", "future", "Run arbitrary Python outside Code Mode", kind="not_implemented",
        note="There is no raw Python tool (ADR-0004). Code Mode scripts call tdflow tools only."),
    Cap("n_saveas", "future", "Save As, export or open another project file", kind="not_implemented"),
    Cap("n_remote", "future", "Accept connections from other computers", kind="not_implemented",
        note="TDflowMCP listens on this computer only (127.0.0.1)."),
)
BY_ID = {c.id: c for c in CATALOG}


class ChangeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- statuses -------------------------------------------------------------------------------------------------------
def _facts(f: Optional[Mapping]) -> dict:
    f = dict(f or {})
    f.setdefault("active", None)          # None: every catalogued tool counts as active (pure tests)
    f.setdefault("code_mode", False)
    f.setdefault("approvals", True)
    f.setdefault("root", "")
    return f


def _inactive(cap: Cap, f: dict) -> bool:
    return f["active"] is not None and any(t not in f["active"] for t in cap.tools)


def engine_status(cap: Cap, facts: Optional[Mapping] = None) -> dict:
    """What TDflowMCP itself supports, independent of any agent."""
    f = _facts(facts)
    if cap.kind in ("protected", "not_implemented"):
        return {"status": cap.kind, "why": cap.note}
    if _inactive(cap, f):
        return {"status": "not_implemented", "why": "The tool is not active in this build."}
    if cap.approval and not f["approvals"]:
        return {"status": "denied", "why": "No approval surface is available, so this is refused."}
    why = cap.note
    if cap.setting == "code_mode" and not f["code_mode"]:
        why = "Supported, but Code Mode is off (dashboard Settings)."
    return {"status": "approval" if cap.approval else "available", "why": why}


def role_status(cap: Cap, role: str, facts: Optional[Mapping] = None) -> dict:
    """What an agent with this role can be given (with suitable scopes and permissions)."""
    eng = engine_status(cap, facts)
    if eng["status"] not in ("available", "approval") or cap.kind == "identity":
        return eng if cap.kind != "identity" else {"status": "available", "why": cap.note}
    missing = sorted((set(cap.needs) | set(cap.role_needs)) - set(ROLES[role]))
    if missing:
        return {"status": "denied", "why": f"The {ROLE_LABELS[role]} role has no {', '.join(missing)}."}
    extra = " Only with the dedicated permission." if cap.capability else ""
    return {"status": eng["status"], "why": (cap.note + extra).strip()}


def _probe(scope: str) -> str:
    """A representative operator path inside a scope pattern."""
    base = scope[:-3] if scope.endswith("/**") else scope
    return (base.replace("*", "x").rstrip("/") or "") + "/zz_probe"


def _scopes(agent: Agent, needs: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Review 22 (security 1): like ``Policy._grant_decision``, each needed action is checked on its own against all
    of the agent's grants, so actions given by different grants on the same scope combine."""
    allows = [g for g in agent.grants if g.effect == "allow"]
    cands = {g.scope for g in allows if set(needs) & set(g.actions)}
    allow = sorted(s for s in cands if all(any(a in g.actions and scope_matches(g.scope, _probe(s)) for g in allows)
                                           for a in needs))
    deny = sorted({g.scope for g in agent.grants if g.effect == "deny" and set(needs) & set(g.actions)})
    return allow, deny


def agent_status(cap: Cap, agent: Agent, facts: Optional[Mapping] = None) -> dict:
    """The selected agent's effective access: status, where (scopes), except (deny scopes), why, and ``fix``: what
    a permitted change would alter (``role:<role>``, ``grant:<actions>``, ``capability:<name>``, ``setting:<name>``),
    empty when nothing can enable it."""
    f = _facts(facts)
    eng = engine_status(cap, f)
    out = {"status": eng["status"], "where": [], "except": [], "why": eng["why"], "fix": ""}
    if eng["status"] not in ("available", "approval"):
        return out
    if not agent.enabled:
        return dict(out, status="denied", why="The agent is disabled.", fix="enable")
    if cap.kind == "identity":
        return dict(out, status="available", why=cap.note)
    need_role = set(cap.needs) | set(cap.role_needs)
    missing = sorted(need_role - set(ROLES[agent.role]))
    if missing:
        target = next((r for r in ROLE_ORDER if need_role <= set(ROLES[r])), "")
        also = cap.setting == "code_mode" and not f["code_mode"]
        if target and also:                          # review 22 (usability 2): a role alone would not enable it
            hint = f" It needs the {ROLE_LABELS[target]} role and Code Mode on (Settings); each script still needs your approval."
        elif target:
            hint = f" The {ROLE_LABELS[target]} role can" + (" ask for it (each use needs your approval)."
                                                             if cap.approval else " do it.")
        else:
            hint = ""
        return dict(out, status="denied", fix=(f"role:{target}" + ("+setting:code_mode" if also else "")) if target else "",
                    why=f"Its role ({ROLE_LABELS[agent.role]}) has no {', '.join(missing)}." + hint)
    if cap.capability and cap.capability not in agent.capabilities:
        return dict(out, status="denied", fix=f"capability:{cap.capability}",
                    why="Needs the dedicated permission: " + CAP_LABELS.get(cap.capability, cap.capability) + ".")
    allow, deny = _scopes(agent, cap.needs)
    if cap.on_root:
        allow = [s for s in allow if f["root"] and scope_matches(s, f["root"])]
    if cap.needs and not allow:
        return dict(out, status="denied", fix="grant:" + ",".join(cap.needs),
                    why=f"No scope gives it {' + '.join(cap.needs)}. Add a scope with these actions.")
    if cap.setting == "code_mode" and not f["code_mode"]:
        return dict(out, status="denied", where=allow, fix="setting:code_mode",
                    why="Code Mode is off. Turn it on in Settings; each script still needs your approval.")
    why = cap.note + (" Except where a deny applies." if deny else "")
    return dict(out, status="approval" if cap.approval else "available", where=allow, why=why.strip(),
                **{"except": deny})


def catalog_view(facts: Optional[Mapping] = None) -> List[dict]:
    """Every capability with the engine's and each role's status (the matrix's fixed columns)."""
    out = []
    for c in CATALOG:
        out.append({"id": c.id, "group": c.group, "title": c.title, "tools": list(c.tools), "needs": list(c.needs),
                    "approval": bool(c.approval), "kind": c.kind, "capability": c.capability, "setting": c.setting,
                    "engine": engine_status(c, facts),
                    "roles": {r: role_status(c, r, facts) for r in ROLE_ORDER}})
    return out


def agent_view(agent: Agent, facts: Optional[Mapping] = None) -> Dict[str, dict]:
    return {c.id: agent_status(c, agent, facts) for c in CATALOG}


def for_agent(agent: Agent, facts: Optional[Mapping] = None, page: str = "") -> dict:
    """The answer of ``tdflow_capabilities``: the caller's own effective access, grouped by status, kept apart from
    what TDflowMCP supports in general."""
    view = agent_view(agent, facts)
    groups = dict(GROUPS)
    mine: Dict[str, List[dict]] = {s: [] for s in STATUSES}
    for c in CATALOG:
        v = view[c.id]
        item = {"capability": c.title, "group": groups[c.group]}
        if c.tools:
            item["tools"] = list(c.tools)
        if v["where"]:
            item["scopes"] = v["where"]
        if v["except"]:
            item["except"] = v["except"]
        if v["status"] != "available" or c.kind != "identity":
            item["why"] = v["why"]
        mine[v["status"]].append(item)
    return {
        "agent": {"agent_id": agent.id, "display_name": agent.display_name, "role": agent.role,
                  "role_label": ROLE_LABELS.get(agent.role, agent.role)},
        "about": ("These are YOUR effective permissions right now, as the server enforces them. TDflowMCP itself "
                  "supports more than you may be allowed (see denied). Only the user can change your permissions, "
                  "on the dashboard's Capabilities page or in TouchDesigner. Every call is still checked per path."),
        "your_capabilities": {s: mine[s] for s in ("available", "approval")},
        "approval_required_means": "you may ask; nothing runs until the user approves that exact call",
        "not_available_to_you": {s: mine[s] for s in ("denied", "not_implemented", "protected")},
        "dashboard": {"capabilities_page": page,
                      "how": ("Tell the user to pulse Open Dashboard in TouchDesigner (TDflowMCP page), approve the "
                              "Browser sign-in there, and open the Capabilities tab. It shows every capability, which "
                              "roles can have it, and lets the user change your role, scopes and permissions. Give the "
                              "address only for reference: never ask the user to approve a sign-in that TouchDesigner "
                              "did not open for them.") if page else
                             "Not available right now: the user can pulse Open Dashboard in TouchDesigner."},
        "legend": {s: LABELS[s] for s in STATUSES},
    }


# ---- permission changes ---------------------------------------------------------------------------------------------
def agent_from_rows(agent: Agent, rows: Sequence[Mapping]) -> Agent:
    """The agent as stored: its grant rows (with ids) are the authority for changes."""
    gs = tuple(Grant(scope=r["scope"], actions=frozenset(r["actions"]), effect=r["effect"]) for r in rows)
    return Agent(id=agent.id, display_name=agent.display_name, role=agent.role, enabled=agent.enabled, grants=gs,
                 capabilities=agent.capabilities)


def _in_root(scope: str, root: str) -> bool:
    return scope == root or scope == root + "/**" or scope.startswith(root + "/")


def plan_change(agent: Agent, rows: Sequence[Mapping], change: Mapping, root: str) -> dict:
    """Validate a requested change and compute the result. ``change``: ``role``, ``add_grants`` (scope, actions,
    effect), ``remove_grants`` (ids), ``capabilities`` ({name: bool}). Raises ``ChangeError``.

    A role change keeps the agent's scopes: allow grants that had the old role's full action set get the new role's
    full set (that is how Connect Agent and the token form create them); narrowed grants keep only actions the new
    role has (a grant left with none is removed). Deny grants never change. A dedicated permission the new role
    cannot hold is dropped (shown in the result)."""
    if not isinstance(change, Mapping):
        raise ChangeError("INVALID_CHANGE", "the change must be an object")
    unknown = sorted(set(change) - {"role", "add_grants", "remove_grants", "capabilities"})
    if unknown:
        raise ChangeError("UNSUPPORTED_CHANGE", "only the role, scopes (grants) and dedicated permissions can be "
                                                "changed; not: " + ", ".join(str(u)[:32] for u in unknown[:5]))
    rows = [dict(r) for r in rows]
    ids = {int(r["id"]) for r in rows}
    old_role, role = agent.role, change.get("role", agent.role)
    if role not in ROLES:
        raise ChangeError("ROLE_NOT_ALLOWED", "the role must be observer (Read Only), builder or operator")
    remove = change.get("remove_grants") or []
    if not isinstance(remove, list) or len(remove) > MAX_REMOVE or \
            any(not isinstance(g, int) or isinstance(g, bool) for g in remove):
        raise ChangeError("INVALID_CHANGE", "remove_grants must be a list of grant ids")
    if set(remove) - ids:
        raise ChangeError("GRANT_NOT_FOUND", "a grant to remove does not belong to this agent")
    adds_in = change.get("add_grants") or []
    if not isinstance(adds_in, list) or len(adds_in) > MAX_ADD:
        raise ChangeError("INVALID_CHANGE", f"add_grants must be a list of at most {MAX_ADD} grants")
    notes: List[str] = []
    rewrite, removed = [], sorted(set(remove))
    for r in rows:
        if r["id"] in removed or r["effect"] != "allow" or role == old_role:
            continue
        acts = set(r["actions"])
        new = set(ROLES[role]) if acts == set(ROLES[old_role]) else acts & set(ROLES[role])
        if not new:
            removed.append(r["id"])
            notes.append(f"{r['scope']}: no action left for the {ROLE_LABELS[role]} role, so this grant is removed")
        elif new != acts:
            rewrite.append((r["id"], sorted(new)))
    adds = []
    for a in adds_in:
        if not isinstance(a, Mapping):
            raise ChangeError("INVALID_CHANGE", "each added grant needs scope, actions and effect")
        scope = str(a.get("scope", "")).strip()[:256]
        effect = str(a.get("effect", "allow"))
        acts = a.get("actions")
        if not isinstance(acts, list) or not acts or any(str(x) not in ACTIONS for x in acts):
            raise ChangeError("INVALID_CHANGE", "actions must be some of: " + ", ".join(ACTIONS))
        acts = sorted({str(x) for x in acts})
        if effect not in ("allow", "deny"):
            raise ChangeError("INVALID_CHANGE", "effect must be allow or deny")
        if not _in_root(scope, root):
            raise ChangeError("SCOPE_OUTSIDE_PROJECT_ROOT", f"the scope must lie inside the project root {root}")
        if effect == "allow" and not set(acts) <= set(ROLES[role]):
            raise ChangeError("ACTION_NOT_IN_ROLE", f"the {ROLE_LABELS[role]} role cannot be given "
                                                    + ", ".join(sorted(set(acts) - set(ROLES[role])))
                                                    + "; change the role first")
        try:
            Grant(scope=scope, actions=frozenset(acts), effect=effect)
        except ValueError as e:
            raise ChangeError("INVALID_SCOPE", str(e)[:160]) from None
        adds.append({"scope": scope, "actions": acts, "effect": effect})
    caps = set(agent.capabilities)
    req = change.get("capabilities") or {}
    if not isinstance(req, Mapping):
        raise ChangeError("INVALID_CHANGE", "capabilities must map a permission name to true or false")
    for name, on in req.items():
        if name not in CAPABILITIES or not isinstance(on, bool):
            raise ChangeError("UNSUPPORTED_CHANGE", f"{str(name)[:40]} is not a permission that can be given")
        if on and "update" not in ROLES[role]:
            raise ChangeError("CAPABILITY_NOT_ALLOWED", f"the {ROLE_LABELS[role]} role cannot record project memory")
        (caps.add if on else caps.discard)(name)
    for name in sorted(caps):
        if name == CAP_MEMORY_REPO_WRITE and "update" not in ROLES[role]:
            caps.discard(name)
            notes.append(CAP_LABELS[name] + ": dropped, the " + ROLE_LABELS[role] + " role cannot record")
    kept = [r for r in rows if r["id"] not in removed]
    if len(kept) + len(adds) > MAX_GRANTS:
        raise ChangeError("TOO_MANY_GRANTS", f"an agent can have at most {MAX_GRANTS} grants")
    rw = dict(rewrite)
    after_rows = [dict(r, actions=rw.get(r["id"], r["actions"])) for r in kept] + \
                 [dict(a, id=None) for a in adds]
    proposed = Agent(id=agent.id, display_name=agent.display_name, role=role, enabled=agent.enabled,
                     grants=tuple(Grant(scope=r["scope"], actions=frozenset(r["actions"]), effect=r["effect"])
                                  for r in after_rows), capabilities=frozenset(caps))
    if not any(g.effect == "allow" for g in proposed.grants):
        notes.append("No scope left: the agent will only see its identity tools")
    plan = {"role": role, "rewrite": [[i, a] for i, a in rewrite], "remove": sorted(removed), "add": adds,
            "capabilities": sorted(caps)}
    # Review 22 (usability 5): Connect Agent names agents "<client> (<role label>)"; keep that label true.
    old_tag, new_tag = f" ({ROLE_LABELS[old_role]})", f" ({ROLE_LABELS[role]})"
    if role != old_role and agent.display_name.endswith(old_tag):
        plan["display_name"] = (agent.display_name[:-len(old_tag)] + new_tag)[:64]
        notes.append(f"Its name becomes {plan['display_name']}")
    return {"plan": plan, "agent": proposed, "rows": after_rows, "notes": notes}


def _fingerprint(agent: Agent, rows: Sequence[Mapping]) -> dict:
    return {"agent": agent.id, "role": agent.role, "enabled": agent.enabled, "caps": sorted(agent.capabilities),
            "grants": [[r["id"], r["scope"], sorted(r["actions"]), r["effect"]] for r in rows]}


def change_digest(agent: Agent, rows: Sequence[Mapping], plan: Mapping) -> str:
    """Binds a confirmation to exactly this change of exactly this stored state (a concurrent change invalidates it)."""
    blob = json.dumps({"before": _fingerprint(agent, rows), "plan": plan}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compare(before: Agent, after: Agent, facts: Optional[Mapping] = None) -> dict:
    """Effective differences, and whether the change gives the agent more access (an escalation)."""
    b, a = agent_view(before, facts), agent_view(after, facts)
    diff, up = [], False
    for c in CATALOG:
        x, y = b[c.id], a[c.id]
        more = (RANK[y["status"]] > RANK[x["status"]] or
                (RANK[y["status"]] > 0 and bool(set(y["where"]) - set(x["where"]))) or
                (RANK[y["status"]] > 0 and bool(set(x["except"]) - set(y["except"]))))
        shown = x["status"] != y["status"] or (RANK[y["status"]] > 0 and (x["where"], x["except"]) !=
                                                (y["where"], y["except"]))
        if shown:
            diff.append({"id": c.id, "title": c.title, "from": x["status"], "to": y["status"],
                         "from_where": x["where"], "to_where": y["where"], "escalation": bool(more)})
        up = up or more
    role_up = ROLE_ORDER.index(after.role) > ROLE_ORDER.index(before.role)
    cap_up = bool(set(after.capabilities) - set(before.capabilities))
    reasons = []
    if role_up:
        reasons.append(f"role {ROLE_LABELS[before.role]} -> {ROLE_LABELS[after.role]}")
    if cap_up:
        reasons.append("adds " + ", ".join(CAP_LABELS.get(c, c) for c in sorted(set(after.capabilities) -
                                                                                   set(before.capabilities))))
    if up and not reasons:
        reasons.append("more capabilities or wider scopes")
    return {"diff": diff, "escalation": bool(up or role_up or cap_up), "reasons": reasons}


def raw_escalation(before: Agent, rows: Sequence[Mapping], plan: Mapping) -> List[str]:
    """Review 22 (security 1, 2): more access judged from the change itself, independent of the current state (a
    disabled agent, Code Mode off or an inactive tool would hide it in the effective matrix)."""
    out = []
    if ROLE_ORDER.index(plan["role"]) > ROLE_ORDER.index(before.role):
        out.append(f"role {ROLE_LABELS[before.role]} -> {ROLE_LABELS[plan['role']]}")
    if set(plan["capabilities"]) - set(before.capabilities):
        out.append("adds " + ", ".join(CAP_LABELS.get(c, c) for c in sorted(set(plan["capabilities"]) -
                                                                           set(before.capabilities))))
    old = {r["id"]: r for r in rows}
    if any(g["effect"] == "allow" for g in plan["add"]):
        out.append("adds an allow scope")
    if any(set(a) - set(old[i]["actions"]) for i, a in plan["rewrite"] if i in old):
        out.append("adds actions to a scope")
    if any(old[i]["effect"] == "deny" for i in plan["remove"] if i in old):
        out.append("removes a deny")
    return out


LENIENT = {"active": None, "code_mode": True, "approvals": True}


def preview(agent: Agent, rows: Sequence[Mapping], change: Mapping, root: str,
            facts: Optional[Mapping] = None) -> dict:
    before = agent_from_rows(agent, rows)
    p = plan_change(before, rows, change, root)
    cmp_ = compare(before, p["agent"], facts)
    # The same comparison as if the agent were enabled, Code Mode on and every tool active: what it could do later.
    lenient = dict(LENIENT, root=(facts or {}).get("root", root))
    en = lambda a: Agent(a.id, a.display_name, a.role, True, a.grants, a.capabilities)   # noqa: E731
    latent = compare(en(before), en(p["agent"]), lenient)
    raw = raw_escalation(before, rows, p["plan"])
    reasons = list(dict.fromkeys(cmp_["reasons"] + raw + (["more access once enabled or with Code Mode on"]
                                                        if latent["escalation"] and not (cmp_["escalation"] or raw)
                                                        else [])))
    esc = bool(cmp_["escalation"] or raw or latent["escalation"])
    return {"agent_id": agent.id, "plan": p["plan"], "notes": p["notes"], "diff": cmp_["diff"],
            "escalation": esc, "escalation_reasons": reasons,
            "result": {"role": p["agent"].role, "capabilities": sorted(p["agent"].capabilities),
                       "grants": [{"scope": r["scope"], "actions": list(r["actions"]), "effect": r["effect"]}
                                  for r in p["rows"]],
                       "matrix": agent_view(p["agent"], facts)},
            "digest": change_digest(before, rows, p["plan"]), "changes_anything": bool(
                cmp_["diff"] or p["plan"]["remove"] or p["plan"]["add"] or p["plan"]["rewrite"]
                or p["agent"].role != agent.role or set(p["agent"].capabilities) != set(agent.capabilities))}


def dashboard_view(agents: Sequence[Tuple[Agent, Sequence[Mapping], bool]], facts: Optional[Mapping], root: str,
                   state: Mapping, page: str) -> dict:
    """The Capabilities page's data: ``agents`` = (agent, stored grant rows, connected). Every agent row is built from
    its stored grants, the same authority a change is checked against."""
    out = []
    for a, rows, connected in agents:
        out.append({"id": a.id, "display_name": a.display_name, "role": a.role, "enabled": a.enabled,
                    "capabilities": sorted(a.capabilities), "grants": [dict(r) for r in rows],
                    "connected": bool(connected), "matrix": agent_view(agent_from_rows(a, rows), facts)})
    return {"groups": [{"id": g, "title": t} for g, t in GROUPS], "legend": dict(LABELS),
            "catalog": catalog_view(facts), "agents": out, "project_root": root,
            "roles": [{"id": r, "label": ROLE_LABELS[r], "actions": sorted(ROLES[r])} for r in ROLE_ORDER],
            "dedicated": [{"name": n, "label": CAP_LABELS.get(n, n)} for n in sorted(CAPABILITIES)],
            "actions": list(ACTIONS), "state": dict(state), "page": page}
