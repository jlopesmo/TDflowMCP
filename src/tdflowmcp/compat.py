"""Compatibility checks (setup-assistant §3): pure functions over collected facts.

The TouchDesigner adapter (Phase 1A item 9) collects ``facts``; this module only judges them.
Checks diagnose — they never install, upgrade or reconfigure anything (invariant 9).
Missing, ``None`` or malformed facts yield 'unknown', never 'pass'.

Fact contract (all keys optional):
  td_build, tox_saved_build: "YYYY.NNNNN"
  python_version: (3, 11, 10) or "3.11.10"
  modules: {name: <module fact>} where a module fact is
           True | version string (incl. "")   -> importable
           False | None                       -> not importable
           {"ok": bool, "version": str}       -> importable iff ok is True
  transport: "python-http" (default, ADR-0002 rev. 2026-11-22) | "webserver-dat"
  bind_host: str                   (python-http) the configured bind literal
  exclusive_bind: bool             (python-http) SO_EXCLUSIVEADDRUSE available (Windows) / not needed (other OS)
  webserver_par_names: list[str]   (webserver-dat)
  bind_evidence: bindverify.Evidence
  port, port_listeners_other: list of (addr, port, pid) of OTHER processes; port_probe_tdflowmcp: bool
  datadir_writable: bool
  instances: number of TDflowMCP components in the project (including this one)
  clients: {client_id: {"selected": bool, "present": bool | None, "support": str}}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from tdflowmcp.net import bindverify

# Oldest build TDflowMCP is tested on and supports (owner D10, 2026-11-22: 2025.32440 stays supported;
# S6 showed the embedded Python transport meets the security model there).
MIN_SUPPORTED_BUILD = (2025, 32440)
TRANSPORTS = ("python-http", "webserver-dat")
PYTHON_MAJOR_MINOR = (3, 11)
REQUIRED_MODULES = ("json", "sqlite3", "ssl", "hmac", "hashlib", "secrets", "jsonschema",
                    "http.server", "socketserver", "threading", "queue", "socket")
RESULTS = ("pass", "warn", "fail", "unknown")


@dataclass(frozen=True)
class CheckResult:
    id: str
    result: str
    evidence: str
    remediation_id: str = ""

    def __post_init__(self):
        if self.result not in RESULTS:
            raise ValueError(f"bad result {self.result!r}")
        if self.result in ("warn", "fail") and not self.remediation_id:
            raise ValueError(f"{self.id}: warn/fail requires a remediation_id")


def parse_build(text: Optional[str]) -> Optional[Tuple[int, int]]:
    try:
        major, minor = str(text).strip().split(".")
        return int(major), int(minor)
    except (AttributeError, ValueError):
        return None


def check_td_build(f: Mapping) -> CheckResult:
    b = parse_build(f.get("td_build"))
    if b is None:
        return CheckResult("td.build", "unknown", "TouchDesigner build not reported")
    ev = f"TouchDesigner {b[0]}.{b[1]}"
    saved = parse_build(f.get("tox_saved_build"))
    if saved and saved > b:
        return CheckResult("td.build", "fail", ev + f"; component saved by newer build {saved[0]}.{saved[1]}",
                           "td.build.tox_newer")
    if b >= MIN_SUPPORTED_BUILD:
        return CheckResult("td.build", "pass", ev)
    return CheckResult("td.build", "warn", ev + f" is older than the oldest tested build "
                       f"{MIN_SUPPORTED_BUILD[0]}.{MIN_SUPPORTED_BUILD[1]}", "td.build.older")


def _parse_version(v) -> Optional[Tuple[int, ...]]:
    try:
        parts = v.split(".") if isinstance(v, str) else list(v)
        out = tuple(int(x) for x in parts[:3])
        return out if len(out) >= 2 else None
    except (TypeError, ValueError):
        return None


def _module_present(fact) -> Optional[bool]:
    if fact is True or isinstance(fact, str):
        return True
    if fact is False or fact is None:
        return False
    if isinstance(fact, Mapping):
        return fact.get("ok") is True
    return None


def check_py_version(f: Mapping) -> CheckResult:
    v = _parse_version(f.get("python_version"))
    if v is None:
        return CheckResult("py.version", "unknown", "Python version not reported or malformed")
    ev = "Python " + ".".join(map(str, v))
    if v[:2] == PYTHON_MAJOR_MINOR:
        return CheckResult("py.version", "pass", ev)
    return CheckResult("py.version", "fail", ev + " (3.11.x required)", "py.version.mismatch")


def check_py_deps(f: Mapping) -> CheckResult:
    mods = f.get("modules")
    if not isinstance(mods, Mapping):
        return CheckResult("py.deps", "unknown", "module availability not reported")
    states = {m: _module_present(mods.get(m)) for m in REQUIRED_MODULES}
    if any(v is None for v in states.values()):
        return CheckResult("py.deps", "unknown", "malformed module facts: "
                           + ", ".join(m for m, v in states.items() if v is None))
    missing = [m for m, ok in states.items() if not ok]
    if missing:
        return CheckResult("py.deps", "fail", "missing: " + ", ".join(missing), "py.deps.missing")
    js = mods.get("jsonschema")
    ver = js if isinstance(js, str) else (js.get("version") if isinstance(js, Mapping) else "")
    return CheckResult("py.deps", "pass", "all required bundled modules importable"
                       + (f"; jsonschema {ver}" if ver else ""))


def check_net_capability(f: Mapping) -> CheckResult:
    transport = f.get("transport")
    if transport not in TRANSPORTS:
        return CheckResult("net.capability", "unknown", "transport not reported")
    if transport == "python-http":
        host, excl = f.get("bind_host"), f.get("exclusive_bind")
        if not isinstance(host, str) or not isinstance(excl, bool):
            return CheckResult("net.capability", "unknown", "embedded transport capability not inspected")
        if bindverify.embedded_capability(host).verdict is not bindverify.Verdict.PASS:
            return CheckResult("net.capability", "fail", f"bind host {host!r} is not the loopback literal "
                               "127.0.0.1; the listener will not be activated", "net.capability.bind_host")
        if not excl:
            return CheckResult("net.capability", "fail", "exclusive socket binding unavailable; another socket "
                               "could share the port; the listener will not be activated",
                               "net.capability.no_exclusive_bind")
        return CheckResult("net.capability", "pass", "embedded transport binds 127.0.0.1 exclusively")
    pars = f.get("webserver_par_names")
    if pars is None:
        return CheckResult("net.capability", "unknown", "Web Server DAT parameters not inspected")
    ev = bindverify.capability_check(pars)
    if ev.verdict is bindverify.Verdict.PASS:
        return CheckResult("net.capability", "pass", "Web Server DAT supports localaddress")
    return CheckResult("net.capability", "fail",
                       "Web Server DAT has no localaddress parameter: it cannot bind loopback-only; "
                       "the listener will not be activated", "net.capability.no_localaddress")


def check_net_bind(f: Mapping) -> CheckResult:
    ev = f.get("bind_evidence")
    if not isinstance(ev, bindverify.Evidence):
        return CheckResult("net.bind", "unknown", "listener not verified (not started)")
    if ev.verdict is bindverify.Verdict.PASS:
        return CheckResult("net.bind", "pass", "effective binding verified loopback-only")
    return CheckResult("net.bind", "fail", f"binding not verified ({ev.verdict.value}: "
                       + ", ".join(ev.reasons) + "); listener closed", "net.bind.unverified")


def check_net_port(f: Mapping) -> CheckResult:
    others = f.get("port_listeners_other")
    if not isinstance(others, (list, tuple)):
        return CheckResult("net.port", "unknown", "port not checked")
    port = f.get("port", 9480)
    if not others:
        return CheckResult("net.port", "pass", f"port {port} free")
    who = "responds like TDflowMCP (unverified)" if f.get("port_probe_tdflowmcp") else "another process"
    return CheckResult("net.port", "fail", f"port {port} in use by {who}", "net.port.in_use")


def check_datadir(f: Mapping) -> CheckResult:
    w = f.get("datadir_writable")
    if not isinstance(w, bool):
        return CheckResult("fs.datadir", "unknown", "data directory not checked")
    if w:
        return CheckResult("fs.datadir", "pass", "data directory writable")
    return CheckResult("fs.datadir", "fail", "data directory not writable", "fs.datadir.unwritable")


def check_instances(f: Mapping) -> CheckResult:
    n = f.get("instances")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return CheckResult("td.instances", "unknown", "TDflowMCP components not counted")
    if n == 1:
        return CheckResult("td.instances", "pass", "exactly one TDflowMCP component")
    return CheckResult("td.instances", "warn", f"{n} TDflowMCP components in the project", "td.instances.multiple")


def check_agent_clients(f: Mapping) -> CheckResult:
    clients = f.get("clients")
    if not isinstance(clients, Mapping) or not clients:
        return CheckResult("agent.clients", "unknown", "no coding agent selected")
    usable = [c for c, v in clients.items() if v.get("selected") and v.get("support") in ("primary", "verified")]
    if usable:
        return CheckResult("agent.clients", "pass", "supported client selected: " + ", ".join(sorted(usable)))
    return CheckResult("agent.clients", "warn", "no selected client is primary/verified",
                       "agent.clients.unsupported")


CHECKS: Sequence[Callable[[Mapping], CheckResult]] = (
    check_td_build, check_py_version, check_py_deps, check_net_capability, check_net_bind,
    check_net_port, check_datadir, check_instances, check_agent_clients,
)


def run_checks(facts: Mapping) -> List[CheckResult]:
    return [c(facts) for c in CHECKS]


def summary(results: Sequence[CheckResult]) -> str:
    counts = {r: sum(1 for x in results if x.result == r) for r in RESULTS}
    return " · ".join(f"{counts[r]} {r}" for r in RESULTS if counts[r] or r in ("pass", "fail"))


def listener_allowed(results: Sequence[CheckResult]) -> bool:
    """Step 0 of the bind gate: may the listener even be activated?"""
    by = {r.id: r for r in results}
    return by.get("net.capability", CheckResult("net.capability", "unknown", "")).result == "pass" and \
        by.get("net.port", CheckResult("net.port", "unknown", "")).result in ("pass",)


def mcp_enable_allowed(results: Sequence[CheckResult], has_enabled_agent: bool,
                       security_selftests_pass: bool) -> Tuple[bool, List[str]]:
    """Preconditions of setup-assistant §4. Older builds get no override."""
    by: Dict[str, CheckResult] = {r.id: r for r in results}
    blockers = []
    if by.get("td.build") is None or by["td.build"].result in ("fail", "unknown"):
        blockers.append("td.build")
    for cid in ("net.capability", "net.bind", "net.port", "fs.datadir"):
        if by.get(cid) is None or by[cid].result != "pass":
            blockers.append(cid)
    if not security_selftests_pass:
        blockers.append("sec.selftest")
    if not has_enabled_agent:
        blockers.append("agent.token")
    return (not blockers), blockers
