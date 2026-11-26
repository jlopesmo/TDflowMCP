"""One-click Codex connection, with real Codex engines (ADR-0017, Issue #3). Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_codex_tests.py [--tox build/TDflowMCP.tox] [--no-live]

Coding Agent: Codex -> Connect Agent -> Approve -> a Codex engine connects, in a disposable project, against a
**disposable client home** (``TDFLOW_CLIENT_HOME``) with a realistic ``.codex/config.toml`` (comments, other MCP
servers, tables Codex writes) and a ``.claude.json``. The user's real Codex and Claude Code configuration, sign-in
and sessions are never used or written: the engines and Claude Code run with the user's environment plus
``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR`` set to the client home. The real ``~/.codex/config.toml`` is only hashed
before and after the run, to prove that it did not change.

Observed with real clients (not simulated), using the configuration TDflowMCP wrote, unmodified:
- the engine bundled in the installed Codex VS Code extension (``codex app-server``, the process the extension runs)
  and the Codex CLI's engine, each with ``CODEX_HOME`` = the client home's ``.codex``: they list the ``tdflow`` tools
  through the header helper, call ``tdflow_whoami`` (the Codex agent, its role and scope), create an operator in
  scope and are refused outside it. No model is called and no Codex sign-in is used;
- Claude Code (CLI, ``CLAUDE_CONFIG_DIR`` = the client home) connected next to it with its own agent and key;
- revoking the Codex key: the running engine's next call fails while Claude Code stays connected; Repair Connection:
  the same running engine works again without a restart (it runs the helper again after the 401);
- launch 2 (a new TouchDesigner process on the saved project): Enable MCP only, a newly started engine reconnects
  with the stored key; damaged and unsupported settings are refused untouched; Disconnect Agent removes exactly the
  table (the file is byte-identical to the original) and leaves Claude Code connected.
Not covered here: the chat window of the VS Code extension itself, and a model choosing to call the tools (the
owner's acceptance step). ``--no-live`` replaces the engines with direct MCP calls (simulated).
Results: build/td-codex-test-results.json (no secrets).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_onboard_tests as ob  # noqa: E402
import run_td_mcp_tests as mt  # noqa: E402
import codex_engine as ce  # noqa: E402

g, base, T, H = ob.g, ob.base, ob.T, ob.H
ROOT = base.ROOT
STATE = {"engines": {}, "seen": []}
CLAUDE_ORIGINAL = {"numStartups": 12, "theme": "dark",
                   "mcpServers": {"other-server": {"type": "http", "url": "http://127.0.0.1:9/mcp"}},
                   "projects": {"F:/elsewhere": {"allowedTools": []}}}
# Shaped like a real Codex configuration. The other servers are disabled so that the engines start nothing else.
CODEX_ORIGINAL = '''# Codex settings (test profile)
model = "gpt-5"   # keep this comment
model_reasoning_effort = "medium"

[features]
web_search = false

# [mcp_servers.tdflow] <- only a comment that looks like a table
[projects.'X:\\Some Folder\\project']
trust_level = "trusted"

[mcp_servers.blender]
command = "tdflowmcp-test-command-that-does-not-exist"
args = ["x"]
enabled = false

[mcp_servers.docs]
url = "http://127.0.0.1:9/mcp"
bearer_token_env_var = "DOCS_TOKEN"
enabled = false

[apps.connector_1.tools."github.add_comment"]
approval_mode = "approve"
'''.encode("utf-8")


def codex_home():
    return os.path.join(STATE["home"], ".codex")


def codex_cfg():
    return os.path.join(codex_home(), "config.toml")


def read(path):
    with open(path, "rb") as f:
        return f.read()


def codex_token():
    """Run the configured helper exactly as Codex does: the harness then holds the token for direct checks, as any
    process of this user could. Returns (exit status, token or '')."""
    entry = (tomllib.loads(read(codex_cfg()).decode("utf-8")).get("mcp_servers") or {}).get("tdflow") or {}
    rc, hdr = ce.run_helper(entry.get("http_headers_helper", "exit 9"), codex_home())
    auth = (hdr or {}).get("Authorization", "")
    return rc, auth[len("Bearer "):] if auth.startswith("Bearer ") else ""


def claude_token():
    e = (json.loads(read(os.path.join(STATE["home"], ".claude.json")).decode("utf-8")).get("mcpServers") or {}).get("tdflow") or {}
    r = subprocess.run('cmd.exe /d /s /c "' + e.get("headersHelper", "") + '"', capture_output=True, text=True,
                       timeout=60, cwd=STATE["home"])
    try:
        auth = json.loads(r.stdout.strip() or "{}").get("Authorization", "")
    except ValueError:
        auth = ""
    return auth[len("Bearer "):] if auth.startswith("Bearer ") else ""


def claude_list(label):
    """``claude mcp list`` with the client home as Claude Code's user configuration; '' when Claude Code is absent."""
    exe = shutil.which("claude")
    if not exe or not STATE["live"]:
        return None
    env = dict(os.environ, CLAUDE_CONFIG_DIR=STATE["home"])
    env.pop("TDFLOW_TOKEN", None)
    r = subprocess.run([exe, "mcp", "list"], capture_output=True, timeout=180, cwd=STATE["proj"], env=env)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    line = next((x for x in out.splitlines() if x.startswith("tdflow:")), "")
    STATE["seen"].append({"client": label, "line": line.strip()[:120]})
    return line


