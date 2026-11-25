"""Governance dashboard logic (ADR-0005, Phase 2B): audit search, live tail, sessions, reports. Pure Python, no `td`.

Everything here runs on HTTP handler threads against the store proxy (the audit worker). Nothing needs
TouchDesigner's main thread, so the dashboard keeps working while TouchDesigner is not running frames (a
minimized window, spike S9).

The dashboard is a **view and decision surface only**: every rule is enforced by the engine. Text that agents
wrote (arguments, notes, summaries) is returned as data, labelled, and rendered by the page with ``textContent``.

Audit search never accepts SQL from the browser: ``event_filter`` turns a small set of validated fields into a
parameterised WHERE clause. Searches arrive as JSON bodies of POST requests, never as query strings.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

KINDS = ("intent", "outcome", "deny", "admin", "mcp", "kill_switch", "auth_fail_burst", "migration", "approval")
OUTCOMES = ("ok", "error", "aborted", "unknown")
MAX_LIMIT = 500
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_TOOL = re.compile(r"^[a-z_]{1,64}$")
SHOWN = ("seq", "ts", "kind", "agent_id", "token_id", "session_id", "client_info", "tool", "targets", "decision",
         "rule_id", "policy_version", "approval_id", "outcome", "error_code", "duration_ms", "undo_label",
         "request_id", "args_redacted", "detail")


class BadQuery(Exception):
    pass


def _int(q: Mapping, key: str, default: Optional[int], lo: int, hi: int) -> Optional[int]:
    v = q.get(key)
    if v in (None, ""):
        return default
    try:
        n = int(str(v))
    except ValueError:
        raise BadQuery(key) from None
    if not lo <= n <= hi:
        raise BadQuery(key)
    return n


def clean_query(body: Mapping) -> Dict[str, str]:
    """Search fields from a JSON request body (dashboard searches are POSTs: exact Origin and CSRF, and no query
    string ever reaches the router). Only scalars are kept, as short strings."""
    out: Dict[str, str] = {}
    for k, v in (body or {}).items():
        if isinstance(k, str) and len(k) <= 32 and isinstance(v, (str, int)) and not isinstance(v, bool):
            if len(str(v)) > 256:
                raise BadQuery(k)
            out[k] = str(v)
    return out


def event_filter(q: Mapping[str, str]) -> Tuple[str, tuple, int]:
    """(where, params, limit) for ``Store.events``. Every value is validated and passed as a parameter."""
    where: List[str] = []
    params: List[Any] = []
    after = _int(q, "after", None, 0, 2 ** 62)
    before = _int(q, "before", None, 1, 2 ** 62)
    if after is not None:
        where.append("seq > ?")
        params.append(after)
    if before is not None:
        where.append("seq < ?")
        params.append(before)
    for key, col, pat in (("agent", "agent_id", _ID), ("session", "session_id", _ID), ("tool", "tool", _TOOL),
                          ("request", "request_id", _ID)):
        v = q.get(key)
        if v:
            if not pat.match(v):
                raise BadQuery(key)
            where.append(f"{col} = ?")
            params.append(v)
    kind = q.get("kind")
    if kind:
        if kind not in KINDS:
            raise BadQuery("kind")
        where.append("kind = ?")
        params.append(kind)
    outcome = q.get("outcome")
    if outcome:
        if outcome == "denied":
            where.append("kind = 'deny'")
        elif outcome in OUTCOMES:
            where.append("outcome = ?")
            params.append(outcome)
        else:
            raise BadQuery("outcome")
    text = q.get("q")
    if text:
        if not re.match(r"^[A-Za-z0-9_./\-]{1,64}$", text):
            raise BadQuery("q")
        like = "%" + text.replace("_", r"\_") + "%"
        where.append(r"(targets LIKE ? ESCAPE '\' OR args_redacted LIKE ? ESCAPE '\' OR error_code LIKE ? ESCAPE '\')")
        params += [like, like, like]
    return " AND ".join(where), tuple(params), _int(q, "limit", 100, 1, MAX_LIMIT)


def _json(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def shape_event(ev: Mapping) -> dict:
    """One audit event for the dashboard: the stored fields, with JSON text fields decoded."""
    out = {k: ev.get(k) for k in SHOWN}
    out["targets"] = _json(ev.get("targets"), [])
    out["detail"] = _json(ev.get("detail"), {})
    out["client_info"] = _json(ev.get("client_info"), ev.get("client_info"))
    out["agent_text_untrusted"] = True           # arguments and notes come from agents
    return out


def events_page(store, q: Mapping[str, str]) -> dict:
    """Newest first. ``after=<seq>`` is the live tail: only events newer than what the page already has."""
    where, params, limit = event_filter(q)
    rows = store.events(where, params, limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    return {"events": [shape_event(r) for r in rows], "more": more, "last_seq": store.max_seq()}


def summarize(events: Sequence[Mapping]) -> dict:
    """Counts for a report or a session view."""
    by_tool: Dict[str, Dict[str, int]] = {}
    denied, errors, changed = 0, 0, set()
    for e in events:
        tool = e.get("tool") or "-"
        t = by_tool.setdefault(tool, {"calls": 0, "ok": 0, "denied": 0, "failed": 0})
        if e.get("kind") == "outcome":
            t["calls"] += 1
            if e.get("outcome") == "ok":
                t["ok"] += 1
                for action, path in _json(e.get("targets"), []) if isinstance(_json(e.get("targets"), []), list) else []:
                    if action in ("create", "update", "delete"):
                        changed.add(path)
            else:
                t["failed"] += 1
                errors += 1
        elif e.get("kind") == "deny":
            t["denied"] += 1
            denied += 1
    return {"by_tool": {k: by_tool[k] for k in sorted(by_tool)}, "denied": denied, "failed": errors,
            "changed_paths": sorted(changed)[:500], "changed_paths_total": len(changed)}


def unaccounted(events: Sequence[Mapping]) -> List[dict]:
    """Intents without an outcome: executions the audit log cannot account for (a crash or an audit failure)."""
    by: Dict[tuple, List[str]] = {}
    first: Dict[tuple, Mapping] = {}
    for e in events:
        if e.get("kind") in ("intent", "outcome") and e.get("request_id"):
            k = (e["request_id"], e.get("tool"), e.get("token_id"))
            by.setdefault(k, []).append(e["kind"])
            if e["kind"] == "intent":
                first[k] = e
    return [{"seq": first[k].get("seq"), "tool": k[1], "agent_id": first[k].get("agent_id"), "ts": first[k].get("ts")}
            for k, v in by.items() if "outcome" not in v and k in first]


def build_report(store, q: Mapping[str, str], version: str, limit: int = 5000) -> dict:
    """A session or range report with its verification (ADR-0005 gate: the export verifies against the
    checkpoint). ``verification`` is the store's own check of the hash chain and the keyed checkpoints, done
    now; ``covered`` says whether the reported range lies entirely inside the verified part."""
    session = q.get("session")
    lo = _int(q, "from_seq", None, 0, 2 ** 62)
    hi = _int(q, "to_seq", None, 1, 2 ** 62)
    where, params = [], []
    if session:
        if not _ID.match(session):
            raise BadQuery("session")
        where.append("session_id = ?")
        params.append(session)
    if lo is not None:
        where.append("seq >= ?")
        params.append(lo)
    if hi is not None:
        where.append("seq <= ?")
        params.append(hi)
    if not where:
        raise BadQuery("session or from_seq/to_seq")
    rows = list(reversed(store.events(" AND ".join(where), tuple(params), limit + 1)))
    truncated = len(rows) > limit
    rows = rows[-limit:] if truncated else rows
    v = store.verify()
    seqs = [int(r["seq"]) for r in rows]
    covered = bool(rows) and v.get("ok") is True and max(seqs) <= int(v.get("verified_through") or 0)
    events = [shape_event(r) for r in rows]
    body = {"schema": "tdflowmcp.report/1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tdflowmcp": version, "scope": {"session": session, "from_seq": lo, "to_seq": hi},
            "range": {"first_seq": min(seqs) if seqs else None, "last_seq": max(seqs) if seqs else None,
                      "events": len(rows), "truncated": truncated},
            "summary": summarize(rows), "unaccounted_executions": unaccounted(rows),
            "verification": {"chain_ok": v.get("chain_ok"), "checkpoints_ok": v.get("checkpoints_ok"),
                             "ok": v.get("ok"), "verified_through": v.get("verified_through"),
                             "unverified_tail": v.get("unverified_tail"), "first_bad_seq": v.get("first_bad_seq"),
                             "covered": covered,
                             "statement": ("Every event in this report lies inside the part of the audit log whose hash "
                                           "chain and keyed checkpoints verify." if covered else
                                           "NOT fully verified: the audit log failed verification, or part of this "
                                           "range is newer than the last keyed checkpoint.")},
            "events": events}
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
                                    .encode("utf-8", "replace")).hexdigest()
    return body


def report_markdown(rep: Mapping) -> str:
    """The same report as text. Agent-written values are fenced as data; nothing here is an instruction."""
    r, v, s = rep["range"], rep["verification"], rep["summary"]
    out = ["# TDflowMCP audit report", "",
           f"- Generated: {rep['generated_at']} by TDflowMCP {rep['tdflowmcp']}",
           f"- Scope: session `{rep['scope'].get('session') or '-'}`, events {r['first_seq']} to {r['last_seq']} "
           f"({r['events']} events{', truncated' if r['truncated'] else ''})",
           f"- Verification: **{'verified' if v['covered'] else 'NOT fully verified'}**. {v['statement']}",
           f"  Chain {'intact' if v['chain_ok'] else 'BROKEN'}, checkpoints {'valid' if v['checkpoints_ok'] else 'INVALID'}, "
           f"verified through event {v['verified_through']}, {v['unverified_tail']} event(s) after the last checkpoint.",
           f"- Report digest (SHA-256 of the JSON report): `{rep.get('sha256', '')}`", "",
           "## Summary", "", "| Tool | Calls | OK | Failed | Denied |", "|---|---|---|---|---|"]
    for tool, c in s["by_tool"].items():
        out.append(f"| `{tool}` | {c['calls']} | {c['ok']} | {c['failed']} | {c['denied']} |")
    out += ["", f"Changed operator paths ({s['changed_paths_total']}):", ""]
    out += [f"- `{p}`" for p in s["changed_paths"][:100]] or ["- none"]
    un = rep["unaccounted_executions"]
    out += ["", f"## Executions without a recorded outcome ({len(un)})", ""]
    out += [f"- event {u['seq']}: `{u['tool']}` by `{u['agent_id']}` at {u['ts']}" for u in un] or ["- none"]
    out += ["", "## Events", "", "| Seq | Time | Kind | Agent | Tool | Result | Targets |", "|---|---|---|---|---|---|---|"]
    for e in rep["events"]:
        res = e.get("outcome") or e.get("decision") or ""
        if e.get("error_code"):
            res += f" ({e['error_code']})"
        tg = ", ".join(f"{a} {p}" for a, p in (e.get("targets") or [])[:3] if isinstance(a, str) and isinstance(p, str))
        out.append(f"| {e['seq']} | {e['ts']} | {e['kind']} | `{e.get('agent_id') or '-'}` | `{e.get('tool') or '-'}` | "
                   f"{res} | `{tg}` |")
    return "\n".join(out) + "\n"
