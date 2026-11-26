"""First-run acceptance for the one-click connection (owner D22, ADR-0015). Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_native_tests.py [--tox build/TDflowMCP.tox] [--no-live]

Drop the .tox -> Check Ready -> Connect Agent -> Approve -> Start Working, in a disposable project, against a
**disposable client home** (``TDFLOW_CLIENT_HOME``) that stands in for the user's profile: a ``.claude.json`` with
another MCP server and unrelated settings, and an installed Claude Code VS Code extension folder. The real user's
Claude Code configuration is never read or written.

Real-client validation (not simulated): the configuration TDflowMCP wrote is used, unmodified, by
- the Claude Code CLI on PATH, with ``CLAUDE_CONFIG_DIR`` pointing at the client home (that file is then exactly its
  user-scope configuration): ``claude mcp list`` connects through the headersHelper;
- the binary bundled in the installed Claude Code VS Code extension (the engine of the extension), the same way;
- a live ``claude -p`` session given the very same ``tdflow`` entry (``--mcp-config``, ``--strict-mcp-config``,
  restricted tools): it inspects, creates an operator in the authorized network and is refused outside it. No token
  is put in its environment: the helper fetches the credential.
Then: Suspend / resume and save (launch 1). rc4 (the owner's rc3 restart finding), launches 2-4:
- **restart 1:** a fresh TouchDesigner process opens the saved project. Before anything is loaded, it shows MCP off,
  "Turn on Enable MCP.", agents "not loaded" (never "0 agents"); no file was created or changed (snapshot). Enable MCP
  alone loads the same agent; Claude Code reconnects with the same credential; a live session creates, connects,
  updates and undoes; no duplicate agent or token; the audit chain continues. Save, clean exit;
- **restart 2** after that successful reconnection: the same, then the harness ends TouchDesigner abruptly;
- **recovery** after the abrupt end: identity, agent, credential and audit chain intact, reconnect; then revocation,
  repair, a failed setup and Disconnect (rollback).
Results: build/td-native-test-results.json (no secrets).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_onboard_tests as ob  # noqa: E402
import run_td_mcp_tests as mt  # noqa: E402

g, base, T, H = ob.g, ob.base, ob.T, ob.H
ROOT = base.ROOT
STATE = {}
ORIGINAL = {"numStartups": 12, "theme": "dark", "tipsHistory": {"x": 3},
            "mcpServers": {"other-server": {"type": "http", "url": "http://127.0.0.1:9/mcp"}},
            "projects": {"F:/elsewhere": {"allowedTools": []}}}
ALLOWED = ["mcp__tdflow__tdflow_whoami", "mcp__tdflow__tdflow_list_operators", "mcp__tdflow__tdflow_create_operator",
           "mcp__tdflow__tdflow_set_parameters", "mcp__tdflow__tdflow_get_parameters", "mcp__tdflow__tdflow_connect",
           "mcp__tdflow__tdflow_undo", "mcp__tdflow__tdflow_get_dat_text", "mcp__tdflow__tdflow_list_operator_types"]
PROMPT2 = ("You are connected to a TouchDesigner project through the MCP server named tdflow. 1) Call tdflow_whoami. "
           "2) With tdflow_create_operator create a Level TOP named probe_level inside /project1/fx. 3) With "
           "tdflow_connect connect the output of /project1/fx/noise1 to the first input of /project1/fx/probe_level. "
           "4) Set parameter opacity of /project1/fx/probe_level to 0.5 with tdflow_set_parameters, then read it back "
           "with tdflow_get_parameters. 5) Call tdflow_undo once to undo that last change, then read opacity again. "
           "6) Use tdflow_get_dat_text on /project1/fx/notes with proposed_text set to 'def invalid(:'. "
           "Read the code field in python_validation. 7) Call tdflow_list_operator_types for path /project1/fx, "
           "family TOP, query 'procedural noise', and read the matching type. "
           "Finish with exactly one line: RESULT role=<role from whoami> set=<value read in step 4> "
           "after_undo=<value read in step 5> preview=<validation code from step 6> catalog=<type from step 7>")
PROMPT = ("You are connected to a TouchDesigner project through the MCP server named tdflow. 1) Call tdflow_whoami. "
          "2) Call tdflow_list_operators for /project1/fx. 3) With tdflow_create_operator create a Constant TOP named "
          "probe1 inside /project1/fx. 4) With tdflow_create_operator try to create a Constant TOP named probe_outside "
          "directly inside /project1 (outside your allowed scope); if it is refused, do not retry or work around it. "
          "Finish with exactly one line: RESULT role=<role from whoami> created=<yes|no> outside=<denied|allowed>")


def home():
    return STATE["home"]


def cfg_path():
    return os.path.join(home(), ".claude.json")


def entry():
    with open(cfg_path(), encoding="utf-8") as f:
        return (json.load(f).get("mcpServers") or {}).get("tdflow")


def helper_token():
    """Run the configured headersHelper exactly as Claude Code does on Windows (cmd.exe): the harness then holds the
    token for direct checks, as any process of this user could."""
    e = entry() or {}
    r = subprocess.run('cmd.exe /d /s /c "' + e.get("headersHelper", "") + '"', capture_output=True, text=True,
                       timeout=60, cwd=home())
    try:
        hdr = json.loads(r.stdout.strip() or "{}")
    except ValueError:
        hdr = {}
    auth = hdr.get("Authorization", "")
    return auth[len("Bearer "):] if auth.startswith("Bearer ") else ""


def mcp_list(exe, label):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=home())
    env.pop("TDFLOW_TOKEN", None)
    r = subprocess.run([exe, "mcp", "list"], capture_output=True, timeout=180, cwd=STATE["proj"], env=env)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    line = next((x for x in out.splitlines() if x.startswith("tdflow:")), "")
    STATE.setdefault("mcp_list", []).append({"client": label, "line": line.strip()[:160]})
    return line


def vscode_engine():
    hits = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".vscode", "extensions", "anthropic.claude-code-*",
                                         "resources", "native-binary", "claude.exe")))
    return hits[-1] if hits else None


def version(exe):
    return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()


def snapshot():
    """Every file TDflowMCP or the client could touch: private root, project data folder, client home, the .toe."""
    out = {}
    roots = [STATE["private"], os.path.join(STATE["proj"], ".tdflowmcp"), STATE["home"]]
    for r in roots:
        for d, dirs, files in os.walk(r):
            out[d] = ("dir", len(dirs) + len(files))              # a new or removed entry changes this too
            for f in files:
                fp = os.path.join(d, f)
                try:
                    st = os.stat(fp)
                    out[fp] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
    if STATE.get("toe") and os.path.exists(STATE["toe"]):
        st = os.stat(STATE["toe"])
        out[STATE["toe"]] = (st.st_size, st.st_mtime_ns)
    return out


def audit_events():
    """Event count of the project's audit database, read from a copy (TouchDesigner has not opened it yet)."""
    import sqlite3
    dd = os.path.join(STATE["proj"], ".tdflowmcp")
    for d, _, files in os.walk(dd):
        if "tdflowmcp.sqlite" in files:
            tmp = tempfile.mkdtemp(dir=STATE["top"])
            for suf in ("", "-wal", "-shm"):
                if os.path.exists(os.path.join(d, "tdflowmcp.sqlite" + suf)):
                    shutil.copyfile(os.path.join(d, "tdflowmcp.sqlite" + suf),
                                    os.path.join(tmp, "tdflowmcp.sqlite" + suf))
            c = sqlite3.connect(os.path.join(tmp, "tdflowmcp.sqlite"))
            n = c.execute("select count(*) from events").fetchone()[0]
            c.close()
            shutil.rmtree(tmp, ignore_errors=True)
            return n
    return 0


