"""Dogfooding: real Claude Code sessions use TDflowMCP for realistic work in a disposable TouchDesigner
project (owner priority, 2026-11-22). Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_dogfood.py [--tox build/TDflowMCP.tox] [--tasks inspect,edit_undo,...]

Isolation (review 8):
- The disposable TouchDesigner project (Claude Code's working directory) is a fresh folder in the system
  temp directory, outside this repository. The repository's CLAUDE.md, auto-memory and project settings
  therefore do not apply. Results and transcripts stay under .tmp/td-dogfood/.
- Claude Code runs with ``--strict-mcp-config`` (only the temporary MCP config), ``--tools Skill,ToolSearch``,
  ``--setting-sources project`` (the disposable project's settings only: no user/local settings, hooks or
  user Skills), ``--permission-mode default``, explicit ``--disallowedTools`` and ``--no-session-persistence``. The harness asserts from the
  session's init message that only TDflowMCP tools, Skill and ToolSearch exist.
- The token is only in the child environment. ``claude mcp add`` is never run, and no Claude Code settings or
  ~/.claude/skills are written. Claude Code may still update its own client state (e.g. ~/.claude.json) as any
  CLI run does.
- On timeout, only the harness's own child process tree is killed.
Results: build/td-dogfood-results.json (tool calls, errors, turns, durations, cost; no secrets).
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
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_onboard_tests as ob  # noqa: E402

g, mt, base, T, H, TOKENS = ob.g, ob.mt, ob.base, ob.T, ob.H, ob.TOKENS
ROOT = base.ROOT
TOOLS = ["tdflow_connect", "tdflow_create_operator", "tdflow_delete_operator", "tdflow_disconnect",
         "tdflow_get_dat_text", "tdflow_get_errors", "tdflow_get_operator", "tdflow_get_parameters",
         "tdflow_list_operators", "tdflow_project_info", "tdflow_session_list", "tdflow_session_note",
         "tdflow_set_dat_text", "tdflow_set_parameters", "tdflow_undo", "tdflow_whoami",
         "tdflow_task_list", "tdflow_task_checkpoint", "tdflow_handoff_create", "tdflow_handoff_resume",
         # Phase 3/4 (2026-11-23)
         "tdflow_describe_network", "tdflow_capture_top", "tdflow_experiment_create", "tdflow_experiment_promote",
         "tdflow_experiment_discard", "tdflow_lease_scope", "tdflow_approval_status", "tdflow_capabilities"]
ALLOWED = [f"mcp__tdflow__{t}" for t in TOOLS] + ["Skill"]
SESSION_S = 420                                   # per session; the launch and in-TD budgets cover 5 of these
TASKS = {
    "inspect": "Use the tdflow MCP tools to inspect the TouchDesigner network under /project1/fx. Describe each "
               "operator, how they are wired, and report any errors or warnings. Do not change anything.",
    "edit_undo": "In /project1/fx, set the brightness of level1 to 1.5, read it back to verify, then undo that "
                 "change with tdflow_undo and verify the original value is restored. Report the values you saw.",
    "build": "In /project1/fx, add a Blur TOP named blur1 and wire it between level1 and out, so the chain is "
             "noise1 -> level1 -> blur1 -> out. Verify the wiring by reading the operators back and check for errors.",
    "data_notes": "Replace the text of the DAT /project1/fx/notes with a short markdown summary of the network in "
                  "/project1/fx, read it back to verify, then add a session note describing what you did and list "
                  "your recent sessions.",
    "boundaries": "Using only the tdflow tools, try each of these once and report exactly what TDflowMCP answered: "
                  "(a) read the operator /project1/TDflowMCP, (b) set an expression 'absTime.seconds % 1' on the "
                  "opacity parameter of /project1/fx/level1, (c) create an Execute DAT named exec2 in /project1/fx. "
                  "Do not try workarounds.",
}
# Phase 3/4 dogfood (2026-11-23): the network summary, image capture and experiments, as a builder would use them.
TASKS.update({
    "analyze": "Using the tdflow tools, give me an overview of the network in /project1/fx: what each operator does, "
               "how they are wired, what the optimization hints say, and what the final image in /project1/fx/out looks "
               "like (look at it). Do not change anything.",
    "experiment": "In /project1/fx, create a Container COMP named look with a Noise TOP named n1 inside it. Then try a "
                  "change safely: make an experiment copy of look named look_try, set the amplitude of the Noise TOP in "
                  "the copy to 0.3, look at the copy's image to check it, and if it looks fine promote the experiment so "
                  "it replaces look. Verify the result and report what you did.",
})
# Phase 2A dogfood: two separate sessions (different agents) share one task through project memory. The
# resume prompt is the "Resume earlier work" section of Tell Your Coding Agent plus one sentence.
TASKS.update({
    "handoff_start": "In /project1/fx, work on a task with task_id blur_chain: add a Blur TOP named blur1 between "
                     "level1 and out (noise1 -> level1 -> blur1 -> out). Record a task checkpoint when you start, make "
                     "and verify the change, then create a handoff for the next agent with claims the server can "
                     "check. Then stop and report what the server said about your claims.",
    "handoff_resume": "A previous agent worked in this TouchDesigner project. Resume its open task through TDflowMCP, "
                      "find out what changed since its handoff (including anything changed outside TDflowMCP), then "
                      "finish the task by setting the size of blur1 to 10, verify it, and record a final checkpoint "
                      "with status done. Report what you found.",
})
MEMORY_TASKS = ("handoff_start", "handoff_resume")
STATE = {}


def run_session(name, prompt, cfg, work, secret, token=None):
    exe = shutil.which("claude")
    env = dict(os.environ, TDFLOW_TOKEN=token or TOKENS["B"])
    t0 = time.time()
    rc, raw, err = ob.run_claude(exe, ["-p", prompt, "--mcp-config", cfg, "--strict-mcp-config", "--allowedTools",
                                       *ALLOWED, *ob.ISOLATION, "--output-format", "stream-json", "--verbose"],
                                 work, env, SESSION_S)
    p = types.SimpleNamespace(returncode=rc, stdout=raw, stderr=err)
    tools = ob.init_tools(raw)
    T(f"dogfood.{name}.agent_tools_isolated", ob.isolated_ok(tools), tools=tools)
    out = p.stdout.replace(secret, "<redacted>")
    with open(os.path.join(STATE["top"], f"transcript-{name}.jsonl"), "w", encoding="utf-8") as f:
        f.write(out)
    calls, results, final = [], {}, {}
    for line in out.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("type") == "assistant":
            for c in (m.get("message") or {}).get("content", []):
                if c.get("type") == "tool_use":
                    calls.append({"id": c.get("id"), "tool": c.get("name", "").replace("mcp__tdflow__", ""),
                                  "input": json.dumps(c.get("input"))[:300]})
        elif m.get("type") == "user":
            for c in (m.get("message") or {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    txt = c.get("content")
                    txt = json.dumps(txt) if not isinstance(txt, str) else txt
                    code = None
                    try:
                        inner = json.loads(json.loads(txt)[0]["text"]) if txt.startswith("[") else json.loads(txt)
                        code = inner.get("code") if isinstance(inner, dict) else None
                    except (ValueError, KeyError, TypeError, IndexError):
                        pass
                    results[c.get("tool_use_id")] = {"is_error": bool(c.get("is_error")), "code": code,
                                                     "excerpt": txt[:240]}
        elif m.get("type") == "result":
            final = {"turns": m.get("num_turns"), "duration_ms": m.get("duration_ms"),
                     "duration_api_ms": m.get("duration_api_ms"), "cost_usd": m.get("total_cost_usd"),
                     "is_error": m.get("is_error"), "result": str(m.get("result", ""))[:2500]}
    for c in calls:
        c.update(results.get(c.pop("id"), {}))
    return {"task": name, "rc": p.returncode, "wall_s": round(time.time() - t0, 1), "calls": calls, **final,
            "stderr_tail": p.stderr.replace(secret, "<redacted>")[-500:],
            "secret_leak": secret in p.stdout or secret in p.stderr}


def cp_dogfood(data, pid):
    work = data["project_folder"]
    r = base.req("GET", "/setup/api/tellagent", {"X-TDflow-Request": "1"})
    cfg_obj = ob.extract_claude_json(r.get("body", b"").decode("utf-8"))
    cfg = os.path.join(STATE["top"], "claude-mcp-config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump(cfg_obj, f)
    sk = (base.jbody(mt.admin("GET", "/setup/api/skills")) or {}).get("targets", [])
    proj = next(t for t in sk if t["client"] == "claude-code" and t["scope"] == "project")
    inst = mt.admin("POST", "/setup/api/skills/install", {"client": "claude-code", "scope": "project",
                                                           "digest": proj["install"]["digest"]})
    T("dogfood.skill_installed_by_consent_flow", inst["status"] == 200)
    secret = TOKENS["B"].split(".", 1)[1]
    sessions = []
    STATE.update(cfg=cfg, work=work, secret=secret)
    for name in [t for t in STATE["tasks"] if t != "handoff_resume"]:
        s = run_session(name, TASKS[name], cfg, work, secret)
        sessions.append(s)
        T(f"dogfood.{name}.session_completed", s["rc"] == 0 and not s.get("is_error"), rc=s["rc"])
        T(f"dogfood.{name}.no_token_leak", not s["secret_leak"])
    STATE["sessions"] = sessions
    return {"sessions": len(sessions)}


def cp_dogfood_resume(data, pid):
    """The second agent gets only the Resume section the user would paste from Tell Your Coding Agent."""
    tell = data.get("tell", "")
    sec = tell[tell.find("## Resume earlier work"):] if "## Resume earlier work" in tell else ""
    sec = sec.split("\n## ", 1)[0] if "\n## " in sec[3:] else sec
    T("dogfood.tell_has_resume_section", "tdflow_task_list" in sec and "1 task(s)" in sec, section=sec[:600])
    STATE["resume_section"] = sec
    prompt = sec.strip() + "\n\n" + TASKS["handoff_resume"]
    tok = TOKENS["A"]                                 # a different agent continues the task
    s = run_session("handoff_resume", prompt, STATE["cfg"], STATE["work"], tok.split(".", 1)[1], token=tok)
    STATE["sessions"].append(s)
    T("dogfood.handoff_resume.session_completed", s["rc"] == 0 and not s.get("is_error"), rc=s["rc"])
    T("dogfood.handoff_resume.no_token_leak", not s["secret_leak"])
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-dogfood-results.json"))
    ap.add_argument("--tasks", default=",".join(t for t in TASKS if t not in MEMORY_TASKS))
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-dogfood"), ROOT)
    if os.path.exists(top):
        shutil.rmtree(top)
    os.makedirs(top)
    STATE.update(top=top, tasks=[t for t in a.tasks.split(",") if t in TASKS])
    proj = tempfile.mkdtemp(prefix="tdflow-dogfood-project-")      # outside the repository (review 8, M3)
    run_dir = os.path.join(top, "run")
    os.makedirs(run_dir)
    env = {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private"), "TDFLOW_SKILLS_HOME": os.path.join(top, "home")}
    if any(t in MEMORY_TASKS for t in STATE["tasks"]):
        env["TDFLOW_DOGFOOD_MEMORY"] = "1"
    res = g.launch("dogfood", os.path.abspath(a.tox), run_dir, proj, env,
                   {"setup_ready": mt.cp_setup_ready, "pair_approved": mt.cp_pair_approved, "dogfood": cp_dogfood,
                    "dogfood_resume": cp_dogfood_resume},
                   deadline_s=600 + SESSION_S * len(STATE["tasks"]))
    shutil.rmtree(proj, ignore_errors=True)
    in_td = res.get("tests") or []
    out = {"td_build": res.get("td_build"), "claude_version": subprocess.run(
               [shutil.which("claude"), "--version"], capture_output=True, text=True).stdout.strip(),
           "sessions": STATE.get("sessions", []), "state": res.get("dogfood_state"), "audit": res.get("dogfood_audit"),
           "memory": res.get("dogfood_memory"), "resume_section": STATE.get("resume_section"),
           "onframe": res.get("frame_stats_onframe"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td,
                     "errors": res.get("errors") or []},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]}}
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps({"in_td": f"{out['in_td']['passed']}/{out['in_td']['total']}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}",
                      "failed": [t["name"] for t in in_td + H["tests"] if not t["ok"]],
                      "errors": out["in_td"]["errors"][:2],
                      "sessions": [{k: s.get(k) for k in ("task", "turns", "wall_s", "cost_usd")} |
                                   {"calls": len(s["calls"]), "errors": sum(1 for c in s["calls"] if c.get("is_error"))}
                                   for s in out["sessions"]]}, indent=1))


if __name__ == "__main__":
    main()