def engines():
    """[(label, path)] of the real Codex engines found on this computer."""
    out = []
    ext, ver = ce.extension_engine()
    if ext:
        out.append(("vscode_extension_engine", ext))
        STATE["extension_version"] = ver
    cli = ce.cli_engine()
    if cli:
        out.append(("cli_engine", cli))
    return out


def engine(label, exe, fresh=False):
    e = STATE["engines"].get(label)
    if e is not None and (fresh or not e.alive()):
        e.close()
        e = None
    if e is None:
        e = ce.Engine(exe, codex_home(), STATE["proj"], label).open()
        STATE["engines"][label] = e
        STATE.setdefault("versions", {})[label] = e.version
        T(f"live.{label}.USES_ONLY_THE_DISPOSABLE_CODEX_HOME",
          os.path.normcase(os.path.abspath(e.codex_home or "")) == os.path.normcase(os.path.abspath(codex_home())),
          home=os.path.basename(os.path.dirname(e.codex_home or "")))
    return e


def whoami_ok(label, e, tag):
    ok, who = e.call("tdflow_whoami")
    scopes = [s.get("scope") for s in (who.get("scopes") or [])] if ok and isinstance(who, dict) else []
    good = ok and who.get("_isError") is False and who.get("agent_id") == STATE["aid"] and who.get("role") == "builder" \
        and scopes == ["/project1/fx/**"]
    T(f"{tag}.{label}.WHOAMI_IS_THE_CODEX_AGENT_WITH_ITS_ROLE_AND_SCOPE", good,
      agent=who.get("agent_id") if isinstance(who, dict) else None, role=who.get("role") if isinstance(who, dict) else None,
      scopes=scopes, error=None if ok else who)
    return good


def no_leak(tag, e):
    secret = (STATE.get("token") or "x.\0").split(".", 1)[-1]
    T(f"{tag}.{e.label}.NO_TOKEN_IN_ENGINE_OUTPUT", secret not in e.output())