def make_inspected(tag):
    def cp(data, pid):
        now = snapshot()
        before = STATE["snap_before"]
        new = sorted(os.path.relpath(f, STATE["top"]) for f in set(now) - set(before))
        changed = sorted(os.path.relpath(f, STATE["top"]) for f in set(now) & set(before) if now[f] != before[f])
        T(f"{tag}.REOPENING_CREATED_OR_CHANGED_NOTHING", not new and not changed, new=new[:5], changed=changed[:5])
        vf = os.path.join(STATE["private"], "connect", "claude-code", STATE["pid"] + ".dpapi")
        T(f"{tag}.CREDENTIAL_AND_STORE_STILL_THERE", os.path.isfile(vf) and os.path.isfile(
            os.path.join(STATE["private"], "projects", STATE["pid"], "credentials.sqlite")))
        n = audit_events()
        T(f"{tag}.audit_events_present", n > 0, events=n)
        if tag == "recover":                                  # review 20 (5): nothing durable was lost in the kill
            T("recover.NO_AUDIT_EVENT_LOST_IN_THE_KILL", n >= (STATE.get("events_before_kill") or 1),
              events=n, before_kill=STATE.get("events_before_kill"))
        try:
            with open(os.path.join(STATE["private"], "projects", STATE["pid"], "summary.json"), encoding="utf-8") as f:
                summ = json.load(f)
        except (OSError, ValueError):
            summ = {}
        T(f"{tag}.SUMMARY_KNOWS_THE_AGENT", summ.get("agents_enabled") == 1 and summ.get("clients") == ["claude-code"]
          and not any(k in json.dumps(summ) for k in ("tdf_", "Bearer")), summary=summ)
        return {"pid": STATE["pid"], "aid": STATE["aid"], "agents": STATE["agents"], "events": n}
    return cp


