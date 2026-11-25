"""Approvals (security-model §4, system-design §4; Phase 2B). Pure Python, no `td`, no storage.

The model:
- A call that policy would allow only with approval is **not executed**. The server records a *pending* request
  (who, which tool, the complete arguments and their SHA-256, the resolved targets, the policy version, an
  expiry) and answers at once. Nobody waits: TouchDesigner's main thread never blocks for a human.
- The user decides on the dashboard, which shows the **complete raw arguments** (full code, full expression
  text) and the resolved targets, never only an agent-written summary.
- Execution happens **only when the agent repeats the identical call**: same agent, same tool, same argument
  digest. At that moment the server re-resolves the targets (they must be identical to the approved ones) and
  re-evaluates policy at the current version (a revoked grant cancels the approval). An approval is single-use.
- A decision carries a MAC made with the installation key (private per-user storage), so a row edited in the
  project-local database is not an approval.

Coding agents usually cannot attach custom ``_meta`` to a tool call, so redemption does not depend on
``_meta.tdflow.approval_id``: the identical re-call finds its approval by digest. If a client does send an id, it
must name that same approval.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Any, List, Mapping, Optional, Sequence, Tuple

EXPIRY_S = 600                       # a pending or approved request is usable for ten minutes
MAX_ARGS_CHARS = 400000              # complete arguments are stored for display; larger calls cannot be approved
MAX_PENDING_PER_AGENT = 5            # an agent cannot flood the queue
STATES = ("pending", "approved", "denied", "expired", "redeemed", "cancelled")
OPEN = ("pending", "approved")
ID = re.compile(r"^apr_[A-Za-z0-9]{16}$")
_TOKEN = re.compile(r"tdf_[A-Za-z0-9]{6,}\.[A-Za-z0-9_\-]{8,}")


class ApprovalError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code, self.message = code, message or code


def new_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "apr_" + "".join(secrets.choice(alphabet) for _ in range(16))


def canonical_args(args: Any) -> str:
    """One text for one set of arguments (sorted keys, no insignificant whitespace, no NaN/Infinity). The digest
    is always computed by this server, for the request and for the re-call, so both use the same rules."""
    try:
        return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ApprovalError("INVALID_ARGS", "the arguments cannot be represented exactly, so they cannot be approved") from None


def digest(tool: str, args: Any) -> Tuple[str, str]:
    """(canonical argument text, SHA-256 over tool name + arguments)."""
    text = canonical_args(args)
    if len(text) > MAX_ARGS_CHARS:
        raise ApprovalError("APPROVAL_TOO_LARGE", "the arguments are too large to be shown for approval")
    if any(_INVISIBLE.search(x) for x in _strings(args)):
        # Review 13 ("Trojan Source"): bidirectional overrides and invisible characters can make the code the
        # user reads differ from the code that runs. Such a call cannot be approved.
        raise ApprovalError("APPROVAL_REFUSED", "the arguments contain invisible or text-direction characters, so "
                            "the user could not see exactly what would run; remove them")
    if _TOKEN.search(text):
        # The complete arguments are stored and shown to the user; a credential must never end up there.
        raise ApprovalError("APPROVAL_REFUSED", "the arguments contain a TDflowMCP token; remove it")
    h = hashlib.sha256((tool + "\n" + text).encode("utf-8", "replace")).hexdigest()
    return text, h


def _strings(v):
    """Every string inside the arguments, keys included (control characters are escaped by JSON, so the decoded
    values are checked, not the encoded text)."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield str(k)
            yield from _strings(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)


def targets_key(targets: Sequence[Sequence[Any]]) -> str:
    """The resolved targets as one comparable text: at redemption they must be exactly these. Each target carries
    its facts (action, path, change, parameter, operator count, operator type), so a delete approved for 60
    operators does not run after the component grew to 5000 (review 13)."""
    return json.dumps([[str(x) for x in t] for t in targets], separators=(",", ":"))


def target_facts(targets) -> List[List[Any]]:
    """``policy.Target`` objects → the facts bound by an approval."""
    return [[t.action, t.path, t.change, t.param, int(t.count), t.op_type] for t in targets]


_INVISIBLE = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u00ad\u061c\u180e"
                        "\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]")