def cp_codex_live(data, pid):
    STATE.update(proj=data["project_folder"], pid=data["pid"], aid=data["aid"])
    rc, tok = codex_token()
    T("live.HELPER_RUN_AS_CODEX_RUNS_IT_GETS_THIS_PROJECTS_KEY", rc == 0 and tok.startswith("tdf_"), rc=rc)
    STATE["token"] = tok
    out = {"live": STATE["live"]}
    direct = mt.MCP(tok).call("tdflow_whoami")
    T("live.the_key_belongs_to_the_codex_agent", mt.ok(direct) and direct.get("agent_id") == STATE["aid"])
    if not STATE["live"]:
        return out
    found = engines()
    T("live.A_REAL_CODEX_ENGINE_IS_INSTALLED", bool(found), found=[x[0] for x in found])
    for i, (label, exe) in enumerate(found):
        e = engine(label, exe)
        st = e.servers().get("tdflow") or {}
        tools = sorted((st.get("tools") or {}).keys())
        T(f"live.{label}.LISTS_THE_TDFLOW_TOOLS_THROUGH_THE_WRITTEN_CONFIG", "tdflow_whoami" in tools
          and "tdflow_create_operator" in tools and not st.get("toolsError"), tools=len(tools),
          error=str(st.get("toolsError") or "")[-200:], origin=st.get("httpOrigin"))
        T(f"live.{label}.other_servers_stayed_disabled", all(n in ("tdflow",) or not (r.get("tools") or {})
                                                             for n, r in e.servers().items()))
        whoami_ok(label, e, "live")
        received, preview = e.call("tdflow_get_dat_text", {"path": "/project1/fx/notes", "proposed_text": "def invalid(:"})
        T(f"live.{label}.PYTHON_PREVIEW_REPORTS_SYNTAX", received and preview.get("_isError") is False
          and (preview.get("python_validation") or {}).get("code") == "PYTHON_SYNTAX"
          and preview.get("text") == "hello", r=preview)
        if i == 0:
            ok, made = e.call("tdflow_create_operator", {"parent": "/project1/fx", "type": "constantTOP",
                                                         "name": "codex_probe"})
            T(f"live.{label}.CREATES_AN_OPERATOR_IN_SCOPE", ok and made.get("_isError") is False, r=made)
            ok, no = e.call("tdflow_create_operator", {"parent": "/project1", "type": "constantTOP",
                                                       "name": "codex_outside"})
            T(f"live.{label}.MUTATION_OUTSIDE_THE_SCOPE_IS_DENIED", ok and no.get("_isError") is True
              and no.get("code") == "DENIED", r=no)
            out["denied_by_server"] = bool(ok and no.get("code") == "DENIED")
        no_leak("live", e)
    # `codex mcp list` (the CLI command a user would run) shows the server and no secret.
    cli = dict(found).get("cli_engine") or dict(found).get("vscode_extension_engine")
    env = dict(os.environ, CODEX_HOME=codex_home())
    r = subprocess.run([cli, "mcp", "list"], capture_output=True, text=True, timeout=120, cwd=STATE["proj"], env=env)
    listing = (r.stdout or "") + (r.stderr or "")
    T("live.CODEX_MCP_LIST_SHOWS_TDFLOW_WITHOUT_A_SECRET", "tdflow" in listing and tok.split(".", 1)[-1] not in listing,
      rc=r.returncode)
    T("live.NO_TOKEN_IN_THE_CODEX_CONFIG", tok.split(".", 1)[-1].encode() not in read(codex_cfg()))
    return out


def cp_both_live(data, pid):
    STATE["claude_aid"] = data["claude_aid"]
    ctok = claude_token()
    STATE["claude_token"] = ctok
    c = mt.MCP(ctok).call("tdflow_whoami")
    x = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("both.EACH_CLIENT_HAS_ITS_OWN_KEY_AND_IDENTITY", ctok.startswith("tdf_") and ctok != STATE["token"]
      and mt.ok(c) and mt.ok(x) and c.get("agent_id") == data["claude_aid"] and x.get("agent_id") == data["codex_aid"],
      claude=c.get("agent_id"), codex=x.get("agent_id"))
    line = claude_list("claude-cli-next-to-codex")
    if line is not None:
        T("both.CLAUDE_CODE_CONNECTS_NEXT_TO_CODEX", "Connected" in line and "Failed" not in line, line=line)
    for label, e in STATE["engines"].items():
        whoami_ok(label, e, "both")
    return {}


def cp_codex_revoked(data, pid):
    r = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("revoke.REVOKED_KEY_REFUSED", r.get("_http") == 401 or r.get("code") in ("UNAUTHORIZED", "AUTH"), r=r)
    for label, e in STATE["engines"].items():
        ok, res = e.call("tdflow_whoami")
        T(f"revoke.{label}.THE_RUNNING_ENGINE_IS_REFUSED_WITH_401", not ok and "401" in str(res), result=str(res)[-160:])
    c = mt.MCP(STATE["claude_token"]).call("tdflow_whoami")
    T("revoke.CLAUDE_CODE_KEY_STILL_WORKS", mt.ok(c) and c.get("agent_id") == STATE["claude_aid"])
    line = claude_list("claude-cli-while-codex-revoked")
    if line is not None:
        T("revoke.CLAUDE_CODE_STAYS_CONNECTED", "Connected" in line and "Failed" not in line, line=line)
    return {}


