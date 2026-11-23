"""Phase 1C acceptance in TouchDesigner: a live Claude Code session connects using only the generated
"Tell Your Coding Agent" text, and a Skills install/update/uninstall round-trip. Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_onboard_tests.py [--tox build/TDflowMCP.tox] [--no-live]

Safety:
- disposable project, data, private storage and Skills home under .tmp/td-onboard-tests/ only;
- the MCP client config is a temporary file in .tmp passed with ``--mcp-config`` and ``--strict-mcp-config``:
  the user's Claude Code configuration and ~/.claude/skills are never written (``claude mcp add`` is not run);
- the token reaches Claude Code only through the TDFLOW_TOKEN environment variable of the child process;
- Claude Code may use exactly the tools listed in ``--allowedTools``; no permission bypass is used.
Results: build/td-onboard-test-results.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_gate_tests as g  # noqa: E402

mt, base, T, H, TOKENS = g.mt, g.base, g.T, g.H, g.TOKENS
ROOT = base.ROOT
STATE = {}
ALLOWED = ["mcp__tdflow__tdflow_whoami", "mcp__tdflow__tdflow_get_parameters", "mcp__tdflow__tdflow_set_parameters",
           "Skill"]
PROMPT = ("You are connected to a TouchDesigner project through the MCP server named tdflow. "
          "If a skill named tdflow-core is available, use it and note its TDflowMCP skill version. "
          "1) Call tdflow_whoami. 2) Set parameter opacity of /project1/fx/level1 to 0.25 with tdflow_set_parameters. "
          "3) Read it back with tdflow_get_parameters. "
          "Finish with exactly one line: RESULT skill_version=<version or none> expected_skill_version=<from whoami> "
          "opacity=<value read back> audit_seq=<from the set call>")


def extract_claude_json(tell: str):
    block = tell.split("- Claude Code", 1)[1].split("\n   - ", 1)[0]
    m = re.search(r"JSON config entry: `(\{.*\})`", block)
    return json.loads(m.group(1)) if m else None


def cp_onboard(data, pid):
    out = {}
    # 1. Connection, from the generated text only.
    r = base.req("GET", "/setup/api/tellagent", {"X-TDflow-Request": "1"})
    tell = r.get("body", b"").decode("utf-8")
    cfg = extract_claude_json(tell)
    secret = TOKENS["B"].split(".", 1)[1]
    T("connect.text_has_claude_json_with_placeholder", bool(cfg) and "${TDFLOW_TOKEN}" in json.dumps(cfg)
      and secret not in tell)
    for shell in ("bash:", "zsh:", "PowerShell:"):
        line = next((l for l in tell.splitlines() if l.strip().startswith(shell) and "claude mcp add" in l), "")
        T(f"connect.{shell[:-1].lower()}_command_keeps_placeholder_literal", "'Authorization: Bearer ${TDFLOW_TOKEN}'" in line,
          line=line)
    work = data["project_folder"]
    cfg_path = os.path.join(STATE["top"], "claude-mcp-config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    # 2. Skills round-trip through the paired admin API (project scope: the disposable project folder; user
    #    scope: the disposable TDFLOW_SKILLS_HOME).
    sk = (base.jbody(mt.admin("GET", "/setup/api/skills")) or {}).get("targets", [])
    proj = next((t for t in sk if t["client"] == "claude-code" and t["scope"] == "project"), None)
    user = next((t for t in sk if t["client"] == "claude-code" and t["scope"] == "user"), None)
    T("skills.targets_listed", bool(proj and user and "error" not in proj and "error" not in user), targets=sk)
    T("skills.preview_writes_nothing", not os.path.exists(os.path.join(work, ".claude")))
    r1 = mt.admin("POST", "/setup/api/skills/install", {"client": "claude-code", "scope": "user",
                                                         "digest": user["install"]["digest"]})
    uf = os.path.join(STATE["skills_home"], ".claude", "skills", "tdflow-core", "SKILL.md")
    T("skills.install_user_scope", r1["status"] == 200 and os.path.exists(uf), status=r1["status"], body=base.jbody(r1))
    stale = mt.admin("POST", "/setup/api/skills/install", {"client": "claude-code", "scope": "user",
                                                            "digest": user["install"]["digest"]})
    T("skills.stale_preview_refused", stale["status"] == 409 and (base.jbody(stale) or {}).get("code") == "PREVIEW_CHANGED")
    bad = mt.admin("POST", "/setup/api/skills/install", {"client": "codex", "scope": "user", "digest": "x"})
    T("skills.unlisted_target_refused", bad["status"] == 400)
    with open(uf, "a", encoding="utf-8") as f:
        f.write("\nmy own note\n")
    sk2 = (base.jbody(mt.admin("GET", "/setup/api/skills")) or {}).get("targets", [])
    u2 = next(t for t in sk2 if t["client"] == "claude-code" and t["scope"] == "user")
    T("skills.modified_file_is_a_conflict", u2["install"]["conflicts"] == ["tdflow-core/SKILL.md"])
    ow = mt.admin("POST", "/setup/api/skills/install", {"client": "claude-code", "scope": "user",
                                                         "digest": u2["install"]["digest"]})
    T("skills.never_overwrites_modified", ow["status"] == 409 and "my own note" in open(uf, encoding="utf-8").read())
    man = os.path.join(STATE["skills_home"], ".claude", "skills", "tdflow-skills.manifest.json")
    m = json.load(open(man, encoding="utf-8"))
    m["version"] = "0.9"
    json.dump(m, open(man, "w", encoding="utf-8"))
    sk3 = (base.jbody(mt.admin("GET", "/setup/api/skills")) or {}).get("targets", [])
    u3 = next(t for t in sk3 if t["client"] == "claude-code" and t["scope"] == "user")
    T("version.mismatch_surfaced_on_setup_page", u3["mismatch"] is True and u3["installed_version"] == "0.9")
    un = mt.admin("POST", "/setup/api/skills/uninstall", {"client": "claude-code", "scope": "user",
                                                           "digest": u3["uninstall"]["digest"]})
    T("skills.uninstall_keeps_modified", un["status"] == 200 and (base.jbody(un) or {}).get("kept_modified") ==
      ["tdflow-core/SKILL.md"] and os.path.exists(uf) and not os.path.exists(man))
    r2 = mt.admin("POST", "/setup/api/skills/install", {"client": "claude-code", "scope": "project",
                                                         "digest": proj["install"]["digest"]})
    pf = os.path.join(work, ".claude", "skills", "tdflow-core", "SKILL.md")
    T("skills.install_project_scope", r2["status"] == 200 and os.path.exists(pf))
    blob = b"".join(open(os.path.join(d, f), "rb").read() for root in (os.path.join(work, ".claude"),
                    os.path.join(STATE["skills_home"], ".claude")) for d, _, fs in os.walk(root) for f in fs)
    T("skills.no_token_in_skills_files", secret.encode() not in blob)
    # 3. Live Claude Code session (instruction-only connection; token only in the child environment).
    if STATE["live"]:
        exe = shutil.which("claude")
        ver = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
        env = dict(os.environ, TDFLOW_TOKEN=TOKENS["B"])
        t0 = time.time()
        p = subprocess.run([exe, "-p", PROMPT, "--mcp-config", cfg_path, "--strict-mcp-config",
                            "--allowedTools", *ALLOWED, "--output-format", "json"],
                           cwd=work, env=env, capture_output=True, text=True, timeout=600)
        with open(os.path.join(STATE["top"], "claude_output.json"), "w", encoding="utf-8") as f:   # .tmp only
            json.dump({"stdout": p.stdout.replace(secret, "<redacted>"), "stderr": p.stderr.replace(secret, "<redacted>")}, f)
        st = base.jbody(base.req("GET", "/setup/api/status", {"X-TDflow-Request": "1"})) or {}
        out["transport_after"] = st.get("transport") or st.get("listener")
        out["claude_version"] = ver
        out["seconds"] = round(time.time() - t0, 1)
        try:
            res = json.loads(p.stdout)
        except ValueError:
            res = {"result": p.stdout[-2000:]}
        text = str(res.get("result", ""))
        line = next((l for l in text.splitlines() if l.strip().startswith("RESULT")), "")
        out["result_line"] = line
        out["rc"] = p.returncode
        out["num_turns"] = res.get("num_turns")
        T("live.claude_code_session_ok", p.returncode == 0 and bool(line), rc=p.returncode, line=line,
          stderr=p.stderr[-800:])
        T("live.opacity_read_back", "opacity=0.25" in line, line=line)
        T("live.expected_skill_version_from_whoami", "expected_skill_version=1.0" in line, line=line)
        T("live.project_skill_discovered", "skill_version=1.0" in line, line=line)
        T("live.no_token_in_agent_output", secret not in p.stdout and secret not in p.stderr)
        with open(cfg_path, encoding="utf-8") as f:
            T("live.client_config_file_has_no_token", secret not in f.read())
    return out


HANDLERS = {"setup_ready": mt.cp_setup_ready, "pair_approved": mt.cp_pair_approved, "onboard": cp_onboard}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-onboard-test-results.json"))
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-onboard-tests"), ROOT)
    if os.path.exists(top):
        shutil.rmtree(top)
    os.makedirs(top)
    STATE.update(top=top, live=not a.no_live, skills_home=os.path.join(top, "home"))
    os.makedirs(STATE["skills_home"])
    proj, run_dir = os.path.join(top, "project"), os.path.join(top, "onboard")
    os.makedirs(proj)
    os.makedirs(run_dir)
    tox = os.path.abspath(a.tox)
    res = g.launch("onboard", tox, run_dir, proj, {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private"),
                                                   "TDFLOW_SKILLS_HOME": STATE["skills_home"]}, HANDLERS)
    in_td = res.get("tests") or []
    errs = res.get("errors") or []
    out = {"tox_sha256": g.hashlib.sha256(open(tox, "rb").read()).hexdigest(), "td_build": res.get("td_build"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]},
           "live": res.get("onboard")}
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
