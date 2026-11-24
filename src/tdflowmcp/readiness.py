"""First-run readiness (owner UX assignment, 2026-11-23). Pure Python, no `td`.

Four separate states, never confused with one another:
- **Environment Ready:** the component and the system prerequisites that can be checked without listening pass
  (TouchDesigner build, bundled Python and modules, loopback capability, port, a single instance).
- **MCP Ready:** storage, audit integrity and at least one enabled agent credential exist. The live loopback
  verification still runs every time MCP is enabled; nothing here replaces it.
- **MCP Enabled:** the endpoint is serving agents.
- **Agent Connected:** an authenticated agent request was actually seen recently (``connected`` rows), not merely
  a registered agent.

``evaluate`` turns facts into those states plus one plain-language status line with the next step. Checks that
need a listener or a connection are reported as not yet verified, never as passed.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence

ENV_CHECKS = ("td.build", "py.version", "py.deps", "net.capability", "net.port", "td.instances")
MCP_CHECKS = ("fs.datadir", "audit.integrity")
LIVE_CHECKS = ("net.bind", "sec.selftest")        # verified live when MCP is enabled
CONNECTED_WITHIN_S = 120

CHECK_LABELS = {
    "td.build": "TouchDesigner build", "py.version": "Python version", "py.deps": "Python modules",
    "net.capability": "loopback networking", "net.port": "port", "td.instances": "single TDflowMCP component",
    "fs.datadir": "storage", "audit.integrity": "audit log", "net.bind": "loopback-only binding",
    "sec.selftest": "security self-test", "agent.clients": "coding agents",
}


def _result(c) -> tuple:
    if isinstance(c, Mapping):
        return c.get("id"), c.get("result")
    return getattr(c, "id", None), getattr(c, "result", None)


def environment(checks: Sequence) -> Dict:
    """{"state": "ready" | "warning" | "not_ready" | "pending", "failing": [ids], "unverified": [ids]}"""
    by = dict(_result(c) for c in checks or ())
    if not by:
        return {"state": "pending", "failing": [], "unverified": list(ENV_CHECKS)}
    failing = [i for i in ENV_CHECKS if by.get(i) == "fail"]
    unverified = [i for i in ENV_CHECKS if by.get(i) in (None, "unknown")]
    warn = [i for i in ENV_CHECKS if by.get(i) == "warn"]
    state = "not_ready" if failing else ("pending" if unverified else ("warning" if warn else "ready"))
    return {"state": state, "failing": failing, "unverified": unverified, "warnings": warn}


def mcp(checks: Sequence, store_open: bool, agents_enabled: int) -> Dict:
    """{"state": "ready" | "not_ready" | "not_verified", "missing": [...]}"""
    by = dict(_result(c) for c in checks or ())
    missing = []
    if not store_open:
        return {"state": "not_verified", "missing": ["storage (opens when you connect an agent)"]}
    for i in MCP_CHECKS:
        if by.get(i) != "pass":
            missing.append(CHECK_LABELS[i])
    if agents_enabled < 1:
        missing.append("an agent")
    return {"state": "ready" if not missing else "not_ready", "missing": missing,
            "live_check": "loopback binding and self-test are verified each time MCP is enabled"}


def connected(rows: Sequence[Mapping], now: float) -> List[Dict]:
    """Agents with an authenticated request within ``CONNECTED_WITHIN_S``. ``rows``: {agent, client, last}."""
    out = [dict(r, age_s=int(now - r["last"])) for r in rows or () if now - r.get("last", 0) <= CONNECTED_WITHIN_S]
    return sorted(out, key=lambda r: r["age_s"])


def evaluate(f: Mapping) -> Dict:
    """Facts: checks, store_open, agents_enabled, mcp_on, suspended, error_code, connected (rows), now,
    client (display name of the selected or detected coding agent), handoff (a new token waits to be copied)."""
    env = environment(f.get("checks") or ())
    m = mcp(f.get("checks") or (), bool(f.get("store_open")), int(f.get("agents_enabled") or 0))
    conn = connected(f.get("connected") or (), float(f.get("now") or 0))
    client = f.get("client") or "your coding agent"
    if f.get("error_code"):
        stage, line = "error", f"Error {f['error_code']}. Pulse Run Diagnostics."
    elif f.get("suspended"):
        stage, line = "suspended", "Agents suspended. Turn off Suspend Agents to resume."
    elif env["state"] == "pending" and not f.get("checks"):
        stage, line = "checking", "Environment checks pending. Pulse Run Diagnostics."
    elif env["state"] == "not_ready":
        names = ", ".join(CHECK_LABELS.get(i, i) for i in env["failing"])
        stage, line = "env_not_ready", f"Not ready: {names}. See View Diagnostics (Advanced)."
    elif conn:
        who = conn[0]
        more = f" (+{len(conn) - 1} more)" if len(conn) > 1 else ""
        stage, line = "connected", f"{who.get('client') or who.get('agent')} connected{more}. Ready."
    elif f.get("mcp_on"):
        if f.get("handoff"):
            stage, line = "enabled_handoff", f"Pulse Copy Token, then start {client}."
        else:
            stage, line = "enabled_waiting", f"Waiting for {client} to connect."
    elif int(f.get("agents_enabled") or 0) < 1:
        stage, line = "env_ready", "Ready. Pulse Connect Agent."
    elif f.get("handoff"):
        stage, line = "agent_created", "Pulse Copy Token, then turn on Enable MCP."
    else:
        stage, line = "agent_configured", "Agent ready. Turn on Enable MCP."
    return {"environment": env, "mcp": m, "mcp_enabled": bool(f.get("mcp_on")), "agent_connected": bool(conn),
            "connected": conn, "stage": stage, "line": line}


def system_line(checks: Sequence) -> str:
    """Compact System Check parameter value."""
    env = environment(checks)
    if env["state"] == "pending" and not checks:
        return "Not run yet"
    by = dict(_result(c) for c in checks or ())
    counts = {k: sum(1 for v in by.values() if v == k) for k in ("pass", "warn", "fail", "unknown")}
    label = {"ready": "Ready", "warning": "Ready, with warnings", "not_ready": "Not ready",
             "pending": "Partly checked"}[env["state"]]
    tail = f"{counts['fail']} fail" if counts["fail"] else (f"{counts['warn']} warn" if counts["warn"] else
                                                             f"{counts['pass']} pass")
    return f"{label} · {tail} · {counts['unknown']} later"


def report(checks: Sequence, remediation: Optional[Mapping] = None, detected: Sequence[str] = ()) -> str:
    """Full diagnostics text for the native viewer (no secrets, no absolute paths)."""
    lines = ["TDflowMCP diagnostics", "", system_line(checks), ""]
    for c in checks or ():
        cid, res = _result(c)
        ev = c.get("evidence") if isinstance(c, Mapping) else getattr(c, "evidence", "")
        rid = c.get("remediation_id") if isinstance(c, Mapping) else getattr(c, "remediation_id", None)
        word = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "unknown": "NOT YET VERIFIED"}.get(res, str(res))
        lines.append(f"[{word}] {CHECK_LABELS.get(cid, cid)} ({cid}): {ev}")
        fix = (remediation or {}).get(rid) if rid else None
        if fix and res in ("warn", "fail"):
            steps = fix.get("fix") or fix.get("steps") or []
            if isinstance(steps, str):
                steps = [steps]
            for s in steps[:6]:
                lines.append(f"    - {s}")
    lines += ["", "Coding agents found on PATH (not run): " + (", ".join(detected) if detected else "none"),
              "", "Checks that need a listener or a connection (loopback-only binding, security self-test, agent "
              "connection) are verified when you enable MCP; they show NOT YET VERIFIED until then."]
    return "\n".join(lines)