def cp_codex_repaired(data, pid):
    rc, tok = codex_token()
    T("repair.NEW_KEY_IN_THE_STORE", rc == 0 and tok.startswith("tdf_") and tok != STATE["token"])
    STATE["token_old"], STATE["token"] = STATE["token"], tok
    for label, e in STATE["engines"].items():
        T(f"repair.{label}.the_engine_was_not_restarted", e.alive())
        whoami_ok(label, e, "repair")                     # the same process: it ran the helper again after the 401
        no_leak("repair", e)
    c = mt.MCP(STATE["claude_token"]).call("tdflow_whoami")
    T("repair.CLAUDE_CODE_KEY_UNCHANGED", mt.ok(c) and c.get("agent_id") == STATE["claude_aid"])
    if not STATE["engines"]:
        T("repair.new_key_works", mt.ok(mt.MCP(tok).call("tdflow_whoami")))
    return {}


def cp_restart_inspected(data, pid):
    T("restart.NOTHING_WROTE_THE_CODEX_CONFIG_WHILE_CLOSED", read(codex_cfg()) == STATE["codex_connected_bytes"])
    return dict(STATE["ids"])


def cp_restart_live(data, pid):
    rc, tok = codex_token()
    T("restart.SAME_KEY_REUSED", rc == 0 and tok == STATE["token"])
    out = {"live": STATE["live"]}
    if not STATE["live"]:
        T("restart.key_works", mt.ok(mt.MCP(tok).call("tdflow_whoami")))
        return out
    for label, exe in engines():
        e = engine(label, exe, fresh=True)                 # a newly started Codex
        whoami_ok(label, e, "restart")
        no_leak("restart", e)
    line = claude_list("claude-cli-after-restart")
    if line is not None:
        T("restart.CLAUDE_CODE_RECONNECTS_TOO", "Connected" in line and "Failed" not in line, line=line)
    return out


def cp_codex_disconnected(data, pid):
    T("disconnect.CODEX_CONFIG_BYTE_IDENTICAL_TO_BEFORE_THE_CONNECTION", read(codex_cfg()) == CODEX_ORIGINAL)
    r = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("disconnect.CODEX_KEY_REFUSED_WITH_401", r.get("_http") == 401, r=r)
    rc, tok = codex_token()
    T("disconnect.no_helper_left", rc != 0 and tok == "")
    c = mt.MCP(STATE["claude_token"]).call("tdflow_whoami")
    T("disconnect.CLAUDE_CODE_KEY_STILL_WORKS", mt.ok(c) and c.get("agent_id") == STATE["claude_aid"])
    if STATE["live"]:
        for label, exe in engines()[:1]:
            e = engine(label, exe, fresh=True)
            T(f"disconnect.{label}.CODEX_NO_LONGER_HAS_TDFLOW", "tdflow" not in e.servers(), servers=sorted(e.servers()))
        line = claude_list("claude-cli-after-codex-disconnect")
        if line is not None:
            T("disconnect.CLAUDE_CODE_STAYS_CONNECTED", "Connected" in line and "Failed" not in line, line=line)
    return {}


LAUNCH1 = {"codex_live": cp_codex_live, "both_live": cp_both_live, "codex_revoked": cp_codex_revoked,
           "codex_repaired": cp_codex_repaired}
LAUNCH2 = {"codex_restart_inspected": cp_restart_inspected, "codex_restart_live": cp_restart_live,
           "codex_disconnected": cp_codex_disconnected}


