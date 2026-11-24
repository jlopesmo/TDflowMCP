"""'Tell Your Coding Agent' text (setup-assistant §6): Setup + Repair sections for Phase 1A.

Generated from allowlisted fields only: component version, TouchDesigner build, controller state and
error code, check ids/results, and the static troubleshooting text. Never included: tokens, check
evidence strings (they may hold environment detail), operator names, paths, addresses or raw errors.
"""
from __future__ import annotations

import json
from typing import Mapping, Sequence

from tdflowmcp import __version__
from tdflowmcp.compat import CheckResult, summary

RULES = ("Do not install software, change TouchDesigner, operating-system, firewall or coding-agent "
         "configuration, or end any process without asking the user first. Explain each step to the user "
         "and let them perform it. Connecting you is the user's action in TouchDesigner (Connect Agent, then Approve): "
         "TDflowMCP then makes exactly the configuration change the user approved. That is authorized; changing "
         "configuration silently, or editing TDflowMCP's entry yourself, is not.")


def _state_sentence(state: str, error_code: str, port: int) -> str:
    if state in ("Enabled", "Suspended"):
        return (f"{state}. MCP is served at http://127.0.0.1:{port}/mcp (this computer only)"
                + (". All agents are currently suspended by the user." if state == "Suspended" else "."))
    if state == "Setup":
        return "Listener on (this computer only); MCP is not on yet."
    if state == "Error":
        return f"Error ({error_code or 'unknown'}). The listener is not running."
    if state in ("Starting", "Stopping"):
        return f"{state}. The listener is changing state; ask the user to wait a moment."
    return "Off (MCP is not on yet)."


SHELLS = (("bash", "bash"), ("zsh", "zsh"), ("powershell", "PowerShell"))


def _fill(obj, url: str):
    """Substitute the endpoint into a registry snippet. Only '{url}' is replaced; ${TDFLOW_TOKEN} stays."""
    if isinstance(obj, str):
        return obj.replace("{url}", url)
    if isinstance(obj, dict):
        return {k: _fill(v, url) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, url) for v in obj]
    return obj


def detect(clients: Mapping, which) -> list:
    """Client ids whose command name is on PATH. ``which`` is ``shutil.which``: it looks names up, it never runs
    them. A hit is a hint, not proof that the client works; nothing about paths is kept."""
    found = []
    for c in (clients.get("clients", []) if isinstance(clients, Mapping) else []):
        if any(which(n) for n in c.get("detect") or []):
            found.append(c["id"])
    return found


def choose(selection: str, detected: Sequence[str], clients: Mapping) -> list:
    """Which clients the instructions cover: the user's explicit choice, else (Auto) the detected ones, else the
    generic instructions for any MCP client."""
    ids = [c["id"] for c in (clients.get("clients", []) if isinstance(clients, Mapping) else [])]
    if selection and selection != "auto" and selection in ids:
        return [selection]
    found = [i for i in detected if i in ids]
    return found or (["other"] if "other" in ids else ids[:1])


def display(clients: Mapping, cid: str) -> str:
    for c in (clients.get("clients", []) if isinstance(clients, Mapping) else []):
        if c["id"] == cid:
            return c.get("display", cid)
    return cid


def _shell(platform: str):
    return ("powershell", "PowerShell") if platform == "win32" else ("zsh", "zsh") if platform == "darwin" else         ("bash", "bash")


def connect_section(port: int, clients: Mapping, selected: Sequence[str] = (), platform: str = "win32",
                    automatic: Sequence[str] = (), set_up: Sequence[str] = ()) -> list:
    """'Connect' section (owner D22): only the selected client(s) and this platform. ``automatic``: client ids with
    consent-based automatic setup on this platform; ``set_up``: clients already connected in this project. Allowlisted
    inputs only; the token never appears (the manual fallback names the ${TDFLOW_TOKEN} placeholder)."""
    url = f"http://127.0.0.1:{int(port)}/mcp"
    reg = clients.get("clients", []) if isinstance(clients, Mapping) else []
    if selected:
        reg = [c for c in reg if c.get("id") in selected] or reg
    env = clients.get("token_env", {}) if isinstance(clients, Mapping) else {}
    key, label = _shell(platform)
    out = []
    for c in reg:
        cid, name = c.get("id"), c.get("display")
        if cid in automatic:
            state = "already set up for this project" if cid in set_up else "not set up yet"
            out += ["", f"## Connect {name} (automatic; {state})",
                    "The user does this in TouchDesigner; there is nothing to type or paste:",
                    "1. TDflowMCP page: Connect Agent, then Approve in the dialog. TDflowMCP adds the user-scope MCP "
                    "server `tdflow` to Claude Code's configuration (shared by the CLI and the VS Code extension). Its "
                    "fixed helper command reads this project's credential from the user's protected store; no token "
                    "is shown, pasted or stored in plain text.",
                    "2. Start a new Claude Code conversation (VS Code: a new chat; CLI: a new session), or run /mcp "
                    "and reconnect `tdflow`.",
                    "3. Call `tdflow_whoami`. TouchDesigner then shows Agent Connected — Ready.",
                    "If `tdflow` is missing or Failed: first ask the user to check that TouchDesigner is open (not "
                    "minimized) and Enable MCP is on (it is off after every restart). Otherwise ask for Repair "
                    "Connection (Agents page). Do not edit the `tdflow` entry yourself."]
            continue
        mcp = _fill(c.get("mcp") or {}, url)
        why = c.get("manual_reason") or "this client cannot fetch a credential by itself"
        out += ["", f"## Connect {name} (manual setup)",
                f"Why manual: {why}. The token is pasted once per terminal.",
                f"1. TouchDesigner: set Coding Agent to {name}, pulse Connect Agent, then Copy Token (Agents page) "
                "within 2 minutes.",
                f"2. In the {label} terminal that starts the agent (never in a file, a commit or the chat):"]
        if env.get(key):
            out.append(f"   `{env[key]}`")
        out.append(f"3. Register the server `tdflow` once ({url}, Streamable HTTP):")
        cmds = mcp.get("add_command") or {}
        if cmds.get(key):
            out.append(f"   `{cmds[key]}`")
        elif mcp.get("toml"):
            out.append("   TOML (one line per `;`): `" + mcp["toml"].replace("\n", "; ") + "`")
        elif mcp.get("json"):
            out.append("   JSON entry: `" + json.dumps(mcp["json"], separators=(", ", ": ")) + "`")
        if mcp.get("config_file_note"):
            out.append("   Where: " + mcp["config_file_note"])
        start = {"cursor": "`cursor .`", "vscode-copilot": "`code .`"}.get(cid, "the agent")
        out.append(f"4. Turn on Enable MCP, start {start} from that terminal, call `tdflow_whoami`.")
    out += ["", "Optional: TDflowMCP Skills install from the Setup page (preview first). `tdflow_whoami` reports "
            "`expected_skill_version`; tell the user if your Skills differ."]
    return out


