"""Server-side CRUD+execute policy engine (security-model §3, ADR-0009). Pure Python, no `td`.

Inputs are *resolved* targets: the TD adapter turns tool arguments into concrete, absolute operator
paths plus facts (type, protection, what would change). The engine never sees TD objects.

Evaluation order (§3.2):
1. Hard denies (§3.4), never overridable.
2. Global rules: any ``execute`` and large deletes need approval. Phase 1 has no approval surface,
   so ``require_approval`` resolves to deny with ``APPROVAL_UNAVAILABLE`` (§4).
3. Grants of the agent (role ∩ grant actions on matching scopes): explicit deny wins, no allow = deny.
4. Mutations are all-or-nothing; recursive reads use ``filter_readable``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ACTIONS = ("read", "create", "update", "delete", "execute")
MUTATING = frozenset({"create", "update", "delete", "execute"})
ROLES: Dict[str, frozenset] = {
    "observer": frozenset({"read"}),
    "builder": frozenset({"read", "create", "update", "delete"}),
    "operator": frozenset({"read", "create", "update", "delete", "execute"}),
}
# Operator types whose creation (or DAT text) makes TouchDesigner run code or open network connections
# (§3.3). Creating them needs `create` + `execute`, and they are default-denied by allowed_op_types.
EXECUTABLE_TYPES = frozenset({
    "executeDAT", "chopexecuteDAT", "datexecuteDAT", "opexecuteDAT", "parameterexecuteDAT",
    "panelexecuteDAT", "scriptDAT", "scriptCHOP", "scriptSOP", "scriptTOP", "webclientDAT",
    "webserverDAT", "tcpipDAT", "udpinDAT", "udpoutDAT", "websocketDAT", "oscinDAT", "oscoutDAT",
    "serialDAT", "mqttclientDAT", "socketioDAT", "folderDAT", "engineCOMP",
})
# Phase 1B creation ALLOWLIST (review 6, H1/H3): only plain operators that neither run code, open network
# connections, read or write files, nor introspect other operators. Everything else is G2-denied.
SAFE_CREATE_TYPES = frozenset({
    # TOPs
    "noiseTOP", "levelTOP", "nullTOP", "constantTOP", "rampTOP", "rectangleTOP", "circleTOP", "compositeTOP",
    "overTOP", "underTOP", "addTOP", "multiplyTOP", "blurTOP", "transformTOP", "hsvadjustTOP", "crossTOP",
    "switchTOP", "flipTOP", "cropTOP", "resolutionTOP", "edgeTOP", "lumablurTOP", "thresholdTOP", "monochromeTOP",
    # CHOPs
    "constantCHOP", "noiseCHOP", "lfoCHOP", "mathCHOP", "nullCHOP", "filterCHOP", "lagCHOP", "mergeCHOP",
    "renameCHOP", "limitCHOP", "speedCHOP", "patternCHOP", "waveCHOP", "switchCHOP", "triggerCHOP",
    # SOPs
    "boxSOP", "sphereSOP", "gridSOP", "circleSOP", "tubeSOP", "torusSOP", "lineSOP", "noiseSOP", "transformSOP",
    "nullSOP", "mergeSOP", "facetSOP", "sortSOP",
    # MATs and COMPs (COMP extension/clone/shortcut parameters are denylisted below)
    "constantMAT", "phongMAT", "pbrMAT", "wireframeMAT", "baseCOMP", "containerCOMP", "geometryCOMP",
    "cameraCOMP", "lightCOMP",
    # DATs: data only (writing text additionally needs the user's 'tdflowdata' tag)
    "tableDAT", "textDAT", "nullDAT",
})
# Owner D20: executable types that may be created **with the user's approval** (operator role; rule A1). They are
# created inactive ("staged"); turning them on is itself an approval-gated change. Network, file-system and
# Script operators stay outside (not approvable).
APPROVABLE_CREATE_TYPES = frozenset({"executeDAT", "chopexecuteDAT", "datexecuteDAT", "opexecuteDAT",
                                     "parameterexecuteDAT", "panelexecuteDAT"})
# Parameters that switch an executable operator on: changing them is classified as execute (D20).
ACTIVATION_PARAMS = ("active",)
# Parameters whose value TouchDesigner evaluates as Python, that load/execute code, that change identity or
# cloning (which can replace children, also of ancestors), or that expose shortcuts (default denylist).
PARAM_DENYLIST = ("extension*", "promoteextension*", "extname*", "reinitextensions", "callbacks", "callbacksdat",
                  "externaltox", "enableexternaltox*", "syncfile", "loadonstart*", "write", "writepulse",
                  "script*", "initextonstart", "pythonexpr*", "dat", "clone*", "enablecloning*", "cloneimmune",
                  "opshortcut", "parentshortcut", "iop*", "parentshortcut", "relpath", "subcompname",
                  "savebackup", "reloadcustom", "reloadbuiltin", "file", "folder", "plugin*") + tuple(
    f"ext{i}*" for i in range(10))                 # TD's internal names behind extension1.. (ext0object, ext0name…)
PROTECTED_ROOTS = ("/sys", "/local", "/ui")
ANCESTOR_FORBIDDEN_CHANGES = frozenset({"delete", "rename", "flags", "extension"})
MAX_DELETE_WITHOUT_APPROVAL = 50
MAX_OPS_PER_CALL = 50
# Capabilities: dedicated, owner-issued permissions that are not operator scopes (ADR-0013, owner D18). An
# agent has none unless the user ticks them when creating its token. Enforced server-side by the engine.
CAP_MEMORY_REPO_WRITE = "memory:repo:write"   # write task records into the repository-backed memory folder
CAPABILITIES = frozenset({CAP_MEMORY_REPO_WRITE})


def _glob_regex(scope: str) -> "re.Pattern":
    if not scope.startswith("/"):
        raise ValueError(f"scope must be absolute: {scope!r}")
    segs = scope.strip("/").split("/") if scope != "/" else []
    out = ""
    for i, s in enumerate(segs):
        if s == "**":
            out += "(?:/.*)?" if i == len(segs) - 1 else "(?:/[^/]+)*"
        else:
            if "**" in s or s == "":
                raise ValueError("'**' must be a whole segment and segments may not be empty")
            out += "/" + "".join("[^/]*" if ch == "*" else re.escape(ch) for ch in s)
    return re.compile("^" + (out or "/") + "$")


def scope_matches(scope: str, path: str) -> bool:
    return bool(_glob_regex(scope).match(path))


def _name_denied(name: str, patterns: Iterable[str]) -> bool:
    for p in patterns:
        if p.endswith("*") and name.startswith(p[:-1]) or name == p:
            return True
    return False


@dataclass(frozen=True)
class Grant:
    scope: str
    actions: frozenset
    effect: str = "allow"                     # allow | deny
    allowed_op_types: Optional[frozenset] = None   # None = any non-executable type
    param_denylist: Tuple[str, ...] = ()

    def __post_init__(self):
        _glob_regex(self.scope)
        if self.effect not in ("allow", "deny"):
            raise ValueError("effect")
        if not set(self.actions) <= set(ACTIONS):
            raise ValueError("actions")


@dataclass(frozen=True)
class Agent:
    id: str
    display_name: str
    role: str
    enabled: bool = True
    grants: Tuple[Grant, ...] = ()
    capabilities: frozenset = frozenset()

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")
        if not set(self.capabilities) <= CAPABILITIES:
            raise ValueError("unknown capability")


@dataclass(frozen=True)
class Target:
    action: str
    path: str                        # resolved absolute path (never relative)
    exists: bool = True
    op_type: str = ""
    protected: str = ""              # "" | "instance" | "tdflowproject"  (target is it or inside it)
    ancestor_of: str = ""            # "" | "instance" | "tdflowproject"  (target is an ancestor of it)
    change: str = ""                 # what an update/delete does: param | expression | flags | rename | delete | wire | text | extension
    param: str = ""
    count: int = 1                   # operators affected (delete includes descendants)


@dataclass(frozen=True)
class Verdict:
    allow: bool
    code: str = "OK"                 # OK | DENIED | APPROVAL_UNAVAILABLE | APPROVAL_REQUIRED | INVALID_TARGET
    rule_id: str = ""
    reason: str = ""
    target: Optional[str] = None
    policy_version: str = ""


class Policy:
    def __init__(self, root: str = "/project1", max_delete: int = MAX_DELETE_WITHOUT_APPROVAL,
                 max_ops: int = MAX_OPS_PER_CALL):
        if not root.startswith("/") or root in ("/",) or "*" in root or ".." in root:
            raise ValueError("project root must be an absolute operator path")
        self.root = root.rstrip("/")
        self.max_delete, self.max_ops = max_delete, max_ops
        # Phase 2B: with an approval surface (the dashboard) the two approval rules answer APPROVAL_REQUIRED;
        # without one they stay a plain denial (APPROVAL_UNAVAILABLE), exactly as in Phases 1-2A.
        self.approvals = False

    def version(self, agent: Optional[Agent] = None) -> str:
        blob = {"root": self.root, "max_delete": self.max_delete, "max_ops": self.max_ops,
                "exec": sorted(EXECUTABLE_TYPES), "deny": PARAM_DENYLIST}
        if agent is not None:
            blob["agent"] = [agent.id, agent.role, agent.enabled,
                             [(g.scope, sorted(g.actions), g.effect, sorted(g.allowed_op_types or []),
                               list(g.param_denylist)) for g in agent.grants]]
            if agent.capabilities:                 # absent when empty, so existing policy versions are unchanged
                blob["capabilities"] = sorted(agent.capabilities)
        return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()[:16]

    # ---- hard denies (§3.4) --------------------------------------------------------------------
    def hard_deny(self, t: Target) -> Optional[Tuple[str, str]]:
        p = t.path
        if (not p.startswith("/") or "//" in p or (p != "/" and p.endswith("/"))
                or any(s in (".", "..", "") for s in p.strip("/").split("/"))):
            return "H0", "target path is not a resolved absolute path"
        if any(p == r or p.startswith(r + "/") for r in PROTECTED_ROOTS):
            return "H1", "system areas (/sys, /local, /ui) are protected"
        if not (p == self.root or p.startswith(self.root + "/")):
            return "H2", "outside the configured project root"
        if t.protected == "instance":
            return "H3", "TDflowMCP components are protected"
        if t.protected == "tdflowproject":
            return "H4", "tdflowProject changes only through memory tools"
        if t.ancestor_of and (t.change in ANCESTOR_FORBIDDEN_CHANGES or t.action == "delete"):
            return "H5", f"ancestor of a protected component: {t.change or t.action} is not allowed"
        if p == self.root and t.action == "delete":
            return "H6", "the project root cannot be deleted"
        if t.change == "tags":
            return "H7", "reserved tags cannot be changed by agents"
        return None

    # ---- evaluation ----------------------------------------------------------------------------
    def _grant_decision(self, agent: Agent, t: Target) -> Tuple[bool, str, str]:
        role_actions = ROLES[agent.role]
        if t.action not in role_actions:
            return False, "R1", f"role {agent.role} has no {t.action} permission"
        matching = [g for g in agent.grants if t.action in g.actions and scope_matches(g.scope, t.path)]
        if any(g.effect == "deny" for g in matching):
            return False, "G1", "an explicit deny grant matches"
        allows = [g for g in matching if g.effect == "allow"]
        if not allows:
            return False, "G0", f"no grant allows {t.action} on this path"
        if t.action == "create":
            approvable = t.op_type in APPROVABLE_CREATE_TYPES and "execute" in role_actions
            if t.op_type not in SAFE_CREATE_TYPES and not approvable:   # allowlist; network/file/script never qualify
                return False, "G2", f"operator type {t.op_type or '?'} is not on the Phase 1 allowlist"
            if not any(g.allowed_op_types is None or t.op_type in g.allowed_op_types for g in allows):
                return False, "G2", f"operator type {t.op_type} is not allowed by your grants"
        if t.action == "update" and t.param:
            if _name_denied(t.param, PARAM_DENYLIST) or all(_name_denied(t.param, g.param_denylist) for g in allows):
                return False, "G3", f"parameter {t.param} is on the deny list"
        return True, "G", ""

    def evaluate(self, agent: Optional[Agent], targets: Sequence[Target], approved: bool = False) -> Verdict:
        """``approved``: the user approved exactly this call (the engine verified the approval). Only the two
        approval rules (A1, A2) are then skipped; hard denies, role, grants, allowlists and limits all still apply."""
        ver = self.version(agent)
        if agent is None or not agent.enabled:
            return Verdict(False, "DENIED", "A0", "agent unknown or disabled", None, ver)
        if not targets:
            return Verdict(False, "INVALID_TARGET", "T0", "no targets", None, ver)
        mutations = [t for t in targets if t.action in MUTATING]
        if len(mutations) > self.max_ops:
            return Verdict(False, "DENIED", "L1", f"more than {self.max_ops} operations in one call", None, ver)
        for t in targets:
            if t.action not in ACTIONS:
                return Verdict(False, "INVALID_TARGET", "T1", "unknown action", t.path, ver)
            hd = self.hard_deny(t)
            if hd:
                return Verdict(False, "DENIED", hd[0], hd[1], t.path, ver)
        for t in targets:                                     # deny reasons beat approval reasons
            ok, rule, why = self._grant_decision(agent, t)
            if not ok:
                return Verdict(False, "DENIED", rule, why, t.path, ver)
        if approved:
            return Verdict(True, "OK", "GA", "", None, ver)      # allowed by grants plus the user's approval
        code = "APPROVAL_REQUIRED" if self.approvals else "APPROVAL_UNAVAILABLE"
        for t in targets:
            if t.action == "execute":
                why = ("this makes TouchDesigner run code, so the user must approve it" if self.approvals else
                       "execute needs approval, which is not available in Phase 1")
                return Verdict(False, code, "A1", why, t.path, ver)
            if t.action == "delete" and t.count > self.max_delete:
                return Verdict(False, code, "A2",
                               f"deleting more than {self.max_delete} operators needs approval", t.path, ver)
        return Verdict(True, "OK", "G", "", None, ver)

    def approval_reasons(self, targets: Sequence[Target]) -> List[str]:
        """Why this call needs approval, for the user (every matching rule, not only the first)."""
        out = []
        for t in targets:
            if t.action == "execute":
                what = {"expression": f"sets an expression on parameter {t.param}", "reference": f"sets parameter "
                        f"{t.param}, which names another operator or a file", "text": "writes text TouchDesigner may run "
                        "as code", "wire": "wires data into an operator that may evaluate it",
                        "activation": f"switches {t.param} of an executable operator (its code starts or stops "
                        "running)", "trigger": f"changes {t.param} of an Execute DAT, which decides when its code "
                        "runs"}.get(t.change, f"creates a {t.op_type}, an operator that runs code (created "
                        "switched off)" if t.op_type else "runs code")
                out.append(f"A1: {t.path} {what}. Approved code runs with full access to TouchDesigner (including "
                           "TDflowMCP itself); scopes do not limit it")
            if t.action == "delete" and t.count > self.max_delete:
                out.append(f"A2: deletes {t.count} operators under {t.path} (more than {self.max_delete})")
        return out[:10]

    def filter_readable(self, agent: Optional[Agent], targets: Sequence[Target]) -> Tuple[List[Target], int]:
        """Recursive reads: drop hard-denied and unpermitted entries instead of failing (§3.2.5)."""
        if agent is None or not agent.enabled:
            return [], len(targets)
        keep = [t for t in targets if not self.hard_deny(t) and self._grant_decision(agent, t)[0]]
        return keep, len(targets) - len(keep)

    def visible_tools(self, agent: Optional[Agent]) -> bool:
        return bool(agent and agent.enabled and agent.grants)


def grants_for(role: str, scopes: Sequence[str]) -> Tuple[Grant, ...]:
    """Scope preset: the role's actions on each scope (used by Setup token creation)."""
    acts = ROLES[role]
    return tuple(Grant(scope=s, actions=frozenset(acts)) for s in scopes)
