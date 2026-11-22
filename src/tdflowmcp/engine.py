"""Tool-call orchestration (system-design §3): validate → resolve → policy → audit intent → execute →
audit outcome. Pure Python, no `td`; runs on TouchDesigner's main thread via the transport pump.

Guarantees enforced here (unit-tested with a fake adapter):
- Every call is authorised against the agent's grants with hard denies first; denials are audited.
- Nothing executes unless the ``intent`` event was durably written (fail closed: AUDIT_UNAVAILABLE),
  and every executed call gets an ``outcome`` event (if that write fails the result says so).
- The kill switch and token revocation are re-checked at dispatch time.
- Only tools whose security tests passed are *active* (``active_tools``); others are not listed or callable.
- Undo only targets the caller's own newest audited change, re-checks policy for the inverse
  actions, and reports whether the restoration was verified.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional, Sequence

from tdflowmcp import TOOL_SURFACE_VERSION, __version__, schema
from tdflowmcp.identity import Identity, Sessions
from tdflowmcp.policy import Agent, Policy, Target
from tdflowmcp.protocol import tool_error, tool_result
from tdflowmcp.store import AuditUnavailable, Store, redact
from tdflowmcp.tools import Registry, Tool

# Undo re-check (security model §3.3, revised): undoing a create removes it (needs delete); undoing a delete
# restores the user's original operator, so it needs the same delete grant that allowed the change.
INVERSE = {"create": "delete", "delete": "delete", "update": "update", "read": "read", "execute": "execute"}


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


class Resolution:
    def __init__(self, targets: Sequence[Target], context: Optional[dict] = None):
        self.targets, self.context = list(targets), dict(context or {})


class Adapter:
    """Implemented by the TD adapter (src/td_adapter). Every method runs on the main thread."""

    def resolve(self, tool: Tool, args: dict) -> Resolution:  # pragma: no cover - interface
        raise NotImplementedError

    def execute(self, tool: Tool, args: dict, res: Resolution, undo_label: str,
                readable: Callable[[Sequence[Target]], List[Target]]) -> dict:  # pragma: no cover
        raise NotImplementedError

    def undo(self, record: dict) -> dict:  # pragma: no cover - interface
        """Return {"undone": bool, "verified": bool, "code": str?}. Raise ToolFailure(UNDO_CONFLICT) if
        the change is no longer the newest one."""
        raise NotImplementedError

    def project_summary(self) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class Engine:
    def __init__(self, store: Store, identity: Identity, policy: Policy, registry: Registry, adapter: Adapter,
                 active_tools: Sequence[str], suspended: Callable[[], bool], clock=time.perf_counter,
                 on_audit_failure: Optional[Callable[[], None]] = None):
        self.store, self.identity, self.policy, self.registry, self.adapter = store, identity, policy, registry, adapter
        self.active = frozenset(active_tools)
        self.suspended = suspended
        self.sessions = Sessions()
        self.clock = clock
        self.on_audit_failure = on_audit_failure or (lambda: None)

    # ---- MCP host for one caller ---------------------------------------------------------------
    def list_tools(self, token_id: str) -> List[dict]:
        agent = self.identity.agent_for_token(token_id)
        if agent is None or self.suspended():
            return []
        return [t.describe() for t in self.registry.all() if t.name in self.active
                and (t.resolver in ("none",) or agent.grants)]

    def call(self, token_id: str, name: str, args: dict, client_info: Optional[dict] = None,
             request_id: str = "") -> Optional[dict]:
        tool = self.registry.get(name)
        if tool is None or tool.name not in self.active:
            return None                                              # protocol error: unknown tool
        t0 = self.clock()
        ci = redact(client_info or {}, 200)
        agent = self.identity.agent_for_token(token_id)
        base = {"token_id": token_id, "tool": name, "method": "tools/call", "client_info": ci,
                "request_id": request_id, "args_hmac": self.store.args_hmac(args) if self.store.available else None,
                "args_redacted": redact(args)}
        if agent is None:
            return self._deny(base, "DENIED", "A0", "token revoked or agent disabled", None)
        base["agent_id"] = agent.id
        base["session_id"] = self.sessions.session_for(token_id, ci)
        if self.suspended():
            return self._deny(base, "SUSPENDED", "K1", "all agents are suspended", None)
        errs = schema.errors(args, tool.input_schema)
        if errs:
            return self._deny(base, "INVALID_ARGS", "V1", "; ".join(errs[:5]), None)
        if tool.resolver == "none":
            return self._run_identity_tool(tool, agent, args, base, t0)
        try:
            res = self.adapter.resolve(tool, args)
        except ToolFailure as f:
            # Never reveal what exists (or how it is shaped) outside the caller's scope: if the requested
            # paths are not permitted anyway, report the policy denial instead of the resolution error.
            pv = self.policy.evaluate(agent, _requested_targets(tool, args, getattr(self.adapter, "path_facts", None)))
            if not pv.allow and pv.code == "DENIED":
                base.update(policy_version=pv.policy_version)
                return self._deny(base, pv.code, pv.rule_id, pv.reason, pv.target)
            return self._deny(base, f.code, "R0", f.message, None)
        if tool.resolver == "undo":
            return self._undo(tool, agent, base, t0)
        verdict = self.policy.evaluate(agent, res.targets)
        base.update(targets=[[t.action, t.path] for t in res.targets][:50], policy_version=verdict.policy_version)
        if not verdict.allow:
            return self._deny(base, verdict.code, verdict.rule_id, verdict.reason, verdict.target)
        mutating = not tool.read_only
        label = None
        try:
            seq = self.store.append("intent", decision="ALLOW", rule_id=verdict.rule_id, **base)
        except AuditUnavailable:
            return tool_error("AUDIT_UNAVAILABLE", "The audit store is unavailable, so nothing was executed.")
        if mutating:
            label = f"tdflow:{seq}"
        try:
            out = self.adapter.execute(tool, args, res, label or "",
                                       lambda ts: self.policy.filter_readable(agent, ts)[0])
            outcome, code, detail = "ok", None, out.pop("_audit", None)
        except ToolFailure as f:
            out, outcome, code, detail = None, "error", f.code, f.extra.get("_audit")
            failure = f
        except Exception as e:                                       # never leak internals to the agent
            out, outcome, code, detail = None, "error", "INTERNAL_ERROR", {"exc": type(e).__name__}
            failure = ToolFailure("INTERNAL_ERROR", "The operation failed inside TouchDesigner.")
        dur = int((self.clock() - t0) * 1000)
        reversible = (detail or {}).get("reversible", tool.reversible) if outcome == "ok" else "no"
        try:
            oseq = self.store.append("outcome", decision="ALLOW", outcome=outcome, error_code=code,
                                     duration_ms=dur, undo_label=label if outcome == "ok" and reversible != "no" else None,
                                     detail=dict(detail or {}, intent_seq=seq, reversible=reversible), **base)
        except AuditUnavailable:
            oseq = None
            if mutating:
                self.on_audit_failure()            # review 6: suspend agents if an outcome cannot be recorded
        if outcome != "ok":
            extra = dict(failure.extra)
            extra.pop("_audit", None)
            return tool_error(failure.code, failure.message, audit_seq=oseq, **extra)
        if mutating:
            out.update(audit_seq=oseq, undo_label=label if reversible != "no" else None, reversible=reversible != "no")
            if oseq is None:
                out["audit_warning"] = "outcome not recorded: audit store became unavailable"
        return tool_result(out)

    # ---- helpers -------------------------------------------------------------------------------
    def _deny(self, base: dict, code: str, rule: str, reason: str, target: Optional[str]) -> dict:
        try:
            seq = self.store.append("deny", decision=code, rule_id=rule, error_code=code,
                                    detail={"reason": reason[:300], "target": target}, **base)
        except AuditUnavailable:
            seq = None
        return tool_error(code, reason, rule_id=rule, target=target, audit_seq=seq)

    def _run_identity_tool(self, tool: Tool, agent: Agent, args: dict, base: dict, t0: float) -> dict:
        if tool.name == "tdflow_whoami":
            out = {"agent_id": agent.id, "display_name": agent.display_name, "role": agent.role,
                   "scopes": [{"scope": g.scope, "actions": sorted(g.actions), "effect": g.effect} for g in agent.grants],
                   "project_root": self.policy.root, "server_version": __version__,
                   "tool_surface_version": TOOL_SURFACE_VERSION, "expected_skill_version": None,
                   "limits": {"max_ops_per_call": self.policy.max_ops, "max_delete_without_approval": self.policy.max_delete},
                   "approvals": "unavailable in Phase 1 (execute and large deletes are denied)"}
        elif tool.name == "tdflow_session_note":
            out = {"noted": True, "session_id": base["session_id"]}
        elif tool.name == "tdflow_session_list":
            out = None
        else:                                                         # pragma: no cover - registry guards this
            raise ToolFailure("INTERNAL_ERROR", "unknown identity tool")
        try:                                                          # intent + outcome, like every call (§6)
            iseq = self.store.append("intent", decision="ALLOW", rule_id="I", **base)
            if out is None:
                out = {"sessions": self.store.sessions(agent.id, int(args.get("limit", 10)))}
            seq = self.store.append("outcome", decision="ALLOW", outcome="ok", duration_ms=int((self.clock() - t0) * 1000),
                                    detail=dict({"intent_seq": iseq}, **({"note": redact(args.get("text", ""), 2000)}
                                                                         if tool.name == "tdflow_session_note" else {})),
                                    **base)
        except AuditUnavailable:
            return tool_error("AUDIT_UNAVAILABLE", "The audit store is unavailable.")
        out["audit_seq"] = seq
        return tool_result(out)

    def last_undoable(self, agent_id: str) -> Optional[dict]:
        undone = set()
        for ev in self.store.events("agent_id=? AND kind='outcome' AND outcome='ok' AND tool='tdflow_undo'", (agent_id,), 500):
            d = _json(ev.get("detail"))
            if d.get("undoes"):
                undone.add(d["undoes"])
        for ev in self.store.events("agent_id=? AND kind='outcome' AND outcome='ok' AND undo_label IS NOT NULL",
                                    (agent_id,), 500):
            if ev["seq"] not in undone and ev["tool"] != "tdflow_undo":
                return ev
        return None

    def _undo(self, tool: Tool, agent: Agent, base: dict, t0: float) -> dict:
        try:
            rec = self.last_undoable(agent.id)
        except AuditUnavailable:
            return tool_error("AUDIT_UNAVAILABLE", "The audit store is unavailable, so nothing was undone.")
        if rec is None:
            return self._deny(base, "NOTHING_TO_UNDO", "U0", "you have no audited change to undo", None)
        detail = _json(rec.get("detail"))
        if detail.get("reversible", "no") == "no":
            return self._deny(base, "NOT_REVERSIBLE", "U1", "that change is not reversible", None)
        targets = [Target(action=INVERSE[a], path=p) for a, p in _json(rec.get("targets"), [])]
        targets = [t for t in targets if t.action != "read"]
        verdict = self.policy.evaluate(agent, targets or [Target("update", self.policy.root + "/__none__")])
        base.update(targets=[[t.action, t.path] for t in targets], policy_version=verdict.policy_version)
        if not verdict.allow:
            return self._deny(base, verdict.code, verdict.rule_id, verdict.reason, verdict.target)
        try:
            seq = self.store.append("intent", decision="ALLOW", rule_id="U", detail={"undoes": rec["seq"]}, **base)
        except AuditUnavailable:
            return tool_error("AUDIT_UNAVAILABLE", "The audit store is unavailable, so nothing was undone.")
        try:
            r = self.adapter.undo(dict(rec, detail=detail))
            if r.get("undone") and r.get("verified"):
                outcome, code = "ok", None
            else:                                   # never call an unverified restoration a success (owner rule)
                outcome, code = "error", r.get("code") or ("UNDO_UNVERIFIED" if r.get("undone") else "UNDO_FAILED")
        except ToolFailure as f:
            r, outcome, code = {"undone": False, "verified": False}, "error", f.code
        dur = int((self.clock() - t0) * 1000)
        try:
            oseq = self.store.append("outcome", decision="ALLOW", outcome=outcome, error_code=code, duration_ms=dur,
                                     detail={"undoes": rec["seq"] if outcome == "ok" else None,
                                             "attempted": rec["seq"], "verified": bool(r.get("verified"))}, **base)
        except AuditUnavailable:
            oseq = None
        if outcome != "ok":
            msg = {"UNDO_CONFLICT": "The change could not be undone: it is no longer the newest change (another edit, "
                                    "agent or a TouchDesigner restart happened since).",
                   "UNDO_UNVERIFIED": "Undo ran, but the restored state could not be verified. Inspect the operator."}
            return tool_error(code, msg.get(code, "The change could not be undone."), audit_seq=oseq,
                              undoes=rec["seq"], undone=bool(r.get("undone")), verified=False)
        return tool_result({"undone": True, "verified": bool(r.get("verified")), "undoes": rec["seq"],
                            "undone_tool": rec.get("tool"), "audit_seq": oseq})


def _requested_targets(tool: Tool, args: dict, facts=None) -> List[Target]:
    """Path-only targets straight from the arguments (used when resolution fails), with protection facts
    from the adapter so errors never describe protected operators (review 6)."""
    primary = {"set_params": "update", "create": "create", "delete": "delete", "wire": "update",
               "dat_text": "update"}.get(tool.resolver, "read")
    pairs = []
    if isinstance(args.get("path"), str):
        pairs.append((primary, args["path"]))
    if isinstance(args.get("parent"), str) and isinstance(args.get("name"), str):
        pairs.append((primary, args["parent"].rstrip("/") + "/" + args["name"]))
    if isinstance(args.get("dst"), str):
        pairs.append((primary, args["dst"]))
    if isinstance(args.get("src"), str):
        pairs.append(("read", args["src"]))
    out = []
    for action, path in pairs:
        prot, anc = facts(path) if facts else ("", "")
        out.append(Target(action, path, protected=prot, ancestor_of=anc, op_type="" if action != "create" else "?"))
    return out or [Target("read", "/")]


def _json(text, default=None):
    import json
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except ValueError:
        return {} if default is None else default
