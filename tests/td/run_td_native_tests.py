"""First-run acceptance without a browser (owner UX assignment, 2026-11-23). Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_native_tests.py [--tox build/TDflowMCP.tox] [--no-live]

A disposable project gets the component; everything the user does happens through its parameters (td_runner_gate
mode "native"): automatic and manual diagnostics, Coding Agent Auto, Connect Agent, Copy Token, Enable MCP, then a
real Claude Code session configured only from the generated Tell Your Coding Agent text with the token the user
pasted (read here from the clipboard), Connected Agents, Suspend and resume. No pairing and no browser.
Results: build/td-native-test-results.json (no secrets).
"""
from __future__ import annotations

import argparse
import json
import os
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


def cp_native_onboard(data, pid):
    tok = mt.clipboard()
    STATE["token"] = tok
    secret = tok.split(".", 1)[1] if "." in tok else tok
    tell = data.get("tell", "")
    T("native.TOKEN_PASTED_FROM_THE_CLIPBOARD", tok.startswith("tdf_"))
    T("native.instructions_contain_no_token", secret not in tell and "${TDFLOW_TOKEN}" in tell)
    T("native.instructions_describe_the_td_only_path", "Connect Agent" in tell and "Copy Token" in tell)
    cfg = ob.extract_claude_json(tell)
    T("native.claude_config_from_the_text", bool(cfg))
    out = {}
    if not STATE["live"] or not cfg:
        return out
    cfg_path = os.path.join(STATE["top"], "claude-mcp-config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    exe = shutil.which("claude")
    out["claude_version"] = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    env = dict(os.environ, TDFLOW_TOKEN=tok)
    prompt = ("You are connected to a TouchDesigner project through the MCP server named tdflow. 1) Call tdflow_whoami. "
              "2) Set parameter opacity of /project1/fx/level1 to 0.25 with tdflow_set_parameters. 3) Read it back with "
              "tdflow_get_parameters. Finish with exactly one line: RESULT role=<role from whoami> opacity=<value read back>")
    t0 = time.time()
    rc, sout, serr = ob.run_claude(exe, ["-p", prompt, "--mcp-config", cfg_path, "--strict-mcp-config", "--allowedTools",
                                         *ob.ALLOWED, *ob.ISOLATION, "--output-format", "stream-json", "--verbose"],
                                   data["project_folder"], env, 600)
    T("native.live_agent_tools_isolated", ob.isolated_ok(ob.init_tools(sout)))
    res = {}
    for line in sout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("type") == "result":
            res = m
    text = str(res.get("result", ""))
    line = next((x for x in text.splitlines() if x.strip().startswith("RESULT")), "")
    out.update(seconds=round(time.time() - t0, 1), rc=rc, result_line=line, turns=res.get("num_turns"))
    T("native.LIVE_CLAUDE_CODE_SESSION", rc == 0 and "role=builder" in line and "opacity=0.25" in line, line=line,
      stderr=serr[-500:])
    T("native.no_token_in_agent_output", secret not in sout and secret not in serr)
    with open(cfg_path, encoding="utf-8") as f:
        T("native.client_config_has_no_token", secret not in f.read())
    return out


def cp_native_suspended(data, pid):
    r = mt.MCP(STATE["token"]).call("tdflow_whoami")
    T("native.SUSPENDED_REFUSES_THE_AGENT", r.get("code") == "SUSPENDED" or r.get("_http") in (401, 503), r=r)
    return {}


def cp_native_resumed(data, pid):
    T("native.RESUMED_AGENT_WORKS", mt.ok(mt.MCP(STATE["token"]).call("tdflow_whoami")))
    return {}


def cp_native_restart(data, pid):
    T("restart.OLD_TOKEN_STILL_WORKS", mt.ok(mt.MCP(STATE["token"]).call("tdflow_whoami")))
    return {}


HANDLERS = {"native_onboard": cp_native_onboard, "native_suspended": cp_native_suspended,
            "native_resumed": cp_native_resumed}
RESTART = {"native_restart": cp_native_restart}


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
    STATE.update(top=top, live=not a.no_live)
    proj = tempfile.mkdtemp(prefix="tdflow-native-project-")       # outside the repository
    run_dir = os.path.join(top, "native")
    os.makedirs(run_dir)
    tox = os.path.abspath(a.tox)
    env = {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private")}
    res = g.launch("native", tox, run_dir, proj, env, HANDLERS)
    toe = res.get("native_toe")
    T("restart.project_saved", bool(toe) and os.path.exists(toe))
    res2 = {}
    if toe and os.path.exists(toe):
        secret = STATE.get("token", "").split(".", 1)[-1].encode()
        T("restart.NO_TOKEN_IN_THE_SAVED_PROJECT", bool(secret) and secret not in open(toe, "rb").read())
        os.makedirs(os.path.join(top, "restart"))
        res2 = g.launch("native_restart", tox, os.path.join(top, "restart"), proj, env, RESTART, deadline_s=400, toe=toe)
    shutil.rmtree(proj, ignore_errors=True)
    in_td = (res.get("tests") or []) + (res2.get("tests") or [])
    errs = (res.get("errors") or []) + (res2.get("errors") or [])
    out = {"tox_sha256": g.hashlib.sha256(open(tox, "rb").read()).hexdigest(), "td_build": res.get("td_build"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]},
           "live": res.get("native")}
    out["result"] = "pass" if (in_td and out["in_td"]["passed"] == out["in_td"]["total"] and not errs
                               and out["harness"]["passed"] == out["harness"]["total"]) else "fail"
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps({"result": out["result"], "in_td": f"{out['in_td']['passed']}/{out['in_td']['total']}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}",
                      "failed": [t["name"] for t in in_td + H["tests"] if not t["ok"]], "errors": errs[:2],
                      "live": out["live"]}, indent=1))


if __name__ == "__main__":
    main()
