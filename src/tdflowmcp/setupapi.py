"""Read-only Setup Assistant API payloads (setup-assistant §7, Phase 1A). Pure Python, no `td`.

``GET /setup/api/status`` and ``GET /setup/api/tellagent`` are built here from data the TD adapter
collected on the main thread. ``/.well-known/tdflowmcp`` carries product, version and the non-secret connect id (§8.1).
"""
from __future__ import annotations

import json
from typing import Mapping, Sequence

from tdflowmcp import PHASE, TOOL_SURFACE_VERSION, __version__
from tdflowmcp.compat import CheckResult, summary


def well_known(version: str = __version__, connect: str = "") -> bytes:
    """Unauthenticated, loopback only. ``connect`` is the project's non-secret storage id (also its Projectid
    parameter), present once the store is open: Claude Code's headersHelper uses it to pick this project's credential
    (ADR-0015). It grants nothing."""
    body = {"product": "TDflowMCP", "version": version}
    if connect:
        body["connect"] = connect
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def status(ctl: Mapping, td_build: str, checks: Sequence[CheckResult], troubleshooting: Mapping,
           tell_agent: str, checks_age_s: float, version: str = __version__) -> dict:
    entries = troubleshooting.get("entries", {}) if isinstance(troubleshooting, Mapping) else {}
    out_checks = []
    for c in checks:
        e = entries.get(c.remediation_id, {}) if c.remediation_id else {}
        out_checks.append({"id": c.id, "result": c.result, "evidence": c.evidence,
                           "remediation_id": c.remediation_id, "title": e.get("title", ""),
                           "cause": e.get("cause", ""), "confirm": e.get("confirm", ""),
                           "steps": list(e.get("steps", [])), "docs": list(e.get("docs", []))})
    return {
        "product": "TDflowMCP", "version": version, "phase": PHASE, "tool_surface": TOOL_SURFACE_VERSION,
        "td_build": td_build,
        "state": ctl.get("state"), "error_code": ctl.get("error_code"), "message": ctl.get("message"),
        "port": ctl.get("port"), "portstatus": ctl.get("portstatus"), "url": ctl.get("url"),
        "gate": ctl.get("gate"), "reverifies": ctl.get("reverifies"), "mcp": "disabled (Phase 1A)",
        "summary": summary(checks) if checks else "not run",
        "checks_age_s": round(checks_age_s, 1) if checks else None,
        "checks": out_checks,
        "tell_agent": tell_agent,
    }


def dumps(obj) -> bytes:
    return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