def decision_mac(key: bytes, approval: Mapping, decision: str, decided_at: str) -> str:
    """Binds a decision to exactly this request. ``key``: the store's key for the label ``approval``."""
    blob = "\n".join([str(approval["id"]), str(approval["agent_id"]), str(approval["tool"]), str(approval["args_sha256"]),
                      str(approval["targets"]), str(approval["policy_version"]), str(approval["expires_at"]),
                      decision, decided_at])
    return hmac.new(key, blob.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


def usable(approval: Mapping, now: float) -> str:
    """The effective state at ``now`` (a pending or approved request past its expiry is expired)."""
    st = str(approval.get("state"))
    if st in OPEN and float(approval.get("expires_at") or 0) <= now:
        return "expired"
    return st if st in STATES else "cancelled"


def check_redemption(approval: Mapping, key: bytes, agent_id: str, tool: str, args_sha: str, targets: Sequence[Sequence[str]],
                     now: float, given_id: Optional[str] = None) -> None:
    """Raise ApprovalError unless this exact call may run under this approval right now. Policy is re-evaluated by
    the engine separately; this checks identity, digest, targets, state, expiry and the decision MAC."""
    if given_id is not None and given_id != approval.get("id"):
        raise ApprovalError("APPROVAL_MISMATCH", "that approval id does not belong to this call")
    if approval.get("agent_id") != agent_id or approval.get("tool") != tool or approval.get("args_sha256") != args_sha:
        raise ApprovalError("APPROVAL_MISMATCH", "the call is not identical to the approved one")
    st = usable(approval, now)
    if st != "approved":
        raise ApprovalError("APPROVAL_" + st.upper(), {
            "pending": "the user has not decided yet", "denied": "the user denied this request",
            "expired": "the approval expired; ask again", "redeemed": "the approval was already used",
            "cancelled": "the approval was cancelled"}.get(st, st))
    if approval.get("targets") != targets_key(targets):
        raise ApprovalError("APPROVAL_TARGETS_CHANGED", "the project changed: the call no longer resolves to the "
                            "operators that were approved. Ask again.")
    want = decision_mac(key, approval, "approved", str(approval.get("decided_at")))
    if not hmac.compare_digest(str(approval.get("mac") or ""), want):
        raise ApprovalError("APPROVAL_INVALID", "the approval record does not verify; it was not made by the user "
                            "on this installation")


def summary_for_agent(approval: Mapping, now: float) -> dict:
    """What an agent learns about its own request (never another agent's)."""
    st = usable(approval, now)
    out = {"approval_id": approval["id"], "state": st, "tool": approval["tool"],
           "expires_in_s": max(0, int(float(approval["expires_at"]) - now)) if st in OPEN else 0}
    if st == "pending":
        out["next"] = "Wait for the user to decide on the TDflowMCP dashboard, then repeat the identical call."
    elif st == "approved":
        out["next"] = "Repeat the identical call now (same tool, same arguments). It runs once."
    elif st == "denied":
        out["note"] = str(approval.get("note") or "")[:300]
        out["next"] = "The user denied this. Do not retry it or look for another way; ask the user."
    return out


def view_for_user(approval: Mapping, now: float) -> dict:
    """The dashboard row: complete raw arguments and resolved targets. Agent text is data, never an instruction."""
    try:
        targets = json.loads(approval.get("targets") or "[]")
    except ValueError:
        targets = []
    return {"id": approval["id"], "state": usable(approval, now), "created_at": approval.get("created_at"),
            "expires_in_s": max(0, int(float(approval.get("expires_at") or 0) - now)),
            "agent_id": approval.get("agent_id"), "agent_name": approval.get("agent_name"), "tool": approval.get("tool"),
            "reasons": _reasons(approval.get("reasons")), "targets": targets,
            "args": approval.get("args"), "args_sha256": approval.get("args_sha256"),
            "policy_version": approval.get("policy_version"), "decided_at": approval.get("decided_at"),
            "note": approval.get("note"), "agent_text_untrusted": True}


def _reasons(text) -> List[str]:
    try:
        v = json.loads(text or "[]")
        return [str(x)[:200] for x in v][:10] if isinstance(v, list) else []
    except ValueError:
        return []