def make_live(tag):
    def cp(data, pid):
        T(f"{tag}.SAME_CREDENTIAL_REUSED", helper_token() == STATE.get("token"))
        if STATE["live"]:
            line = mcp_list(shutil.which("claude"), f"cli-{tag}")
            T(f"{tag}.CLAUDE_CODE_RECONNECTS_WITHOUT_SETUP", "Connected" in line and "Failed" not in line, line=line)
        else:
            T(f"{tag}.helper_token_works", mt.ok(mt.MCP(STATE["token"]).call("tdflow_whoami")))
        out = {}
        if STATE["live"] and data.get("live"):
            out = live_session(PROMPT2, f"{tag}-session")
            rl = out.get("result_line", "")
            T(f"{tag}.LIVE_SESSION_AFTER_RESTART", out.get("rc") == 0 and "role=builder" in rl and "set=0.5" in rl
              and re.search(r"after_undo=1(\.0+)?\b", rl) is not None, line=rl)
            T(f"{tag}.LIVE_SESSION_PREVIEWS_INVALID_PYTHON", "preview=PYTHON_SYNTAX" in rl, line=rl)
            T(f"{tag}.LIVE_SESSION_FINDS_LOCAL_OPERATOR_TYPE", "catalog=noiseTOP" in rl, line=rl)
        return out
    return cp


def live_session(prompt, label):
    e = entry()
    mcfg = os.path.join(STATE["top"], f"tdflow-entry-{label}.json")
    with open(mcfg, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"tdflow": e}}, f)
    env = dict(os.environ)
    env.pop("TDFLOW_TOKEN", None)
    t0 = time.time()
    rc, sout, serr = ob.run_claude(shutil.which("claude"), ["-p", prompt, "--mcp-config", mcfg, "--strict-mcp-config",
                                                             "--allowedTools", *ALLOWED, *ob.ISOLATION,
                                                             "--output-format", "stream-json", "--verbose"],
                                   STATE["proj"], env, 600)
    res, preview_calls, catalog_calls = {}, [], []
    for ln in sout.splitlines():
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if m.get("type") == "assistant":
            for item in (m.get("message") or {}).get("content") or []:
                if item.get("type") == "tool_use" and item.get("name") == "mcp__tdflow__tdflow_get_dat_text":
                    preview_calls.append(item.get("input"))
                if item.get("type") == "tool_use" and item.get("name") == "mcp__tdflow__tdflow_list_operator_types":
                    catalog_calls.append(item.get("input"))
        if m.get("type") == "result":
            res = m
    text = str(res.get("result", ""))
    rl = next((x for x in text.splitlines() if x.strip().startswith("RESULT")), "")
    secret = (STATE.get("token") or "x.\0").split(".", 1)[-1]
    T(f"{label}.no_token_in_agent_output", secret not in sout and secret not in serr)
    if prompt == PROMPT2:
        T(f"{label}.MCP_PREVIEW_CALLED", {"path": "/project1/fx/notes", "proposed_text": "def invalid(:"} in preview_calls)
        T(f"{label}.MCP_CATALOG_CALLED", any(c.get("path") == "/project1/fx" and c.get("family") == "TOP"
                                             and c.get("query") == "procedural noise" for c in catalog_calls))
    return {"rc": rc, "result_line": rl, "seconds": round(time.time() - t0, 1), "turns": res.get("num_turns")}


def cp_restart2_kill(data, pid):
    """An abrupt end: no save, no clean shutdown (the harness kills ITS OWN TouchDesigner process)."""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    g.STATE["killed_pid"] = pid
    STATE["events_before_kill"] = data.get("events")
    time.sleep(1.0)
    return {}