def build(state: str, error_code: str, port: int, td_build: str, checks: Sequence[CheckResult],
          troubleshooting: Mapping, version: str = __version__, clients: Mapping = None,
          memory_lines: Sequence[str] = (), selected: Sequence[str] = (), access: str = "",
          platform: str = "win32", automatic: Sequence[str] = (), set_up: Sequence[str] = ()) -> str:
    entries = troubleshooting.get("entries", {}) if isinstance(troubleshooting, Mapping) else {}
    problems = [c for c in checks if c.result in ("fail", "warn")]
    lines = [
        "# TDflowMCP: Tell Your Coding Agent (setup and repair)",
        "",
        "Generated by the TDflowMCP component from its current state. It contains no secrets.",
        "",
        "## Setup",
        f"- TDflowMCP component v{version} is installed in a TouchDesigner {td_build or '(unknown build)'} project.",
        f"- State: {_state_sentence(state, error_code, port)}",
        (f"- MCP endpoint: http://127.0.0.1:{port}/mcp (Streamable HTTP, server name `tdflow`). Call tdflow_whoami "
         "first to see your scopes; every change is audited."
         if state in ("Enabled", "Suspended") else
         "- MCP is not on yet. The user connects you from TouchDesigner: Connect Agent, then Approve (see below)."),
        f"- Compatibility checks: {summary(checks) if checks else 'not run yet (pulse Run Diagnostics)'}.",
        # Spike S9: TouchDesigner runs no frames while its window is minimized.
        "- The TouchDesigner window must stay open (it may be behind other windows, but not minimized): "
        "TouchDesigner pauses while minimized, and calls then fail with TDFLOW_NOT_COOKING until the user restores it.",
    ]
    if access:
        lines.append(f"- Access the user chose for new agents: {access}. Your own permissions are what tdflow_whoami "
                     "reports; the server enforces them.")
    if clients:
        lines += connect_section(port, clients, selected, platform, automatic, set_up)
    if memory_lines:                               # Phase 2A: built from validated memory fields only
        lines += [""] + list(memory_lines)
    lines += [
        "",
        "## Repair",
        f"Rules: {RULES}",
    ]
    if error_code and error_code in {"PORT_IN_USE", "BIND_UNVERIFIED", "CLOSE_UNCONFIRMED", "TRANSPORT_FAILED"}:
        lines.append(f"- Listener error {error_code}: see the matching check below, or ask the user to pulse "
                     "Enable MCP (or Connect Agent) once more. TDflowMCP never restarts the listener by itself.")
    if not problems:
        lines.append("- No repair needed: no check reports a warning or failure.")
    for c in problems:
        e = entries.get(c.remediation_id, {})
        lines.append(f"- [{c.result}] {c.id}: {e.get('title', c.remediation_id)}")
        if e.get("cause"):
            lines.append(f"  Cause: {e['cause']}")
        if e.get("confirm"):
            lines.append(f"  Confirm: {e['confirm']}")
        for i, step in enumerate(e.get("steps", []), 1):
            lines.append(f"  Step {i}: {step}")
    unknown = [c.id for c in checks if c.result == "unknown"]
    if unknown:
        lines.append("- Not yet verified (unknown): " + ", ".join(unknown) + ".")
    return "\n".join(lines) + "\n"


def one_line(state: str, error_code: str, checks: Sequence[CheckResult], version: str = __version__) -> str:
    n = sum(1 for c in checks if c.result in ("fail", "warn"))
    if state == "Error" or n:
        return f"Repair: {n} check(s) need attention{' · ' + error_code if error_code else ''} · v{version}"
    if not checks:
        return f"Not checked yet · v{version}"
    return f"Ready · v{version}"
