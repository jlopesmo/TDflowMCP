"""Packaged offline draft acceptance through HTTP, Chromium, Claude Code and Codex."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "web")]
import cdp
import codex_engine
import run_td_diagnostic_tests as diagnostic
import run_td_gate_tests as gate
import run_td_mcp_tests as mcp


ROOT = Path(gate.ROOT)
SCOPE = "/project1/fx"
URL = "/api/script-drafts"
FAMILIES = ("CHOP", "SOP", "TOP")
TOOLS = {action: "tdflow_script_draft_" + action for action in ("guide", "list", "get", "create", "revise", "submit")}
STATE = {}
MARK = "OFFLINE_NATIVE_CANARY"


def body(r):
    return mcp.base.jbody(r) or {}


def ok(check, result):
    gate.T(check, mcp.ok(result))
    if not mcp.ok(result):
        raise RuntimeError(check + " returned " + str(result.get("code", result.get("_code", "error"))))
    return result


def exact(d):
    return {k: d[k] for k in ("id", "revision", "sha256")}


def request(action, source, title, family="TOP", **extra):
    return {"scope": SCOPE, "family": family, "source": source, "title": title, **extra}


def credentials(label, temporary=True):
    r = mcp.admin("POST", "/setup/api/tokens", {"display_name": label, "role": "observer",
                                               "scope": SCOPE + "/**"})
    if r["status"] != 200:
        raise RuntimeError("Disposable agent setup was refused.")
    d = body(r)
    if temporary:
        grant(d["agent"]["id"])
    return d["token"], d["agent"]["id"]


def grant(agent, scope=SCOPE):
    return mcp.admin("POST", URL + "/grant", {"agent": agent, "scope": scope, "minutes": 25,
                                             "read": True, "author": True, "submit": True})


def draft(client, action, args=None):
    return client.call(TOOLS[action], args)


def check_browser(work, expected, source):
    browser = cdp.Browser(str(work / "dashboard-browser"))
    try:
        browser.call("Network.enable")
        origin = mcp.O
        browser.call("Network.setCookie", name="tdflow_admin", value=mcp.STATE["cookies"]["tdflow_admin"],
                     url=origin + "/ui/")
        browser.call("Page.addScriptToEvaluateOnNewDocument", source=
                     "sessionStorage.setItem('tdflow_csrf'," + json.dumps(mcp.STATE["csrf"]) + ");")
        browser.goto(origin + "/ui/#drafts")
        timeout = time.monotonic() + 12
        while time.monotonic() < timeout and not browser.js("document.querySelector('#draft-choice').options.length > 1"):
            time.sleep(0.2)
        browser.js("const picker=document.querySelector('#draft-choice'); picker.value=" + json.dumps(expected["id"])
                   + "; picker.dispatchEvent(new Event('change'));")
        timeout = time.monotonic() + 12
        while time.monotonic() < timeout and browser.js("document.querySelector('#draft-details').hidden"):
            time.sleep(0.2)
        evidence = browser.js("""({
          source: document.querySelector('#draft-source').textContent,
          nested: document.querySelector('#draft-source').children.length,
          xss: Boolean(globalThis.tdflowOfflineProbe),
          warning: document.querySelector('#draft-warning').textContent,
          hash: document.querySelector('#draft-hash').textContent
        })""")
        gate.T("drafts.browser_renders_exact_text_and_no_html", evidence["source"] == source
               and evidence["nested"] == 0 and not evidence["xss"] and expected["sha256"] in evidence["hash"])
        gate.T("drafts.browser_shows_host_warning", "host access" in evidence["warning"] and "Undo" in evidence["warning"])
        browser.js("document.querySelector('#draft-approve').click()")
        timeout = time.monotonic() + 12
        while time.monotonic() < timeout:
            view = body(mcp.admin("POST", URL + "/view", {"id": expected["id"]}))
            if view.get("state") == "approved_offline":
                break
            time.sleep(0.25)
        gate.T("drafts.browser_exact_decision", view.get("state") == "approved_offline"
               and view.get("reviewed_at") and view.get("sha256") == expected["sha256"])
    finally:
        browser.close()


def live_claude(work):
    token, aid = credentials("Isolated Claude offline drafting")
    prompt = ("This disposable TDflowMCP project authorizes only offline drafting under " + SCOPE +
              ". Call tdflow_script_draft_guide once. For each CHOP, SOP and TOP, create one short inert callback "
              "draft titled Claude CHOP, Claude SOP, or Claude TOP. Use scope " + SCOPE +
              ". Use the returned exact revision and hash to revise each once, with a clearer comment. Read each "
              "revision to inspect its diff and syntax diagnostics, then submit its exact current revision for owner "
              "review. Keep all work in the offline draft tools. Give the user the three families and review status.")
    verified, calls, answer, seconds = diagnostic.run_live(work, token, prompt, list(TOOLS.values()))
    drafts = body(mcp.admin("GET", URL)).get("drafts", [])
    rows = [d for d in drafts if d["agent"] == aid]
    called = [p.get("name", "") for p in calls]
    gate.T("drafts.real_claude_model_authored_and_submitted", verified
           and {d["family"] for d in rows if d["revision"] == 2 and d["state"] == "pending_offline"} == set(FAMILIES)
           and called.count("mcp__tdflow__" + TOOLS["get"]) >= 3 and bool(answer), duration_s=seconds)
    return {"duration_s": seconds, "calls": called, "families": [d["family"] for d in rows]}


def live_codex_model():
    executable = codex_engine.cli_engine()
    token, aid = credentials("Isolated Codex model offline drafting")
    server = ('mcp_servers={tdflow={url="' + mcp.O + '/mcp",bearer_token_env_var="TDFLOW_DRAFT_TEST_TOKEN",'
              "enabled_tools=" + json.dumps(list(TOOLS.values())) + "}}")
    prompt = ("Use only the TDflowMCP offline draft tools on this disposable workspace. "
              "Call the guide, then create a short inert callback draft for each of CHOP, SOP and TOP "
              "under " + SCOPE + ". Revise each once, read each revision's syntax and previous diff, "
              "then submit each exact current revision and hash for offline owner review. Report the "
              "three outcomes. Do all work through the offline draft tools.")
    flags = ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "apps",
             "--disable", "plugins", "--disable", "browser_use", "--disable", "computer_use"]
    result = {"accepted": False, "families": [], "draft_calls": 0}
    if executable:
        with tempfile.TemporaryDirectory(prefix="tdflow-offline-codex-") as cwd:
            try:
                r = subprocess.run([executable, "exec", "--ignore-user-config", "--ignore-rules",
                                    "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                                    *flags, "-c", 'web_search="disabled"', "-c", server, "--json", prompt],
                                   cwd=cwd, env=dict(os.environ, TDFLOW_DRAFT_TEST_TOKEN=token),
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
                events = [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]
                items = [e.get("item", {}) for e in events if e.get("type") == "item.completed"]
                calls = [i.get("tool") for i in items if i.get("type") == "mcp_tool_call" and i.get("server") == "tdflow"]
                rows = body(mcp.admin("GET", URL)).get("drafts", [])
                families = sorted(d["family"] for d in rows if d["agent"] == aid and d["revision"] == 2
                                  and d["state"] == "pending_offline")
                result = {"accepted": r.returncode == 0 and families == list(FAMILIES)
                          and calls.count(TOOLS["get"]) >= 3
                          and not any(i.get("type") == "command_execution" for i in items),
                          "families": families, "draft_calls": len(calls), "exit": r.returncode}
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                result["error"] = type(exc).__name__
    gate.T("drafts.real_codex_model_authored_and_submitted", result["accepted"], **result)
    return result


def live_codex(work, project):
    evidence = {}
    for label, executable in (("cli", codex_engine.cli_engine()), ("vscode", codex_engine.extension_engine()[0])):
        token, aid = credentials("Isolated Codex offline drafting " + label)
        home = work / ("codex-" + label)
        home.mkdir()
        (home / "config.toml").write_text(
            f'[mcp_servers.tdflow]\nurl = "{mcp.O}/mcp"\nbearer_token_env_var = "TDFLOW_DRAFT_TEST_TOKEN"\n',
            encoding="utf-8")
        if not executable:
            gate.T("drafts.codex_" + label + "_installed", False)
            continue
        engine = codex_engine.Engine(executable, str(home), str(project), label)
        try:
            with patch.dict(os.environ, {"TDFLOW_DRAFT_TEST_TOKEN": token}):
                engine.open()
            catalog = engine.servers().get("tdflow", {}).get("tools", {})
            gate.T("drafts.codex_" + label + "_discovery", set(TOOLS.values()) <= set(catalog))
            families = set()
            for family in FAMILIES:
                title = "Codex " + label + " " + family
                source = "def onCook(scriptOp):\n    return\n"
                asked, current = engine.call(TOOLS["create"], request("create", source, title, family))
                if not asked or not isinstance(current, dict) or not current.get("id"):
                    break
                asked, revised = engine.call(TOOLS["revise"], {**exact(current), "source": source + "# revised\n"})
                if not asked or not isinstance(revised, dict) or revised.get("revision") != 2:
                    break
                got, view = engine.call(TOOLS["get"], {"id": revised["id"]})
                submitted, final = engine.call(TOOLS["submit"], exact(revised))
                if (got and submitted and view["syntax_ok"] and "+# revised\n" in view["diff"]["lines"]
                        and final.get("state") == "pending_offline"):
                    families.add(family)
            reviewed = {d["family"] for d in body(mcp.admin("GET", URL)).get("drafts", [])
                        if d["agent"] == aid and d["state"] == "pending_offline" and d["revision"] == 2}
            gate.T("drafts.codex_" + label + "_real_transport_all_families", families == reviewed == set(FAMILIES))
            evidence[label] = {"version": engine.version, "families": sorted(families)}
        finally:
            engine.close()
    return evidence


def exercise(work, project, run, clients):
    ungranted, aid = credentials("Disposable offline fixture", temporary=False)
    c = mcp.MCP(ungranted)
    source = ("import socket\nfrom pathlib import Path\n"
              f"Path({str(run / 'candidate-side-effect.txt')!r}).write_text({MARK!r})\n"
              "@missing_decorator()\ndef callback(default=missing_call()):\n    return default\n"
              "html = '<img src=x onerror=globalThis.tdflowOfflineProbe=1>'\n")
    args = request("create", source, MARK, intended_target=SCOPE + "/future_top")
    gate.T("drafts.no_inherited_draft_grant", draft(c, "create", args).get("code") == "DENIED")
    no_csrf = mcp.admin("POST", URL + "/grant", {}, csrf=False)
    gate.T("drafts.no_admin_action_without_csrf", no_csrf["status"] == 403)
    granted = grant(aid)
    gate.T("drafts.paired_grant", granted["status"] == 200, status=granted["status"], code=body(granted).get("code"))
    bad = draft(c, "create", {**args, "intended_target": SCOPE + "/protected/hidden"})
    gate.T("drafts.protected_target_refused", bad.get("code") == "DENIED")
    gate.T("drafts.sibling_target_refused", draft(c, "create", {**args, "intended_target": "/project1/other"}).get("code") == "DENIED")
    hostile = ok("drafts.create_inert_host_payload", draft(c, "create", args))
    gate.T("drafts.hostile_text_stays_inert", hostile["syntax_ok"] and hostile["sha256"] == hashlib.sha256(source.encode()).hexdigest()
           and not (run / "candidate-side-effect.txt").exists())
    for family in FAMILIES:
        denied = mcp.MCP(mcp.TOKENS["E"]).call("tdflow_create_operator",
                                             {"parent": SCOPE, "type": "script" + family, "name": "forbidden"})
        gate.T("drafts.live_script_" + family + "_denied", denied.get("rule_id") == "G2")
    broken = ok("drafts.invalid_syntax_record_is_reviewable", draft(c, "create", request("create", "def broken(", "Syntax problem")))
    gate.T("drafts.broken_syntax_refused_for_submission", not broken["syntax_ok"]
           and draft(c, "submit", exact(broken)).get("code") == "DRAFT_STATE")
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(lambda n: draft(mcp.MCP(ungranted), "revise", {**exact(hostile),
                                  "source": source + f"# changed {n}\n"}), (1, 2)))
    gate.T("drafts.concurrent_stale_refused", sorted([a.get("code", "OK"), b.get("code", "OK")]) == ["DRAFT_CONFLICT", "OK"])
    current = ok("drafts.get_exact_revision", draft(c, "get", {"id": hostile["id"]}))
    gate.T("drafts.exact_bounded_diff", current["revision"] == 2 and any(x.startswith("+# changed") for x in current["diff"]["lines"]))
    ok("drafts.submit_exact", draft(c, "submit", exact(current)))
    bad = mcp.admin("POST", URL + "/decide", {**exact(hostile), "decision": "approved_offline"})
    gate.T("drafts.stale_owner_approval_refused", body(bad).get("code") == "DRAFT_CONFLICT")
    check_browser(work, current, current["source"])
    copy = body(mcp.admin("POST", URL + "/copy", exact(current)))
    gate.T("drafts.approved_copy_exact_host_warning", copy.get("source") == current["source"]
           and copy.get("manifest", {}).get("sha256") == current["sha256"])
    gate.T("drafts.no_self_review_tool", draft(c, "list")["drafts"][0].get("submission_token") is None)
    gate.T("drafts.no_source_in_audit", MARK not in json.dumps(body(mcp.admin("POST", "/api/audit/search", {"limit": 200}))))
    if clients:
        STATE["claude"] = live_claude(work)
        STATE["codex"] = live_codex(work, project)
        STATE["codex"]["model"] = live_codex_model()
    STATE.update(exact=exact(current), token=ungranted, aid=aid)
    return {}


def ready_again(data, pid):
    result = body(mcp.http("GET", "/setup/api/pair/status", {"X-TDflow-Request": "1"}))
    mcp.STATE["csrf"] = result.get("csrf")
    gate.T("drafts.restarted_admin_session", result.get("state") == "approved" and bool(result.get("csrf")))


def restarted(data, pid):
    expected = STATE["exact"]
    c = mcp.MCP(STATE["token"])
    current = ok("drafts.reload_same_revision", draft(c, "get", {"id": expected["id"]}))
    gate.T("drafts.persistence", exact(current) == expected and current["state"] == "approved_offline")
    gate.T("drafts.revocation_applies", mcp.admin("POST", URL + "/revoke",
                                                 {"agent": STATE["aid"], "scope": SCOPE})["status"] == 200
           and draft(c, "get", {"id": expected["id"]}).get("code") == "DRAFT_NOT_FOUND")
    denied = body(mcp.admin("POST", URL + "/decide", {**expected, "decision": "approved_offline"}))
    gate.T("drafts.no_review_replay_after_restart", denied.get("code") == "DRAFT_STATE")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tox", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-clients", action="store_true")
    args = ap.parse_args()
    if any(gate.oslisteners.other_listeners(port, 0) for port in gate.base.PORTS):
        raise SystemExit("A TouchDesigner fixture port is already in use.")
    tox = args.tox.resolve(strict=True)
    work = Path(tempfile.mkdtemp(prefix="td-offline-drafts-", dir=ROOT / ".tmp"))
    project = work / "project"
    first, second = work / "first", work / "restart"
    project.mkdir()
    for run in (first, second):
        run.mkdir()
        shutil.copyfile(HERE / "td_runner_offline_drafts.py", run / "td_runner_offline_drafts.py")
    env = {"TDFLOW_PRIVATE_ROOT": str(work / "private")}
    failures = []

    def fault(data, pid):
        token = STATE.get("token")
        r = draft(mcp.MCP(token), "create", request("create", "x = 1\n", "Audit fault"))
        gate.T("drafts.unavailable_intent_refused", r.get("code") == "AUDIT_UNAVAILABLE")

    initial = gate.launch("offline_drafts", str(tox), str(first), str(project), env,
                          {"setup_ready": mcp.cp_setup_ready, "pair_approved": mcp.cp_pair_approved,
                           "drafts_workflow": lambda _d, _p: exercise(work, project, first, not args.no_clients),
                           "drafts_failed_audit": fault}, deadline_s=660)
    phases = [initial]
    saved = initial.get("drafts_toe")
    if saved and "exact" in STATE:
        mcp.STATE["cookies"], mcp.STATE["csrf"] = {}, None
        phases.append(gate.launch("offline_drafts_restart", str(tox), str(second), str(project), env,
                                  {"setup_ready": mcp.cp_setup_ready, "pair_approved": ready_again,
                                   "drafts_restarted": restarted}, deadline_s=120, toe=saved))
    native = [check for phase in phases for check in phase.get("tests", [])]
    all_checks = gate.H["tests"] + native
    failures.extend(t["name"] for t in all_checks if not t["ok"])
    faults = [fault for phase in phases for fault in phase.get("errors", [])]
    report = {"tox_sha256": hashlib.sha256(tox.read_bytes()).hexdigest(), "tests": all_checks,
              "builds": [p.get("td_build") for p in phases], "errors": faults,
              "claude": STATE.get("claude"), "codex": STATE.get("codex"), "failures": failures}
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**{k: v for k, v in report.items() if k != "tests"}, "checks": len(all_checks)}))
    return 0 if len(phases) == 2 and all(p.get("tests") for p in phases) and not failures and not faults else 1


if __name__ == "__main__":
    raise SystemExit(main())
