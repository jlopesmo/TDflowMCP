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
Then: Suspend / resume, save, restart, reconnect without repeating setup, revocation, repair, a failed setup and
Disconnect (byte-identical rollback). Results: build/td-native-test-results.json (no secrets).
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
           "mcp__tdflow__tdflow_set_parameters"]
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


def cp_connect_live(data, pid):
    STATE["proj"] = data["project_folder"]
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


def cp_connect_suspended(data, pid):
    r = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("native.SUSPENDED_REFUSES_THE_AGENT", r.get("code") == "SUSPENDED" or r.get("_http") in (401, 503), r=r)
    return {}


def cp_connect_resumed(data, pid):
    T("native.RESUMED_AGENT_WORKS", mt.ok(mt.MCP(STATE["token"]).call("tdflow_whoami")))
    return {}


def cp_restart_live(data, pid):
    T("restart.SAME_CREDENTIAL_AFTER_RESTART", helper_token() == STATE.get("token"))
    if STATE["live"]:
        line = mcp_list(shutil.which("claude"), "cli-after-restart")
        T("restart.CLAUDE_CODE_RECONNECTS_WITHOUT_SETUP", "Connected" in line and "Failed" not in line, line=line)
    else:
        T("restart.helper_token_works", mt.ok(mt.MCP(STATE["token"]).call("tdflow_whoami")))
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


def cp_restart_disconnected(data, pid):
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


HANDLERS = {"connect_live": cp_connect_live, "connect_suspended": cp_connect_suspended,
            "connect_resumed": cp_connect_resumed}
RESTART = {"restart_live": cp_restart_live, "restart_revoked": cp_restart_revoked,
           "restart_repaired": cp_restart_repaired, "restart_disconnected": cp_restart_disconnected}


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
    STATE.update(top=top, live=not a.no_live, home=hm, original_bytes=raw)
    proj = tempfile.mkdtemp(prefix="tdflow-native-project-")       # outside the repository
    STATE["proj"] = proj
    run_dir = os.path.join(top, "native")
    os.makedirs(run_dir)
    tox = os.path.abspath(a.tox)
    env = {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private"), "TDFLOW_CLIENT_HOME": hm}
    res = g.launch("native", tox, run_dir, proj, env, HANDLERS)
    toe = res.get("native_toe")
    T("restart.project_saved", bool(toe) and os.path.exists(toe))
    res2 = {}
    if toe and os.path.exists(toe):
        secret = STATE.get("token", "").split(".", 1)[-1].encode()
        T("restart.NO_TOKEN_IN_THE_SAVED_PROJECT", bool(secret) and secret not in open(toe, "rb").read())
        os.makedirs(os.path.join(top, "restart"))
        res2 = g.launch("native_restart", tox, os.path.join(top, "restart"), proj, env, RESTART, deadline_s=500,
                        toe=toe)
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
    in_td = (res.get("tests") or []) + (res2.get("tests") or [])
    errs = (res.get("errors") or []) + (res2.get("errors") or [])
    out = {"tox_sha256": g.hashlib.sha256(open(tox, "rb").read()).hexdigest(), "td_build": res.get("td_build"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]},
           "live": res.get("native"), "mcp_list": STATE.get("mcp_list"),
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