def cp_connect_live(data, pid):
    STATE["proj"] = data["project_folder"]
    STATE["pid"] = data.get("pid")
    e = entry()
    T("live.config_written_by_tdflowmcp_is_used_unmodified", bool(e) and "headersHelper" in e)
    tok = helper_token()
    STATE["token"] = tok
    T("live.HELPER_FETCHES_THIS_PROJECTS_CREDENTIAL", tok.startswith("tdf_"))
    secret = tok.split(".", 1)[-1] if tok else "\0"
    out = {"denied_by_server": False}
    if not STATE["live"]:
        return out
    exe = shutil.which("claude")
    out["cli_version"] = version(exe)
    line = mcp_list(exe, "cli")
    T("live.CLAUDE_CODE_CLI_CONNECTS_FROM_USER_SCOPE", "Connected" in line and "Failed" not in line, line=line)
    vx = vscode_engine()
    if vx:
        out["vscode_engine_version"] = version(vx)
        line = mcp_list(vx, "vscode-extension-engine")
        T("live.VSCODE_EXTENSION_ENGINE_CONNECTS_FROM_USER_SCOPE", "Connected" in line and "Failed" not in line, line=line)
    else:
        T("live.vscode_extension_engine_present", False)
    mcfg = os.path.join(STATE["top"], "tdflow-entry.json")
    with open(mcfg, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"tdflow": e}}, f)
    env = dict(os.environ)
    env.pop("TDFLOW_TOKEN", None)
    t0 = time.time()
    rc, sout, serr = ob.run_claude(exe, ["-p", PROMPT, "--mcp-config", mcfg, "--strict-mcp-config", "--allowedTools",
                                         *ALLOWED, *ob.ISOLATION, "--output-format", "stream-json", "--verbose"],
                                   data["project_folder"], env, 600)
    T("live.agent_tools_isolated", ob.isolated_ok(ob.init_tools(sout)))
    res = {}
    for ln in sout.splitlines():
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if m.get("type") == "result":
            res = m
    text = str(res.get("result", ""))
    rl = next((x for x in text.splitlines() if x.strip().startswith("RESULT")), "")
    out.update(seconds=round(time.time() - t0, 1), rc=rc, result_line=rl, turns=res.get("num_turns"))
    T("live.LIVE_SESSION_WORKS_WITHOUT_ANY_TOKEN_STEP", rc == 0 and "role=builder" in rl and "created=yes" in rl, line=rl,
      stderr=serr[-400:])
    T("live.PERMISSION_DENIED_OUTSIDE_SCOPE", "outside=denied" in rl, line=rl)
    out["denied_by_server"] = "outside=denied" in rl
    T("live.NO_TOKEN_IN_AGENT_OUTPUT", secret not in sout and secret not in serr)
    with open(cfg_path(), encoding="utf-8") as f:
        T("live.NO_TOKEN_IN_THE_CLIENT_CONFIG", secret not in f.read())
    T("live.no_token_in_process_environment_used", "TDFLOW_TOKEN" not in env)
    return out


PROMPT_TOOLS = ALLOWED + ["mcp__tdflow__tdflow_project_info", "mcp__tdflow__tdflow_describe_network",
                          "mcp__tdflow__tdflow_get_operator", "mcp__tdflow__tdflow_get_errors",
                          "mcp__tdflow__tdflow_task_list", "mcp__tdflow__tdflow_handoff_resume",
                          "mcp__tdflow__tdflow_set_dat_text", "mcp__tdflow__tdflow_delete_operator",
                          "mcp__tdflow__tdflow_capabilities"]
CAPS_QUESTION = ("What can you do in my TouchDesigner project through TDflowMCP, and what can't you do? Where can I see "
                 "and change your permissions? Answer briefly. Do not change anything.")
CHANGE_TOOLS = {"tdflow_set_parameters", "tdflow_create_operator", "tdflow_delete_operator", "tdflow_connect",
                "tdflow_disconnect", "tdflow_set_dat_text", "tdflow_undo", "tdflow_execute_script",
                "tdflow_experiment_create", "tdflow_experiment_promote", "tdflow_experiment_discard",
                "tdflow_save_project", "tdflow_task_checkpoint", "tdflow_handoff_create", "tdflow_lease_scope"}