def close_engines():
    for e in STATE["engines"].values():
        e.close()
    STATE["engines"].clear()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-codex-test-results.json"))
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-codex-tests"), ROOT)
    for attempt in range(5):                     # a Codex engine of an earlier run may still be letting go of files
        if not os.path.exists(top):
            break
        shutil.rmtree(top, ignore_errors=attempt < 4)
        if os.path.exists(top):
            time.sleep(2)
    os.makedirs(top)
    hm = os.path.join(top, "client-home")
    _, ext_ver = ce.extension_engine()
    os.makedirs(os.path.join(hm, ".vscode", "extensions", f"openai.chatgpt-{ext_ver or '26.1120.50957'}-win32-x64"))
    os.makedirs(os.path.join(hm, ".vscode", "extensions", "anthropic.claude-code-2.1.330-win32-x64"))
    os.makedirs(os.path.join(hm, ".codex"))
    with open(os.path.join(hm, ".codex", "config.toml"), "wb") as f:
        f.write(CODEX_ORIGINAL)
    with open(os.path.join(hm, ".claude.json"), "wb") as f:
        f.write(json.dumps(CLAUDE_ORIGINAL, indent=2).encode("utf-8"))
    real = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    real_before = read(real) if os.path.isfile(real) else None
    STATE.update(top=top, live=not a.no_live, home=hm, private=os.path.join(top, "private"))
    proj = tempfile.mkdtemp(prefix="tdflow-codex-project-")        # outside the repository
    STATE["proj"] = proj
    tox = os.path.abspath(a.tox)
    env = {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private"), "TDFLOW_CLIENT_HOME": hm}
    res, res2 = {}, {}
    try:
        os.makedirs(os.path.join(top, "codex"))
        res = g.launch("codex", tox, os.path.join(top, "codex"), proj, env, LAUNCH1)
        close_engines()                                            # Codex is closed between the two launches
        toe = res.get("codex_toe")
        T("restart.project_saved", bool(toe) and os.path.exists(toe))
        STATE["ids"] = res.get("codex_ids") or {}
        STATE["codex_connected_bytes"] = read(codex_cfg())
        if toe and os.path.exists(toe):
            secret = (STATE.get("token") or "x.\0").split(".", 1)[-1].encode()
            T("restart.NO_TOKEN_IN_THE_SAVED_PROJECT", secret not in read(toe))
            os.makedirs(os.path.join(top, "restart"))
            res2 = g.launch("codex_restart", tox, os.path.join(top, "restart"), proj, env, LAUNCH2, deadline_s=700,
                            toe=toe)
    finally:
        close_engines()
    shutil.rmtree(proj, ignore_errors=True)
    T("safety.THE_REAL_CODEX_CONFIG_WAS_NOT_TOUCHED", (read(real) if os.path.isfile(real) else None) == real_before)
    leaks = []
    for path in glob.glob(os.path.join(top, "**", "*"), recursive=True):   # incl. the engines' own logs and databases
        if os.path.isfile(path) and os.path.getsize(path) < 80_000_000:
            try:
                blob = read(path)
            except OSError:
                continue
            for t in (STATE.get("token"), STATE.get("token_old"), STATE.get("claude_token")):
                if t and t.split(".", 1)[-1].encode() in blob:
                    leaks.append(os.path.relpath(path, top))
    T("leak.NO_PLAINTEXT_TOKEN_IN_ANY_FILE_INCLUDING_THE_CODEX_HOME", not leaks, files=sorted(set(leaks))[:6])
    in_td = sum(((x.get("tests") or []) for x in (res, res2)), [])
    errs = sum(((x.get("errors") or []) for x in (res, res2)), [])
    out = {"tox_sha256": g.hashlib.sha256(read(tox)).hexdigest(), "td_build": res.get("td_build"),
           "live": STATE["live"], "engine_versions": STATE.get("versions"),
           "extension_version": STATE.get("extension_version"), "claude_lines": STATE["seen"],
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]},
           "codex_record": res.get("codex_record"), "connected_line": res.get("codex_connected_line")}
    out["result"] = "pass" if (in_td and out["in_td"]["passed"] == out["in_td"]["total"] and not errs
                               and out["harness"]["passed"] == out["harness"]["total"]) else "fail"
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps({"result": out["result"], "in_td": f"{out['in_td']['passed']}/{out['in_td']['total']}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}",
                      "failed": [t["name"] for t in in_td + H["tests"] if not t["ok"]], "errors": errs[:2],
                      "engines": out["engine_versions"], "extension": out["extension_version"],
                      "connected_line": out["connected_line"], "claude": STATE["seen"]}, indent=1))


if __name__ == "__main__":
    main()