def cp_agent_prompt(data, pid):
    """D24 success criterion, with a real Claude Code session: the text Copy Agent Prompt put on the clipboard is the
    whole first message. Change tools are allowed on purpose: the agent must choose not to use them until it gets a
    task. Nothing else is added to the prompt."""
    prompt = data.get("prompt", "")
    secret = (STATE.get("token") or "x.\0").split(".", 1)[-1]
    T("prompt.COPIED_PROMPT_HAS_NO_TOKEN", bool(prompt) and secret not in prompt and "tdf_" not in prompt)
    STATE["prompt"] = prompt
    if not STATE["live"]:
        return {"live": False}
    e = entry()
    mcfg = os.path.join(STATE["top"], "tdflow-entry-prompt.json")
    with open(mcfg, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"tdflow": e}}, f)
    env = dict(os.environ)
    env.pop("TDFLOW_TOKEN", None)
    t0 = time.time()
    rc, sout, serr = ob.run_claude(shutil.which("claude"), ["-p", prompt, "--mcp-config", mcfg, "--strict-mcp-config",
                                                             "--allowedTools", *PROMPT_TOOLS, *ob.ISOLATION,
                                                             "--output-format", "stream-json", "--verbose"],
                                   STATE["proj"], env, 600)
    calls, res = [], {}
    for ln in sout.splitlines():
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if m.get("type") == "assistant":
            for c in (m.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    calls.append(str(c.get("name", "")).replace("mcp__tdflow__", ""))
        elif m.get("type") == "result":
            res = m
    text = str(res.get("result", ""))
    td_calls = [c for c in calls if c.startswith("tdflow_")]
    T("prompt.LIVE_SESSION_FROM_THE_COPIED_PROMPT", rc == 0 and bool(text), rc=rc, stderr=serr[-300:])
    T("prompt.AGENT_TOOLS_ISOLATED", ob.isolated_ok(ob.init_tools(sout)))
    T("prompt.FIRST_CALL_IS_WHOAMI", td_calls[:1] == ["tdflow_whoami"], calls=td_calls)
    T("prompt.AGENT_INSPECTED_THE_PROJECT", any(c in td_calls for c in (
        "tdflow_project_info", "tdflow_describe_network", "tdflow_list_operators")), calls=td_calls)
    T("prompt.AGENT_CALLED_NO_CHANGE_TOOL", not [c for c in td_calls if c in CHANGE_TOOLS], calls=td_calls)
    T("prompt.AGENT_GAVE_AN_OVERVIEW", "fx" in text and "builder" in text.lower(), text=text[:600])
    T("prompt.NO_TOKEN_IN_AGENT_OUTPUT", secret not in sout and secret not in serr)
    out = {"live": True, "rc": rc, "calls": td_calls, "seconds": round(time.time() - t0, 1),
           "turns": res.get("num_turns"), "answer": text[:1500]}
    # Owner D25: asked what it can do, the agent reports its own effective capabilities (not TDflowMCP's feature list)
    # and gives the dashboard's Capabilities page address.
    t1 = time.time()
    rc2, sout2, serr2 = ob.run_claude(shutil.which("claude"), ["-p", CAPS_QUESTION, "--mcp-config", mcfg,
                                                               "--strict-mcp-config", "--allowedTools", *PROMPT_TOOLS,
                                                               *ob.ISOLATION, "--output-format", "stream-json",
                                                               "--verbose"], STATE["proj"], env, 600)
    calls2, res2 = [], {}
    for ln in sout2.splitlines():
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if m.get("type") == "assistant":
            for c in (m.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    calls2.append(str(c.get("name", "")).replace("mcp__tdflow__", ""))
        elif m.get("type") == "result":
            res2 = m
    text2 = str(res2.get("result", ""))
    td2 = [c for c in calls2 if c.startswith("tdflow_")]
    low = text2.lower()
    T("caps.LIVE_AGENT_ANSWERS", rc2 == 0 and bool(text2), rc=rc2, stderr=serr2[-300:])
    T("caps.AGENT_CALLED_TDFLOW_CAPABILITIES", "tdflow_capabilities" in td2, calls=td2)
    T("caps.AGENT_POINTED_TO_THE_CAPABILITIES_PAGE", "Capabilities" in text2 and ("Open Dashboard" in text2
                                                                                 or "/ui/#capabilities" in text2),
      text=text2[:800])
    T("caps.AGENT_REPORTED_ITS_ROLE_AND_LIMITS", "builder" in low and any(
        w in low for w in ("cannot", "can't", "not available", "denied", "not allowed", "needs your approval",
                           "approval")), text=text2[:800])
    T("caps.AGENT_CHANGED_NOTHING", not [c for c in td2 if c in CHANGE_TOOLS], calls=td2)
    T("caps.NO_TOKEN_IN_AGENT_OUTPUT", secret not in sout2 and secret not in serr2)
    out["capabilities"] = {"rc": rc2, "calls": td2, "seconds": round(time.time() - t1, 1),
                           "turns": res2.get("num_turns"), "answer": text2[:1500]}
    return out


def cp_connect_suspended(data, pid):
    r = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("native.SUSPENDED_REFUSES_THE_AGENT", r.get("code") == "SUSPENDED" or r.get("_http") in (401, 503), r=r)
    return {}


def cp_connect_resumed(data, pid):
    T("native.RESUMED_AGENT_WORKS", mt.ok(mt.MCP(STATE["token"]).call("tdflow_whoami")))
    return {}


def cp_restart_revoked(data, pid):
    r = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("revoke.REVOKED_CREDENTIAL_REFUSED", r.get("_http") == 401 or r.get("code") in ("UNAUTHORIZED", "AUTH"), r=r)
    if STATE["live"]:
        line = mcp_list(shutil.which("claude"), "cli-revoked")
        T("revoke.CLIENT_REPORTS_FAILURE", "Connected" not in line, line=line)
    return {}


def cp_restart_repaired(data, pid):
    tok = helper_token()
    T("repair.NEW_CREDENTIAL_IN_THE_STORE", tok.startswith("tdf_") and tok != STATE.get("token"))
    STATE["token2"] = tok
    if STATE["live"]:
        line = mcp_list(shutil.which("claude"), "cli-repaired")
        T("repair.CLAUDE_CODE_CONNECTS_AGAIN", "Connected" in line and "Failed" not in line, line=line)
    else:
        T("repair.new_token_works", mt.ok(mt.MCP(tok).call("tdflow_whoami")))
    return {}


def cp_restart_disconnected_summary():
    path = os.path.join(STATE["private"], "projects", STATE["pid"], "summary.json")
    summ = {}
    for _ in range(20):                                       # written on a background thread after the change
        try:
            with open(path, encoding="utf-8") as f:
                summ = json.load(f)
            if summ.get("agents_enabled") == 0:
                return summ
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    return summ


def cp_restart_disconnected(data, pid):
    summ = cp_restart_disconnected_summary()
    T("disconnect.SUMMARY_SAYS_NO_AGENT", summ.get("agents_enabled") == 0 and summ.get("clients") == [], summary=summ)
    with open(cfg_path(), "rb") as f:
        now = f.read()
    if STATE["live"]:      # the real client rewrote its own state meanwhile (as it does); TD checks the exact removal
        cur = json.loads(now.decode("utf-8"))
        T("disconnect.OTHER_SERVER_AND_SETTINGS_KEPT", cur["mcpServers"] == ORIGINAL["mcpServers"]
          and cur["projects"] == ORIGINAL["projects"] and cur["numStartups"] == ORIGINAL["numStartups"])
    else:
        T("disconnect.CONFIG_BYTE_IDENTICAL_TO_BEFORE_THE_CONNECTION", now == STATE["original_bytes"])
    T("disconnect.helper_entry_gone", entry() is None)
    r = mt.MCP(STATE.get("token2", "")).call("tdflow_whoami")
    T("disconnect.CREDENTIAL_NO_LONGER_WORKS", r.get("_http") == 401 or not mt.ok(r), r=r)
    if STATE["live"]:
        line = mcp_list(shutil.which("claude"), "cli-disconnected")
        T("disconnect.CLIENT_NO_LONGER_HAS_TDFLOW", line == "", line=line)
    return {}


HANDLERS = {"connect_live": cp_connect_live, "agent_prompt": cp_agent_prompt, "connect_suspended": cp_connect_suspended,
            "connect_resumed": cp_connect_resumed}
RESTART = {"restart_inspected": make_inspected("restart"), "restart_live": make_live("restart")}
RESTART2 = {"restart2_inspected": make_inspected("restart2"), "restart2_live": make_live("restart2"),
            "restart2_kill": cp_restart2_kill}
RECOVER = {"recover_inspected": make_inspected("recover"), "recover_live": make_live("recover"),
           "restart_revoked": cp_restart_revoked, "restart_repaired": cp_restart_repaired,
           "restart_disconnected": cp_restart_disconnected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-native-test-results.json"))
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-native-tests"), ROOT)
    if os.path.exists(top):
        shutil.rmtree(top)
    os.makedirs(top)
    hm = os.path.join(top, "client-home")
    os.makedirs(os.path.join(hm, ".vscode", "extensions", "anthropic.claude-code-2.1.330-win32-x64"))
    raw = json.dumps(ORIGINAL, indent=2).encode("utf-8")
    with open(os.path.join(hm, ".claude.json"), "wb") as f:
        f.write(raw)
    STATE.update(top=top, live=not a.no_live, home=hm, original_bytes=raw, private=os.path.join(top, "private"))
    proj = tempfile.mkdtemp(prefix="tdflow-native-project-")       # outside the repository
    STATE["proj"] = proj
    run_dir = os.path.join(top, "native")
    os.makedirs(run_dir)
    tox = os.path.abspath(a.tox)
    env = {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private"), "TDFLOW_CLIENT_HOME": hm}
    res = g.launch("native", tox, run_dir, proj, env, HANDLERS)
    toe = res.get("native_toe")
    T("restart.project_saved", bool(toe) and os.path.exists(toe))
    res2, res3, res4 = {}, {}, {}
    STATE["aid"] = (res.get("connect_record") or {}).get("agent_id")
    STATE["agents"] = 1
    STATE["toe"] = toe
    if toe and os.path.exists(toe):
        secret = STATE.get("token", "").split(".", 1)[-1].encode()
        T("restart.NO_TOKEN_IN_THE_SAVED_PROJECT", bool(secret) and secret not in open(toe, "rb").read())
        for mode, handlers, kill, sub in (("native_restart", RESTART, False, "restart"),
                                          ("native_restart2", RESTART2, True, "restart2"),
                                          ("native_recover", RECOVER, False, "recover")):
            os.makedirs(os.path.join(top, sub))
            STATE["snap_before"] = snapshot()
            r = g.launch(mode, tox, os.path.join(top, sub), proj, env, handlers, deadline_s=700, toe=toe,
                         expect_kill=kill)
            if mode == "native_restart":
                res2 = r
            elif mode == "native_restart2":
                res3 = r
            else:
                res4 = r
    shutil.rmtree(proj, ignore_errors=True)
    for path in glob.glob(os.path.join(top, "**", "*"), recursive=True):    # no plaintext token anywhere we wrote
        if os.path.isfile(path) and os.path.getsize(path) < 50_000_000:
            with open(path, "rb") as f:
                blob = f.read()
            for t in (STATE.get("token"), STATE.get("token2")):
                if t and t.split(".", 1)[-1].encode() in blob:
                    T("leak.NO_PLAINTEXT_TOKEN_IN_ANY_FILE", False, file=os.path.relpath(path, top))
    if not [t for t in H["tests"] if t["name"] == "leak.NO_PLAINTEXT_TOKEN_IN_ANY_FILE"]:
        T("leak.NO_PLAINTEXT_TOKEN_IN_ANY_FILE", True)
    in_td = sum(((x.get("tests") or []) for x in (res, res2, res3, res4)), [])
    errs = sum(((x.get("errors") or []) for x in (res, res2, res3, res4)), [])
    out = {"tox_sha256": g.hashlib.sha256(open(tox, "rb").read()).hexdigest(), "td_build": res.get("td_build"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]},
           "live": res.get("native"), "mcp_list": STATE.get("mcp_list"), "agent_prompt": res.get("agent_prompt"),
           "copied_prompt": STATE.get("prompt"),
           "several_detected": res.get("several_detected"), "connect_record": res.get("connect_record")}
    out["result"] = "pass" if (in_td and out["in_td"]["passed"] == out["in_td"]["total"] and not errs
                               and out["harness"]["passed"] == out["harness"]["total"]) else "fail"
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps({"result": out["result"], "in_td": f"{out['in_td']['passed']}/{out['in_td']['total']}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}",
                      "failed": [t["name"] for t in in_td + H["tests"] if not t["ok"]], "errors": errs[:2],
                      "live": out["live"], "mcp_list": out["mcp_list"]}, indent=1))


if __name__ == "__main__":
    main()
