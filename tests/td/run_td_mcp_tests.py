"""Phase 1B in-TouchDesigner acceptance suite for the packaged TDflowMCP.tox. Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_mcp_tests.py [--tox build/TDflowMCP.tox]

Two TouchDesigner launches with the same disposable Datadir (under .tmp/, via TDFLOW_DATADIR):
1. ``mcp``: pairing approved inside TD, agent tokens via the paired admin API, Enable MCP, both protocol
   eras, all 16 tools, scoped allow/deny + hard denies, verified undo, interleaving conflict, kill switch
   (incl. a queued request), audit fail-closed/integrity/tamper, revocation, secrets scan.
2. ``mcp_restart``: agents, sessions and the audit chain persist; an undo across the restart is refused.
Results: build/td-mcp-test-results.json. Safety as in run_td_tests.py (own process tree only, 127.0.0.1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_tests as base  # noqa: E402

ROOT, T, H = base.ROOT, base.T, base.H
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402
from tdflowmcp.net import oslisteners  # noqa: E402

O = "http://127.0.0.1:9480"
TOKENS, STATE = {}, {"cookies": {}}
EXPECTED_TOOLS = sorted(["tdflow_approval_status", "tdflow_capture_top", "tdflow_describe_network", "tdflow_lease_scope", "tdflow_experiment_create",
                         "tdflow_experiment_promote", "tdflow_experiment_discard", "tdflow_save_project"] + ["tdflow_connect", "tdflow_create_operator", "tdflow_delete_operator", "tdflow_disconnect",
                         "tdflow_get_dat_text", "tdflow_get_errors", "tdflow_get_operator", "tdflow_get_parameters",
                         "tdflow_list_operators", "tdflow_project_info", "tdflow_session_list", "tdflow_session_note",
                         "tdflow_set_dat_text", "tdflow_set_parameters", "tdflow_undo", "tdflow_whoami",
                         # Phase 2A memory tools (additive; tested in run_td_memory_tests.py)
                         "tdflow_handoff_create", "tdflow_handoff_resume", "tdflow_task_checkpoint",
                         "tdflow_task_list"])
SECRET_BODIES = []          # responses that legitimately contain a token (token creation): excluded from scans


# ---- HTTP helpers ------------------------------------------------------------------------------
def http(method, path, headers=None, body=b"", cookies=True):
    h = dict(headers or {})
    if cookies and STATE["cookies"]:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in STATE["cookies"].items())
    r = base.req(method, path, h, body)
    for sc in r.get("headers", {}).get("set-cookie", []):
        kv = sc.split(";", 1)[0]
        k, _, v = kv.partition("=")
        if "Max-Age=0" in sc:
            STATE["cookies"].pop(k.strip(), None)
        else:
            STATE["cookies"][k.strip()] = v.strip()
    return r


def admin(method, path, obj=None, csrf=True):
    h = {"X-TDflow-Request": "1"}
    if csrf and STATE.get("csrf"):
        h["X-TDflow-CSRF"] = STATE["csrf"]
    if method != "GET":
        h.update({"Origin": O, "Content-Type": "application/json"})
    return http(method, path, h, json.dumps(obj or {}).encode() if method != "GET" else b"")


class MCP:
    def __init__(self, token, legacy="2025-06-18"):
        self.token, self.legacy = token, legacy
        self.n = 0

    def post(self, obj, headers=None, raw=None):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        r = base.req("POST", "/mcp", h, raw if raw is not None else json.dumps(obj).encode())
        return r.get("status"), base.jbody(r), r

    def rpc(self, method, params=None, modern=False, extra=None):
        self.n += 1
        p = dict(params or {})
        h = {}
        if modern:
            p["_meta"] = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                          "io.modelcontextprotocol/clientCapabilities": {},
                          "io.modelcontextprotocol/clientInfo": {"name": "tdflow-harness", "version": "1"}}
            h = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
            if method == "tools/call":
                h["Mcp-Name"] = p.get("name")
        else:
            h = {"MCP-Protocol-Version": self.legacy}
        h.update(extra or {})
        return self.post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": p}, h)

    def call(self, name, args=None, modern=False):
        st, j, r = self.rpc("tools/call", {"name": name, "arguments": args or {}}, modern)
        if st != 200 or not j or "result" not in j:
            return {"_http": st, "_error": (j or {}).get("error"), "_code": base.code(r)}
        res = j["result"]
        sc = dict(res.get("structuredContent") or {})
        sc["_isError"] = res.get("isError")
        return sc


def ok(r):
    return r.get("_isError") is False


def denied(r, codes=("DENIED",), rules=None):
    return r.get("_isError") is True and r.get("code") in codes and (rules is None or r.get("rule_id") in rules)


# ---- checkpoint handlers ----------------------------------------------------------------------
def cp_setup_ready(data, pid):
    r = MCP(None).post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    T("mcp.disabled_before_enable", r[0] == 503 and base.code(r[2]) == "MCP_DISABLED")
    T("admin.agents_need_session", admin("GET", "/setup/api/agents")["status"] == 401)
    T("admin.tokens_need_session", admin("POST", "/setup/api/tokens", {"display_name": "x"}, csrf=False)["status"] == 403)
    bad = http("POST", "/setup/api/pair", {"X-TDflow-Request": "1", "Origin": "http://evil.example"})
    T("pairing.foreign_origin_refused", bad["status"] == 403 and base.code(bad) == "BAD_ORIGIN")
    r = http("POST", "/setup/api/pair", {"X-TDflow-Request": "1", "Origin": O})
    j = base.jbody(r) or {}
    T("pairing.requested", r["status"] == 200 and j.get("state") == "pending" and "tdflow_pair" in STATE["cookies"]
      and any("HttpOnly" in c and "SameSite=Strict" in c for c in r["headers"].get("set-cookie", [])),
      code=j.get("match_code"))
    st = http("GET", "/setup/api/pair/status", {"X-TDflow-Request": "1"})
    T("pairing.pending_until_approved", (base.jbody(st) or {}).get("state") == "pending")
    saved = dict(STATE["cookies"])
    swap = http("POST", "/setup/api/pair", {"X-TDflow-Request": "1", "Origin": O}, cookies=False)
    STATE["cookies"] = saved
    T("pairing.pending_never_replaced", swap["status"] == 409 and base.code(swap) == "PAIRING_PENDING")
    return {"match_code": j.get("match_code")}


def cp_pair_approved(data, pid):
    st = http("GET", "/setup/api/pair/status", {"X-TDflow-Request": "1"})
    j = base.jbody(st) or {}
    STATE["csrf"] = j.get("csrf")
    T("pairing.approved_session", j.get("state") == "approved" and bool(STATE["csrf"]) and "tdflow_admin" in STATE["cookies"])
    again = base.jbody(http("GET", "/setup/api/pair/status", {"X-TDflow-Request": "1"})) or {}
    T("pairing.credentials_handed_out_once", again.get("state") == "unknown" and "csrf" not in again)
    T("admin.agents_ok_with_session", admin("GET", "/setup/api/agents")["status"] == 200)
    T("admin.reads_need_csrf_too", admin("GET", "/setup/api/agents", csrf=False)["status"] == 401)
    no_csrf = admin("POST", "/setup/api/tokens", {"display_name": "x"}, csrf=False)
    T("admin.mutation_needs_csrf", no_csrf["status"] == 403 and base.code(no_csrf) == "MISSING_CSRF")
    saved = STATE["csrf"]
    STATE["csrf"] = "wrong"
    T("admin.wrong_csrf_refused", admin("POST", "/setup/api/tokens", {"display_name": "x"})["status"] == 401)
    STATE["csrf"] = saved
    T("admin.scope_outside_root_refused", base.code(admin("POST", "/setup/api/tokens", {
        "display_name": "x", "role": "builder", "scope": "/sys/**"})) == "SCOPE_OUTSIDE_PROJECT_ROOT")
    T("admin.unknown_role_refused", base.code(admin("POST", "/setup/api/tokens", {
        "display_name": "x", "role": "admin", "scope": "/project1/**"})) == "ROLE_NOT_ALLOWED")
    specs = {"A": ("Agent A (read+update fx)", "builder", "/project1/fx/**", ["read", "update"]),
             "B": ("Agent B (builder fx)", "builder", "/project1/fx/**", None),
             "C": ("Agent C (observer)", "observer", "/project1/**", None),
             "D": ("Agent D (builder root)", "builder", "/project1/**", None),
             "E": ("Agent E (operator fx)", "operator", "/project1/fx/**", None)}
    for k, (name, role, scope, acts) in specs.items():
        body = {"display_name": name, "role": role, "scope": scope}
        if acts:
            body["actions"] = acts
        r = admin("POST", "/setup/api/tokens", body)
        j = base.jbody(r) or {}
        TOKENS[k] = j.get("token")
        SECRET_BODIES.append(r.get("body", b""))
        T(f"admin.token_{k}_created", r["status"] == 200 and str(j.get("token", "")).startswith("tdf_")
          and j.get("shown_once") is True and "no-store" in r["headers"].get("cache-control", [""])[0])
    lst = base.jbody(admin("GET", "/setup/api/agents")) or {}
    T("admin.list_has_no_secrets", len(lst.get("agents", [])) >= 4 and not any(
        TOKENS[k].split(".")[1] in json.dumps(lst) for k in TOKENS if TOKENS[k]))


def cp_enabled(data, pid):
    A, B, C, D = (MCP(TOKENS[k]) for k in "ABCD")
    st, j, r = MCP(None).rpc("tools/list")
    T("auth.no_token_401", st == 401)
    T("auth.bad_token_401", MCP("tdf_AAAAAAAAAAAA." + "x" * 43).rpc("tools/list")[0] == 401)
    T("auth.evil_origin_403", B.rpc("tools/list", extra={"Origin": "http://evil.example"})[0] == 403)
    # legacy era
    st, j, _ = B.post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "h"}}})
    T("proto.legacy_initialize", st == 200 and j["result"]["protocolVersion"] == "2025-06-18"
      and j["result"]["serverInfo"]["name"] == "TDflowMCP")
    st, _, r = B.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    T("proto.notification_202", st == 202 and not r.get("body"))
    st, j, _ = B.rpc("tools/list")
    names = sorted(t["name"] for t in (j or {}).get("result", {}).get("tools", []))
    T("proto.tools_list_28", names == EXPECTED_TOOLS, names=names)   # + tdflow_execute_script once Code Mode is on
    # modern era
    st, j, _ = B.rpc("server/discover", modern=True)
    T("proto.modern_discover", st == 200 and "2026-07-28" in j["result"]["supportedVersions"])
    st, j, _ = B.rpc("tools/list", modern=True)
    T("proto.modern_tools_list", st == 200 and j["result"]["resultType"] == "complete" and len(j["result"]["tools"]) == len(EXPECTED_TOOLS)
      and j["result"].get("ttlMs") == 0 and j["result"].get("cacheScope") == "private")
    st, j, _ = B.rpc("tools/list", modern=True, extra={"Mcp-Method": "tools/call"})
    T("proto.modern_header_mismatch", st == 400 and j["error"]["code"] == -32020)
    st, j, _ = B.rpc("tools/call", {"name": "tdflow_nope", "arguments": {}}, modern=True)
    T("proto.unknown_tool", st == 400 and j["error"]["code"] == -32602)
    T("proto.modern_call", ok(B.call("tdflow_whoami", modern=True)))
    big = b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"' + b"x" * (1024 * 1024 + 10) + b'"}'
    T("limits.body_too_large", B.post(None, {"MCP-Protocol-Version": "2025-06-18"}, raw=big)[0] == 413)
    # identity and reads
    w = B.call("tdflow_whoami")
    T("tool.whoami", ok(w) and w["role"] == "builder" and w["scopes"][0]["scope"] == "/project1/fx/**")
    pi = C.call("tdflow_project_info")
    T("tool.project_info_filtered", ok(pi) and "fx" in pi["top_level"] and "out1" in pi["top_level"]
      and "TDflowMCP" not in pi["top_level"] and "mem" not in pi["top_level"] and pi["filtered"] >= 2,
      top=pi.get("top_level"), filtered=pi.get("filtered"))
    lo = C.call("tdflow_list_operators", {"path": "/project1"})
    nm = [o["name"] for o in lo.get("operators", [])]
    T("tool.list_operators_filtered", ok(lo) and "fx" in nm and "TDflowMCP" not in nm and "mem" not in nm, names=nm)
    go = B.call("tdflow_get_operator", {"path": "/project1/fx/level1"})
    T("tool.get_operator", ok(go) and go["type"] == "levelTOP" and go["family"] == "TOP")
    gp = B.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "limit": 200})
    T("tool.get_parameters", ok(gp) and any(p["name"] == "opacity" for p in gp["parameters"]))
    # Dogfood 1 regressions: a page filter on a Level TOP crashed; a names filter; inputs/position in lists.
    gp2 = B.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "page": "Post"})
    T("dogfood.page_filter_works", ok(gp2) and gp2["parameters"] and all(p["page"] == "Post" for p in gp2["parameters"]),
      result={k: gp2.get(k) for k in ("code", "total")})
    gp3 = B.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "names": ["opacity", "brightness1"]})
    T("dogfood.names_filter", ok(gp3) and sorted(p["name"] for p in gp3["parameters"]) == ["brightness1", "opacity"])
    lo2 = B.call("tdflow_list_operators", {"path": "/project1/fx"})
    ge2 = B.call("tdflow_get_errors", {"path": "/project1/fx"})
    T("dogfood.get_errors_reports_never_cooked", ok(ge2) and isinstance(ge2.get("never_cooked"), list),
      never_cooked=ge2.get("never_cooked"))
    T("dogfood.get_operator_cook_count", "cook_count" in B.call("tdflow_get_operator", {"path": "/project1/fx/level1"}))
    T("dogfood.list_has_inputs_and_position", ok(lo2) and all("inputs" in o and "x" in o and "y" in o
                                                               for o in lo2["operators"]))
    gt = B.call("tdflow_get_dat_text", {"path": "/project1/fx/notes"})
    T("tool.get_dat_text", ok(gt) and gt["text"] == "hello")
    ge = B.call("tdflow_get_errors", {"path": "/project1/fx"})
    T("tool.get_errors", ok(ge) and isinstance(ge["problems"], list))
    T("tool.not_found", B.call("tdflow_get_operator", {"path": "/project1/fx/none"}).get("code") == "NOT_FOUND")
    T("tool.invalid_args", B.call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                            "values": {f"p{i}": 1 for i in range(51)}}).get("code") == "INVALID_ARGS")
    # Acceptance 1B item 1 (agent A: read+update on /project1/fx/**)
    s = A.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.5}})
    T("acc1.update_value_in_scope", ok(s) and s["now"]["opacity"] == 0.5 and s.get("audit_seq") and s.get("undo_label")
      and s.get("reversible") is True, result=s)
    T("acc1.denied_out_of_scope", denied(A.call("tdflow_set_parameters", {"path": "/project1/out1", "values": {"opacity": 1}})))
    T("acc1.denied_delete", denied(A.call("tdflow_delete_operator", {"path": "/project1/fx/level1"})))
    T("acc1.denied_expression", denied(A.call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                                       "expressions": {"opacity": "absTime.seconds"}}),
                                     ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("acc1.denied_execute_dat_text", denied(A.call("tdflow_set_dat_text", {"path": "/project1/fx/exec1", "text": "x"}),
                                           ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("acc1.denied_tdflowmcp_instance", denied(A.call("tdflow_get_operator", {"path": "/project1/TDflowMCP"}), rules=("H3",)))
    T("acc1.denied_tdflowproject", denied(A.call("tdflow_get_dat_text", {"path": "/project1/mem/record1"}), rules=("H4",)))
    # Hard denies even for a broad builder (agent D on /project1/**)
    T("hard.instance_read", denied(D.call("tdflow_list_operators", {"path": "/project1/TDflowMCP"}), rules=("H3",)))
    T("hard.instance_delete", denied(D.call("tdflow_delete_operator", {"path": "/project1/TDflowMCP"}), rules=("H3",)))
    T("hard.ancestor_extension", denied(D.call("tdflow_set_parameters", {"path": "/project1", "values": {"ext0object": "x"}}))
      and D.call("tdflow_set_parameters", {"path": "/project1", "values": {"extension1": "x"}}).get("_isError") is True)
    T("hard.root_delete", denied(D.call("tdflow_delete_operator", {"path": "/project1"})))
    T("hard.tdflowproject_write", denied(D.call("tdflow_set_dat_text", {"path": "/project1/mem/record1", "text": "x"}),
                                         rules=("H4",)))
    T("hard.system_area", denied(D.call("tdflow_get_operator", {"path": "/local"}), rules=("H1",)))
    T("hard.executable_type", denied(D.call("tdflow_create_operator", {"parent": "/project1/fx", "type": "executeDAT",
                                                                     "name": "evil"}), ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("review6.evaluate_dat_not_creatable", denied(D.call("tdflow_create_operator", {
        "parent": "/project1/fx", "type": "evaluateDAT", "name": "ev1"}), rules=("G2",)))
    T("review6.file_in_dat_not_creatable", denied(D.call("tdflow_create_operator", {
        "parent": "/project1/fx", "type": "fileinDAT", "name": "fi1"}), rules=("G2",)))
    T("review6.wire_into_dat_needs_execute", denied(D.call("tdflow_connect", {
        "src": "/project1/fx/table2", "dst": "/project1/fx/dat_sink"}), ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("review6.reference_parameter_needs_execute", denied(D.call("tdflow_set_parameters", {
        "path": "/project1/fx/sel1", "values": {"top": "/project1/TDflowMCP"}}), ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("review6.clone_parameter_denied", denied(D.call("tdflow_set_parameters", {
        "path": "/project1/fx", "values": {"clone": "/project1/out1"}})))
    T("review6.untagged_table_not_writable", denied(D.call("tdflow_set_dat_text", {
        "path": "/project1/fx/table1", "text": "x"}), ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("review6.no_detail_inside_protected", denied(A.call("tdflow_get_parameters", {
        "path": "/project1/TDflowMCP/nothing"}), rules=("H3",)))
    T("review6.trailing_slash_denied", denied(D.call("tdflow_get_operator", {"path": "/project1/fx/"})))
    T("hard.expression_needs_execute", denied(D.call("tdflow_set_parameters", {"path": "/project1/fx/level1",
        "expressions": {"opacity": "1"}}), ("DENIED", "APPROVAL_UNAVAILABLE")))
    T("hard.no_existence_leak_out_of_scope", denied(A.call("tdflow_get_operator", {"path": "/project1/out1/nothing"}))
      and denied(A.call("tdflow_set_parameters", {"path": "/project1/out1", "values": {"nope": 1}})))
    # Verified undo for every reversible kind (agent B)
    s = B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.7}})
    T("undo.other_agent_conflict", A.call("tdflow_undo").get("code") == "UNDO_CONFLICT")   # B's change is on top
    u = B.call("tdflow_undo")
    back = B.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "limit": 200})
    op_val = next((p["value"] for p in back.get("parameters", []) if p["name"] == "opacity"), None)
    T("undo.set_parameters", ok(s) and ok(u) and u["verified"] and op_val == 0.5, undo=u, value=op_val)
    c = B.call("tdflow_create_operator", {"parent": "/project1/fx", "type": "noiseTOP", "name": "n2"})
    u = B.call("tdflow_undo")
    T("undo.create", ok(c) and ok(u) and u["verified"] and
      B.call("tdflow_get_operator", {"path": "/project1/fx/n2"}).get("code") == "NOT_FOUND", undo=u)
    w1 = B.call("tdflow_connect", {"src": "/project1/fx/noise1", "dst": "/project1/fx/level1"})
    ins = B.call("tdflow_get_operator", {"path": "/project1/fx/level1"}).get("inputs")
    u = B.call("tdflow_undo")
    ins2 = B.call("tdflow_get_operator", {"path": "/project1/fx/level1"}).get("inputs")
    T("undo.connect", ok(w1) and ins == ["/project1/fx/noise1"] and ok(u) and u["verified"] and ins2 == [], undo=u)
    B.call("tdflow_connect", {"src": "/project1/fx/noise1", "dst": "/project1/fx/level1"})
    d1 = B.call("tdflow_disconnect", {"src": "/project1/fx/noise1", "dst": "/project1/fx/level1"})
    u = B.call("tdflow_undo")
    T("undo.disconnect", ok(d1) and ok(u) and u["verified"] and
      B.call("tdflow_get_operator", {"path": "/project1/fx/level1"}).get("inputs") == ["/project1/fx/noise1"], undo=u)
    B.call("tdflow_create_operator", {"parent": "/project1/fx", "type": "nullTOP", "name": "n3"})
    dl = B.call("tdflow_delete_operator", {"path": "/project1/fx/n3"})
    gone = B.call("tdflow_get_operator", {"path": "/project1/fx/n3"}).get("code") == "NOT_FOUND"
    u = B.call("tdflow_undo")
    T("undo.delete", ok(dl) and gone and ok(u) and u["verified"] and
      B.call("tdflow_get_operator", {"path": "/project1/fx/n3"}).get("type") == "nullTOP", undo=u)
    t1 = B.call("tdflow_set_dat_text", {"path": "/project1/fx/notes", "text": "changed"})
    mid = B.call("tdflow_get_dat_text", {"path": "/project1/fx/notes"}).get("text")
    u = B.call("tdflow_undo")
    T("undo.dat_text_snapshot", ok(t1) and t1["reversible"] and mid == "changed" and ok(u) and u["verified"] and
      B.call("tdflow_get_dat_text", {"path": "/project1/fx/notes"}).get("text") == "hello", undo=u)
    t2 = B.call("tdflow_set_dat_text", {"path": "/project1/fx/table2", "text": "x\ty"})
    T("tool.tagged_table_dat_text", ok(t2) and t2["reversible"])
    T("undo.nothing_to_undo", C.call("tdflow_undo").get("code") == "NOTHING_TO_UNDO")
    n = B.call("tdflow_session_note", {"text": "acceptance run note"})
    sl = B.call("tdflow_session_list")
    T("tool.session_note_list", ok(n) and ok(sl) and sl["sessions"] and sl["sessions"][0]["notes"] >= 1)
    STATE["sessions_run1"] = [x["session_id"] for x in sl.get("sessions", [])]
    # For the interleaving checkpoint: B's newest change, then a human edit inside TD.
    s = B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.9}})
    T("interleave.agent_change", ok(s))
    shot = os.path.join(ROOT, "build", "setup-assistant-1b.png")
    exe = next((b for b in base.CHROME if os.path.exists(b)), None)
    if exe:
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
                        "--disable-sync", "--disable-background-networking", "--hide-scrollbars",
                        f"--user-data-dir={os.path.join(H['run_dir'], 'browser-profile')}", "--window-size=1100,2600",
                        "--virtual-time-budget=6000", f"--screenshot={shot}", f"{O}/setup/"],
                       capture_output=True, timeout=90)
    T("browser.screenshot_saved", os.path.exists(shot) and os.path.getsize(shot) > 10000, path="build/setup-assistant-1b.png")
    dom = base.headless_dom()
    T("browser.dom_shows_enabled", 'data-state="Enabled"' in dom and 'id="pair"' in dom and 'id="token-form"' in dom)


def cp_human_edited(data, pid):
    B = MCP(TOKENS["B"])
    u = B.call("tdflow_undo")
    v = B.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "limit": 200})
    val = next((p["value"] for p in v.get("parameters", []) if p["name"] == "opacity"), None)
    T("interleave.undo_conflict", u.get("code") == "UNDO_CONFLICT" and val == 0.9, undo=u, value=val)


def cp_suspended(data, pid):
    B = MCP(TOKENS["B"])
    st, j, r = B.rpc("tools/call", {"name": "tdflow_whoami", "arguments": {}})
    T("kill.suspended_503", st == 503 and base.code(r) == "SUSPENDED")
    T("kill.tools_list_503", B.rpc("tools/list")[0] == 503)


def cp_resumed(data, pid):
    T("kill.resumed", ok(MCP(TOKENS["B"]).call("tdflow_whoami")))
    approval_flow()


def approved(E, tool, args):
    """Request, approve on the dashboard, then repeat the identical call. Returns (request, result)."""
    r = E.call(tool, args)
    if r.get("code") != "APPROVAL_REQUIRED":
        return r, r
    admin("POST", "/api/approvals/decide", {"id": r.get("approval_id"), "decision": "approved"})
    return r, E.call(tool, args)


def code_dat_flow(E):
    """Owner D20: staged code. An Execute DAT is created switched off (approved), gets its code (approved), is
    switched on (approved), and the code runs; switching it off is approval-gated too."""
    B = MCP(TOKENS["B"])
    m = B.call("tdflow_create_operator", {"parent": "/project1/fx", "type": "textDAT", "name": "marker"})
    T("code.marker_created", ok(m), r=m)
    T("code.builder_cannot_request_execute_dat", B.call("tdflow_create_operator", {
        "parent": "/project1/fx", "type": "executeDAT", "name": "ex_b"}).get("rule_id") in ("G2", "R1"))
    T("code.network_dat_never_approvable", E.call("tdflow_create_operator", {
        "parent": "/project1/fx", "type": "webclientDAT", "name": "w"}).get("rule_id") == "G2")
    req, c = approved(E, "tdflow_create_operator", {"parent": "/project1/fx", "type": "executeDAT", "name": "ex1"})
    T("code.EXECUTE_DAT_NEEDS_APPROVAL", req.get("code") == "APPROVAL_REQUIRED" and "switched off" in json.dumps(req.get("reasons")),
      r=req)
    T("code.EXECUTE_DAT_CREATED_STAGED_INACTIVE", ok(c) and c.get("staged_inactive") is True, r=c)
    code = ("def onFrameStart(frame):\n\top('/project1/fx/marker').text = 'ran ' + str(frame)\n\treturn\n")
    req, w = approved(E, "tdflow_set_dat_text", {"path": "/project1/fx/ex1", "text": code})
    T("code.CODE_TEXT_NEEDS_APPROVAL", req.get("code") == "APPROVAL_REQUIRED" and req.get("rule_id") == "A1", r=req)
    T("code.code_text_written", ok(w), r=w)
    t = E.call("tdflow_get_dat_text", {"path": "/project1/fx/ex1"})
    T("code.text_is_exactly_the_approved_code", t.get("text", "").rstrip("\n") == code.rstrip("\n"), text=t.get("text"))
    time.sleep(0.5)
    T("code.not_running_while_inactive", "ran" not in E.call("tdflow_get_dat_text", {"path": "/project1/fx/marker"}).get("text", ""))
    ex = {"path": "/project1/fx/ex1", "values": {"framestart": True, "active": True}}
    req, a = approved(E, "tdflow_set_parameters", ex)
    T("code.ACTIVATION_NEEDS_APPROVAL", req.get("code") == "APPROVAL_REQUIRED" and "switches active" in json.dumps(req.get("reasons")),
      r=req)
    T("code.activated", ok(a), r=a)
    time.sleep(1.0)
    mk = E.call("tdflow_get_dat_text", {"path": "/project1/fx/marker"}).get("text", "")
    T("code.APPROVED_CODE_RUNS", mk.startswith("ran "), marker=mk)
    T("code.builder_cannot_switch_it", B.call("tdflow_set_parameters", {"path": "/project1/fx/ex1", "values": {"active": False}})
      .get("rule_id") == "R1")
    # Review 14: callback toggles decide when code runs; they are execute changes too, not plain updates.
    T("code.BUILDER_CANNOT_TOGGLE_CALLBACKS", B.call("tdflow_set_parameters", {
        "path": "/project1/fx/ex1", "values": {"frameend": True}}).get("rule_id") == "R1")
    tg = E.call("tdflow_set_parameters", {"path": "/project1/fx/ex1", "values": {"frameend": True}})
    T("code.CALLBACK_TOGGLE_NEEDS_APPROVAL", tg.get("code") == "APPROVAL_REQUIRED" and "decides when its code runs"
      in json.dumps(tg.get("reasons")), r=tg)
    req, off = approved(E, "tdflow_set_parameters", {"path": "/project1/fx/ex1", "values": {"active": False}})
    T("code.deactivation_is_approval_gated_and_works", req.get("code") == "APPROVAL_REQUIRED" and ok(off), r=off)
    time.sleep(0.5)
    a1 = E.call("tdflow_get_dat_text", {"path": "/project1/fx/marker"}).get("text", "")
    time.sleep(0.6)
    T("code.stopped_after_deactivation", E.call("tdflow_get_dat_text", {"path": "/project1/fx/marker"}).get("text", "") == a1)
    d = B.call("tdflow_delete_operator", {"path": "/project1/fx/ex1"})
    T("code.staged_operator_removable", ok(d), r=d)
    B.call("tdflow_delete_operator", {"path": "/project1/fx/marker"})


def script_flow(E):
    """Phase 3 Code Mode: one approved script makes several facade calls in TouchDesigner, each audited on its own,
    inside its scope; out-of-scope and approval-needing calls are refused; a builder cannot run scripts."""
    B = MCP(TOKENS["B"])
    T("script.CODE_MODE_OFF_BY_DEFAULT", E.call("tdflow_execute_script", {"scope": "/project1/fx", "code": "result = 1"})
      .get("code") == "CODE_MODE_OFF")
    s = admin("POST", "/api/settings", {"code_mode": True})
    st = base.jbody(admin("GET", "/api/settings")) or {}
    T("script.user_turns_code_mode_on", s["status"] == 200 and (st.get("code_mode") or {}).get("enabled") is True, st=st)
    _, lj, _ = E.rpc("tools/list")
    T("script.listed_once_on", "tdflow_execute_script" in [t["name"] for t in ((lj or {}).get("result") or {}).get("tools", [])])
    T("script.builder_denied", B.call("tdflow_execute_script", {"scope": "/project1/fx", "code": "result = 1"})
      .get("rule_id") == "R1")
    T("script.import_refused_before_request", E.call("tdflow_execute_script", {
        "scope": "/project1/fx", "code": "import os"}).get("code") == "SCRIPT_REFUSED")
    code = ("names = [o['path'] for o in tdflow.list_operators(path='/project1/fx')['operators']]\n"
            "for i, v in enumerate((0.2, 0.4)):\n"
            "    tdflow.set_parameters(path='/project1/fx/level1', values={'opacity': v})\n"
            "print('ops', len(names))\n"
            "result = tdflow.get_parameters(path='/project1/fx/level1', names=['opacity'])['parameters'][0]['value']")
    args = {"scope": "/project1/fx", "code": code}
    req, r = approved(E, "tdflow_execute_script", args)
    T("script.NEEDS_APPROVAL_OF_THE_TEXT", req.get("code") == "APPROVAL_REQUIRED" and "Code Mode script"
      in json.dumps(req.get("reasons")), r=req)
    T("script.RUNS_FACADE_CALLS", ok(r) and r.get("result") == 0.4 and r.get("calls") == 4
      and r.get("output", "").startswith("ops "), r=r)
    T("script.each_change_audited", len([x for x in (r.get("log") or []) if x.get("audit_seq")]) == 2, log=r.get("log"))
    T("script.single_use", E.call("tdflow_execute_script", args).get("code") == "APPROVAL_REQUIRED")
    req, o = approved(E, "tdflow_execute_script", {"scope": "/project1/fx",
                                                   "code": "tdflow.get_operator(path='/project1/other')"})
    T("script.OUT_OF_SCOPE_REFUSED", o.get("code") == "SCOPE_OUTSIDE_SCRIPT", r=o)
    req, x = approved(E, "tdflow_execute_script", {"scope": "/project1/fx", "code":
                      "tdflow.set_parameters(path='/project1/fx/level1', expressions={'opacity': '0.1'})"})
    T("script.NO_HIDDEN_APPROVALS", x.get("code") == "APPROVAL_REQUIRED_IN_SCRIPT", r=x)
    u = E.call("tdflow_undo")
    T("script.changes_undoable_one_by_one", ok(u) and u.get("verified"), r=u)
    # ADR-0004 benchmark: the same 20 parameter changes as separate calls and as one approved script. Measured: wall
    # time of the calls and the bytes an agent sends and reads (arguments + structured results); not model tokens.
    n, ind_bytes, t0 = 20, 0, time.perf_counter()
    for i in range(n):
        a = {"path": "/project1/fx/level1", "values": {"opacity": round(0.3 + i * 0.01, 2)}}
        r = E.call("tdflow_set_parameters", a)
        ind_bytes += len(json.dumps({"name": "tdflow_set_parameters", "arguments": a})) + len(json.dumps(r))
    ind_ms = (time.perf_counter() - t0) * 1000
    code = (f"for i in range({n}):\n"
            "    tdflow.set_parameters(path='/project1/fx/level1', values={'opacity': round(0.3 + i * 0.01, 2)})\n"
            "result = 'done'")
    bargs = {"scope": "/project1/fx", "code": code}
    req = E.call("tdflow_execute_script", bargs)
    admin("POST", "/api/approvals/decide", {"id": req.get("approval_id"), "decision": "approved"})
    t0 = time.perf_counter()
    r = E.call("tdflow_execute_script", bargs)
    sc_ms = (time.perf_counter() - t0) * 1000
    sc_bytes = len(json.dumps({"name": "tdflow_execute_script", "arguments": bargs})) + len(json.dumps(r))
    T("benchmark.CODE_MODE_MEASURED", ok(r) and r.get("calls") == n, individual={"calls": n, "ms": round(ind_ms, 1),
      "bytes": ind_bytes}, script={"calls": 1, "facade_calls": r.get("calls"), "ms": round(sc_ms, 1), "bytes": sc_bytes,
      "plus": "one approval by the user"})


def experiment_flow(E):
    """Phase 3: an experiment is a copy to try changes on; promote replaces the original (undoable); discard deletes
    only experiments; code inside a copy needs approval; saving the project is approval-only."""
    B = MCP(TOKENS["B"])
    B.call("tdflow_create_operator", {"parent": "/project1/fx", "type": "containerCOMP", "name": "exp_src"})
    B.call("tdflow_create_operator", {"parent": "/project1/fx/exp_src", "type": "noiseTOP", "name": "n1"})
    amp0 = (B.call("tdflow_get_parameters", {"path": "/project1/fx/exp_src/n1", "names": ["amp"]}).get("parameters") or [{}])[0].get("value")
    c = B.call("tdflow_experiment_create", {"path": "/project1/fx/exp_src", "name": "exp_try"})
    T("experiment.CREATED_AS_A_COPY", ok(c) and c.get("operators") == 2 and c.get("copied_from") == "/project1/fx/exp_src", r=c)
    B.call("tdflow_set_parameters", {"path": "/project1/fx/exp_try/n1", "values": {"amp": 0.25}})
    a = (B.call("tdflow_get_parameters", {"path": "/project1/fx/exp_src/n1", "names": ["amp"]}).get("parameters") or [{}])[0]
    T("experiment.original_untouched", a.get("value") == amp0, a=a)
    T("experiment.discard_only_experiments", B.call("tdflow_experiment_discard", {"path": "/project1/fx/exp_src"})
      .get("code") == "INVALID_ARGS")
    p = B.call("tdflow_experiment_promote", {"path": "/project1/fx/exp_try"})
    a = (B.call("tdflow_get_parameters", {"path": "/project1/fx/exp_src/n1", "names": ["amp"]}).get("parameters") or [{}])[0]
    T("experiment.PROMOTED", ok(p) and a.get("value") == 0.25 and
      B.call("tdflow_get_operator", {"path": "/project1/fx/exp_try"}).get("code") == "NOT_FOUND", r=p, a=a)
    u = B.call("tdflow_undo")
    a = (B.call("tdflow_get_parameters", {"path": "/project1/fx/exp_src/n1", "names": ["amp"]}).get("parameters") or [{}])[0]
    T("experiment.PROMOTE_UNDONE", ok(u) and u.get("verified") and a.get("value") == amp0 and
      ok(B.call("tdflow_get_operator", {"path": "/project1/fx/exp_try"})), r=u, a=a)
    d = B.call("tdflow_experiment_discard", {"path": "/project1/fx/exp_try"})
    T("experiment.DISCARDED", ok(d) and B.call("tdflow_get_operator", {"path": "/project1/fx/exp_try"}).get("code") == "NOT_FOUND", r=d)
    sn = B.call("tdflow_experiment_create", {"path": "/project1/fx/exp_src", "name": "snap1", "inert": True})
    T("snapshot.CREATED_INERT", ok(sn) and sn.get("inert") is True, r=sn)
    B.call("tdflow_set_parameters", {"path": "/project1/fx/exp_src/n1", "values": {"amp": 0.75}})
    rs = B.call("tdflow_experiment_promote", {"path": "/project1/fx/snap1"})
    a = (B.call("tdflow_get_parameters", {"path": "/project1/fx/exp_src/n1", "names": ["amp"]}).get("parameters") or [{}])[0]
    T("snapshot.RESTORED", ok(rs) and a.get("value") == amp0, r=rs, a=a)
    approved(E, "tdflow_set_parameters", {"path": "/project1/fx/exp_src/n1", "expressions": {"amp": "0.5"}})
    T("experiment.builder_cannot_copy_code", B.call("tdflow_experiment_create", {
        "path": "/project1/fx/exp_src", "name": "exp_b"}).get("rule_id") == "R1")
    req, c2 = approved(E, "tdflow_experiment_create", {"path": "/project1/fx/exp_src", "name": "exp_e"})
    T("experiment.CODE_IN_A_COPY_NEEDS_APPROVAL", req.get("code") == "APPROVAL_REQUIRED" and "expression or bind on parameter amp"
      in json.dumps(req.get("reasons")) and ok(c2), r=req)
    B.call("tdflow_delete_operator", {"path": "/project1/fx/exp_e"})
    B.call("tdflow_delete_operator", {"path": "/project1/fx/exp_src"})
    # Save: approval only, and only with a grant on the project root (added and removed by the user here).
    T("save.scoped_agent_denied", E.call("tdflow_save_project").get("code") == "DENIED")
    aid = E.call("tdflow_whoami").get("agent_id")
    g = base.jbody(admin("POST", "/api/grants/add", {"agent_id": aid, "scope": "/project1/**", "actions": ["execute"]})) or {}
    req, s = approved(E, "tdflow_save_project", {})
    T("save.NEEDS_APPROVAL", req.get("code") == "APPROVAL_REQUIRED" and "cannot be undone" in json.dumps(req.get("reasons")), r=req)
    T("save.SAVED_OR_NEVER_SAVED", ok(s) or (s.get("code") == "INVALID_ARGS" and "never been saved" in s.get("message", "")), r=s)
    admin("POST", "/api/grants/remove", {"agent_id": aid, "grant_id": g.get("grant_id")})
    T("save.grant_removed_again", E.call("tdflow_save_project").get("code") == "DENIED")


def lease_flow(E):
    """Phase 4: a scope lease refuses another agent's changes inside it (reads stay open) until it is released."""
    B = MCP(TOKENS["B"])
    le = B.call("tdflow_lease_scope", {"scope": "/project1/fx", "ttl_s": 60})
    T("lease.TAKEN", ok(le) and le.get("leased") == "/project1/fx", r=le)
    ov = base.jbody(admin("GET", "/api/overview")) or {}
    T("lease.shown_on_dashboard", any(x.get("scope") == "/project1/fx" for x in ov.get("leases") or []), leases=ov.get("leases"))
    r = E.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.6}})
    T("lease.OTHER_AGENT_REFUSED", r.get("code") == "LEASED" and r.get("rule_id") == "L2", r=r)
    T("lease.reads_stay_open", ok(E.call("tdflow_get_operator", {"path": "/project1/fx/level1"})))
    B.call("tdflow_lease_scope", {"scope": "/project1/fx", "release": True})
    T("lease.RELEASED", ok(E.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.6}})))
    B.call("tdflow_lease_scope", {"scope": "/project1/fx", "ttl_s": 600})
    rl = admin("POST", "/api/leases/release", {"scope": "/project1/fx"})
    T("lease.USER_ENDS_A_LEASE", rl["status"] == 200 and ok(E.call("tdflow_set_parameters", {
        "path": "/project1/fx/level1", "values": {"opacity": 0.6}})), status=rl["status"])
    T("lease.project_root_refused", MCP(TOKENS["D"]).call("tdflow_lease_scope", {"scope": "/project1"}).get("code") == "DENIED")


def network_flow(B):
    """Phase 4: a bounded, read-only network summary with heuristic hints; it reads facts only."""
    d = B.call("tdflow_describe_network", {"path": "/project1/fx"})
    T("network.DESCRIBES_ONE_LEVEL", ok(d) and d.get("shown", 0) >= 1 and isinstance(d.get("edges"), list)
      and isinstance(d.get("hints"), list) and "by_family" in d, d={k: d.get(k) for k in ("shown", "total", "by_family", "code")})
    T("network.only_readable_operators", all(o["path"].startswith("/project1/fx/") for o in d.get("operators", [])))
    st, j, _ = B.rpc("tools/call", {"name": "tdflow_capture_top", "arguments": {"path": "/project1/fx/noise1"}})
    content = ((j or {}).get("result") or {}).get("content") or []
    img = next((c for c in content if c.get("type") == "image"), {})
    import base64 as _b64
    raw = _b64.b64decode(img.get("data", "") or "")
    T("capture.IMAGE_CONTENT_PNG", st == 200 and img.get("mimeType") == "image/png" and raw[:4] == b"\x89PNG",
      bytes=len(raw), sc=((j or {}).get("result") or {}).get("structuredContent"))
    T("capture.not_in_structured_or_audit", "_image" not in json.dumps(((j or {}).get("result") or {}).get("structuredContent") or {}))
    T("capture.non_top_refused", B.call("tdflow_capture_top", {"path": "/project1/fx"}).get("code") in ("INVALID_ARGS", "DENIED"))
    T("network.out_of_scope_denied", B.call("tdflow_describe_network", {"path": "/project1"}).get("code") == "DENIED")


def approval_flow():
    """Phase 2B (ADR-0005 gate): an approval executes only on the identical re-call, with targets and policy
    re-checked. Driven through the real MCP endpoint and the paired dashboard API."""
    E = MCP(TOKENS["E"])
    args = {"path": "/project1/fx/level1", "expressions": {"opacity": "0.25 + 0.5"}}
    r = E.call("tdflow_set_parameters", args)
    T("approval.REQUEST_CREATED_NOTHING_EXECUTED", r.get("code") == "APPROVAL_REQUIRED" and r.get("state") == "pending"
      and r.get("rule_id") == "A1", result=r)
    aid = r.get("approval_id")
    p = (E.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "names": ["opacity"]}).get("parameters") or [{}])[0]
    T("approval.parameter_unchanged_while_pending", p.get("mode") == "CONSTANT", par=p)
    T("approval.recall_while_pending_refused", E.call("tdflow_set_parameters", args).get("code") == "APPROVAL_PENDING")
    lst = base.jbody(admin("GET", "/api/approvals")) or {}
    row = next((a for a in lst.get("approvals", []) if a.get("id") == aid), {})
    T("approval.dashboard_shows_complete_arguments", json.loads(row.get("args") or "{}") == args
      and ["execute", "/project1/fx/level1"] in [t[:2] for t in row.get("targets", [])], row=row)
    T("approval.decide_needs_csrf", admin("POST", "/api/approvals/decide", {"id": aid, "decision": "approved"}, csrf=False)["status"] == 403)
    d = admin("POST", "/api/approvals/decide", {"id": aid, "decision": "approved"})
    T("approval.approved_on_dashboard", d["status"] == 200, status=d["status"])
    other = E.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "expressions": {"opacity": "1"}})
    T("approval.DIFFERENT_CALL_NOT_COVERED", other.get("code") == "APPROVAL_REQUIRED" and other.get("approval_id") != aid, result=other)
    ran = E.call("tdflow_set_parameters", args)
    T("approval.IDENTICAL_RECALL_RUNS", ok(ran) and ran.get("expressions_set") == {"opacity": "0.25 + 0.5"}, result=ran)
    p = (E.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "names": ["opacity"]}).get("parameters") or [{}])[0]
    T("approval.expression_set_in_td", p.get("mode") == "EXPRESSION" and p.get("expr") == "0.25 + 0.5"
      and abs(float(p.get("value") or 0) - 0.75) < 1e-6, par=p)
    T("approval.SINGLE_USE", E.call("tdflow_set_parameters", args).get("code") == "APPROVAL_REQUIRED")
    u = E.call("tdflow_undo")
    p = (E.call("tdflow_get_parameters", {"path": "/project1/fx/level1", "names": ["opacity"]}).get("parameters") or [{}])[0]
    T("approval.undo_restores_constant", ok(u) and u.get("verified") is True and p.get("mode") == "CONSTANT", undo=u, par=p)
    T("approval.builder_still_has_no_execute", MCP(TOKENS["B"]).call("tdflow_set_parameters", args).get("rule_id") == "R1")
    ev = base.jbody(admin("POST", "/api/audit/search", {"tool": "tdflow_set_parameters", "limit": 50})) or {}
    kinds = {(e["kind"], e.get("approval_id")) for e in ev.get("events", [])}
    T("approval.execution_audited_with_its_approval", ("intent", aid) in kinds and ("outcome", aid) in kinds, n=len(kinds))
    code_dat_flow(E)
    script_flow(E)
    network_flow(MCP(TOKENS["B"]))
    experiment_flow(E)
    lease_flow(E)
    # Dashboard: overview, live tail and a verified report.
    ov = base.jbody(admin("GET", "/api/overview")) or {}
    T("dashboard.overview", ov.get("mcp") is True and ov.get("cooking") is True and (ov.get("audit") or {}).get("ok") is True, ov=ov)
    tail = base.jbody(admin("POST", "/api/audit/search", {"after": max(0, int(ov.get("last_seq", 0)) - 3)})) or {}
    T("dashboard.live_tail", len(tail.get("events", [])) >= 1 and all(e["seq"] > int(ov["last_seq"]) - 3 for e in tail["events"]))
    T("dashboard.bad_query_refused", admin("POST", "/api/audit/search", {"agent": "x' OR 1=1"})["status"] == 400)
    rep = base.jbody(admin("POST", "/api/report", {"from_seq": 1, "to_seq": int(ov.get("last_seq", 1))})) or {}
    T("dashboard.report_built", (rep.get("report") or {}).get("range", {}).get("events", 0) > 10
      and "verification" in rep.get("report", {}) and "# TDflowMCP audit report" in rep.get("markdown", ""))
    T("dashboard.mandatory_audit_cannot_be_disabled", admin("POST", "/api/settings", {"security_audit": False})["status"] == 400)


def cp_queue_block(data, pid):
    out = {}

    def later():
        time.sleep(0.5)
        out["r"] = MCP(TOKENS["B"]).rpc("tools/call", {"name": "tdflow_set_parameters", "arguments": {
            "path": "/project1/fx/level1", "values": {"opacity": 0.33}}})
    th = threading.Thread(target=later)
    th.start()
    STATE["queue"] = (th, out)


def cp_queue_checked(data, pid):
    th, out = STATE.pop("queue")
    th.join(20)
    st, j, r = out.get("r", (None, None, {}))
    sc = ((j or {}).get("result") or {}).get("structuredContent") or {}
    # Audit-worker build: the queued plan step is dropped at dispatch and the engine answers with an audited
    # SUSPENDED tool error; a request still waiting for the HTTP layer gets 503 SUSPENDED. Both: not executed.
    T("kill.queued_request_rejected", (st == 503 and base.code(r) == "SUSPENDED") or
      (st == 200 and sc.get("code") == "SUSPENDED" and (j or {}).get("result", {}).get("isError") is True), status=st)


def cp_audit_down(data, pid):
    B = MCP(TOKENS["B"])
    T("audit.fail_closed_mutation", B.call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                                     "values": {"opacity": 0.11}}).get("code") == "AUDIT_UNAVAILABLE")
    r = B.call("tdflow_get_operator", {"path": "/project1/fx/level1"})
    # Fail closed either way: the call itself fails, TDflowMCP already turned MCP off (worker failed), or MCP was
    # being turned off while the call waited, so TouchDesigner refused the main-thread step (nothing ran, no audit
    # event; observed once, 2026-11-22).
    T("audit.fail_closed_read", r.get("code") in ("AUDIT_UNAVAILABLE", "MAIN_UNAVAILABLE") or r.get("_code") == "MCP_DISABLED",
      result=r)


def cp_audit_up(data, pid):
    B = MCP(TOKENS["B"])
    s = B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.6}})
    T("audit.works_after_reopen", ok(s) and s.get("audit_seq"))


def cp_scan(data, pid):
    secrets_ = [t.split(".", 1)[1] for t in TOKENS.values() if t]
    texts = {"params": json.dumps(data["params"]), "tell": data["tell"], "tellagent_dat": data["tellagent_dat"],
             "events": json.dumps(data["events"]), "token_rows": json.dumps(data["token_rows"]),
             "diagnostics": json.dumps(data["diagnostics"])}
    hits = [k for k, v in texts.items() for s in secrets_ if s in v]
    T("secrets.no_token_in_params_text_audit_db_rows", not hits, hits=hits)
    T("secrets.tokens_hashed_with_salt", all(len(r[1]) == 32 and len(r[2]) == 64 for r in data["token_rows"]))
    bodies = [b for b in H["bodies"] if b not in SECRET_BODIES]
    T("secrets.no_token_in_other_http_responses", not any(s.encode() in b for b in bodies for s in secrets_))
    base.scan({"params": texts["params"], "tell": texts["tell"], "diag": texts["diagnostics"]}, "environment_details")


def cp_revoked(data, pid):
    lst = base.jbody(admin("GET", "/setup/api/agents")) or {}
    a_id = next(a for a in lst["agents"] if a["display_name"].startswith("Agent A"))
    tid = a_id["tokens"][0]["token_id"]
    r = admin("POST", "/setup/api/tokens/revoke", {"token_id": tid})
    T("revoke.ok", r["status"] == 200)
    T("revoke.immediate_401", MCP(TOKENS["A"]).rpc("tools/list")[0] == 401)
    T("revoke.others_unaffected", ok(MCP(TOKENS["B"]).call("tdflow_whoami")))


def cp_disabled(data, pid):
    st, j, r = MCP(TOKENS["B"]).rpc("tools/list")
    T("disable.mcp_disabled", st == 503 and base.code(r) == "MCP_DISABLED")
    old = dict(STATE["cookies"])
    T("admin.logout", admin("POST", "/setup/api/logout")["status"] == 200 and "tdflow_admin" not in STATE["cookies"])
    STATE["cookies"] = old                                    # replay the old cookie: must be dead server-side
    T("admin.logout_ends_server_session", admin("GET", "/setup/api/agents")["status"] == 401)
    STATE["cookies"] = {}


def cp_restart_enabled(data, pid):
    B = MCP(TOKENS["B"])
    w = B.call("tdflow_whoami")
    T("restart.token_still_valid", ok(w) and w["display_name"].startswith("Agent B"))
    T("restart.revoked_token_still_revoked", MCP(TOKENS["A"]).rpc("tools/list")[0] == 401)
    sl = B.call("tdflow_session_list")
    ids = [x["session_id"] for x in sl.get("sessions", [])]
    T("restart.sessions_persist", ok(sl) and any(s in ids for s in STATE.get("sessions_run1", [])), ids=ids[:5])
    u = B.call("tdflow_undo")
    T("restart.undo_across_restart_refused", u.get("code") == "UNDO_CONFLICT", undo=u)


def cp_minimized(data, pid):
    """Spike S9: a minimized TouchDesigner runs no frames. Calls must fail fast with a clear, retryable error,
    nothing may execute, the listener must survive the 20 s orphan watchdog, and everything must work again
    after the restore without re-enabling MCP. Only this harness's own TouchDesigner window is touched."""
    import tdrun
    td = tdrun.PROCS.get(pid)
    B = MCP(TOKENS["B"])
    T("minimized.works_before", td is not None and ok(B.call("tdflow_set_parameters", {
        "path": "/project1/fx/level1", "values": {"opacity": 0.10}})))
    if td is None:
        return
    td.keep_running = False                         # the tender thread must not restore the window now
    try:
        T("minimized.window_minimized", td.set_minimized(True))
        time.sleep(2.8)                             # beyond not_cooking_after (2 s)
        t0 = time.perf_counter()
        r = B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.11}})
        ms = (time.perf_counter() - t0) * 1000
        T("minimized.MUTATION_REFUSED_FAST_WITH_A_CLEAR_REASON", r.get("_isError") is True
          and r.get("code") == "TDFLOW_NOT_COOKING" and "minimized" in r.get("message", "") and ms < 3000,
          result=r, ms=round(ms))
        rd = B.call("tdflow_get_operator", {"path": "/project1/fx/level1"})
        T("minimized.read_refused_the_same_way", rd.get("_isError") is True and rd.get("code") == "TDFLOW_NOT_COOKING", result=rd)
        st, j, _ = B.rpc("tools/list")
        T("minimized.tools_list_still_answers", st == 200 and len(((j or {}).get("result") or {}).get("tools", [])) >= 16, status=st)
        s = http("GET", "/setup/api/status", {"X-TDflow-Request": "1"})
        T("minimized.status_api_says_not_cooking", s["status"] == 503 and base.code(s) == "TDFLOW_NOT_COOKING", status=s["status"])
        T("minimized.static_setup_page_still_served", http("GET", "/setup/")["status"] == 200)
        time.sleep(23.0)                            # beyond the 20 s orphan watchdog
        T("minimized.LISTENER_SURVIVES_THE_ORPHAN_WATCHDOG", bool(oslisteners.other_listeners(9480, 0)))
        late = B.call("tdflow_get_operator", {"path": "/project1/fx/level1"})
        T("minimized.still_a_clear_refusal_after_25_s", late.get("code") == "TDFLOW_NOT_COOKING", result=late)
    finally:
        restored = td.set_minimized(False)
        td.keep_running = True
    T("minimized.window_restored", restored)
    time.sleep(1.5)
    r = B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.12}})
    T("minimized.WORKS_AGAIN_AFTER_RESTORE_WITHOUT_REENABLING", ok(r)
      and abs((r.get("previous") or {}).get("opacity", -1) - 0.10) < 1e-6, result=r)   # 0.11 never landed


# ---- UX assignment: the TouchDesigner-only workflow and one-click browser sign-in --------------------------------
def clipboard():
    """Read the Windows clipboard as a user's paste would (the token never passes through test files)."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard"],
                       capture_output=True, text=True, timeout=20)
    return (r.stdout or "").strip()


NATIVE = {}


def cp_native_token(data, pid):
    tok = clipboard()
    NATIVE["token"] = tok
    a = MCP(tok)
    a.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "x"}})
    w = a.call("tdflow_whoami")
    T("native.TOKEN_FROM_TD_CONNECTS", ok(w) and w.get("role") == "observer"
      and w.get("scopes", [{}])[0].get("scope") == "/project1/fx/**", role=w.get("role"))
    T("native.observer_cannot_change", denied(a.call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                                              "values": {"opacity": 0.4}})))
    return {"agent_id": w.get("agent_id")}


def cp_native_rotated(data, pid):
    new = clipboard()
    T("native.ROTATED_OLD_TOKEN_REVOKED", MCP(NATIVE["token"]).rpc("tools/list")[0] == 401)
    T("native.ROTATED_NEW_TOKEN_WORKS", new != NATIVE["token"] and ok(MCP(new).call("tdflow_whoami")))
    NATIVE["token"] = new
    return {}


def cp_native_disabled(data, pid):
    T("native.DISABLED_AGENT_REFUSED", MCP(NATIVE["token"]).rpc("tools/list")[0] == 401)
    return {}


def cp_native_approval_requested(data, pid):
    E = MCP(TOKENS["E"])
    NATIVE["args"] = {"path": "/project1/fx/level1", "expressions": {"opacity": "0.31 + 0.02"}}
    r = E.call("tdflow_set_parameters", NATIVE["args"])
    T("native.approval_requested", r.get("code") == "APPROVAL_REQUIRED", r=r)
    return {"expr": "0.31 + 0.02"}


def cp_native_approved(data, pid):
    r = MCP(TOKENS["E"]).call("tdflow_set_parameters", NATIVE["args"])
    T("native.APPROVED_IN_TD_RUNS", ok(r), r=r)
    MCP(TOKENS["E"]).call("tdflow_undo")
    return {}


def cp_web_pair_request(data, pid):
    """Owner D22: the page TouchDesigner opened pairs (no secret in its URL); the retired ticket route is refused."""
    h = {"X-TDflow-Request": "1", "Origin": O, "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
    old = base.req("POST", "/setup/api/pair/ticket", h, json.dumps({"ticket": "x" * 43}).encode())
    T("web.TICKET_ROUTE_RETIRED", old["status"] in (401, 403, 404, 410), status=old["status"])
    STATE["cookies"].pop("tdflow_pair", None)
    r = http("POST", "/setup/api/pair", {"X-TDflow-Request": "1", "Origin": O, "Sec-Fetch-Site": "same-origin"})
    j = base.jbody(r) or {}
    T("web.page_pairing_requested", r["status"] == 200 and j.get("state") == "pending", status=r["status"])
    return {"match_code": j.get("match_code")}


def cp_web_paired(data, pid):
    st = http("GET", "/setup/api/pair/status", {"X-TDflow-Request": "1"})
    j = base.jbody(st) or {}
    T("web.SIGNED_IN_AFTER_APPROVAL_IN_TD", j.get("state") == "approved" and bool(j.get("csrf")), state=j.get("state"))
    STATE["csrf"] = j.get("csrf")
    ov = admin("GET", "/api/overview")
    T("web.SIGNED_IN_DASHBOARD_WORKS", ov["status"] == 200, status=ov["status"])
    w = admin("POST", "/api/settings", {"diagnostic_log": False})
    T("web.changes_allowed_at_once", w["status"] == 200, status=w["status"])     # no warm-up needed any more
    T("web.no_session_resume_endpoint", http("GET", "/setup/api/session", {"X-TDflow-Request": "1"})
      ["status"] in (401, 403, 404))
    return {}


def cp_web_pair_untriggered(data, pid):
    STATE["cookies"].pop("tdflow_pair", None)
    r = http("POST", "/setup/api/pair", {"X-TDflow-Request": "1", "Origin": O})
    T("web.untriggered_request_is_pending_only", r["status"] == 200, status=r["status"])
    return {}


HANDLERS = {"setup_ready": cp_setup_ready, "pair_approved": cp_pair_approved, "enabled": cp_enabled,
            "native_token": cp_native_token, "native_rotated": cp_native_rotated, "native_disabled": cp_native_disabled,
            "native_approval_requested": cp_native_approval_requested, "native_approved": cp_native_approved,
            "web_pair_request": cp_web_pair_request, "web_paired": cp_web_paired,
            "web_pair_untriggered": cp_web_pair_untriggered,
            "human_edited": cp_human_edited, "suspended": cp_suspended, "resumed": cp_resumed, "minimized": cp_minimized,
            "queue_block": cp_queue_block, "queue_checked": cp_queue_checked, "audit_down": cp_audit_down,
            "audit_up": cp_audit_up, "scan": cp_scan, "revoked": cp_revoked, "disabled": cp_disabled,
            "restart_enabled": cp_restart_enabled}


def launch(mode, tox, run_dir, datadir):
    shutil.copyfile(os.path.join(HERE, "td_runner.py"), os.path.join(run_dir, "td_runner.py"))
    shutil.copyfile(os.path.join(HERE, "td_runner_mcp.py"), os.path.join(run_dir, "td_runner_mcp.py"))
    toe = make_toe(run_dir, "td_tests", boot_script("td_runner", start_frame=20))
    td = TDProcess(toe, run_dir, env={"TDFLOW_TOX": tox, "TDFLOW_SRC_ROOT": ROOT, "TDFLOW_TEST_MODE": mode,
                                      "TDFLOW_DATADIR": datadir, "TDFLOW_PRIVATE_ROOT": os.path.join(os.path.dirname(datadir), "private-root"),
                                      "TDFLOW_CLIENT_HOME": os.path.join(run_dir, "client-home")})
    done, deadline = set(), time.time() + 600
    try:
        while td.alive() and time.time() < deadline:
            if os.path.exists(os.path.join(run_dir, "td_results.json")):
                break
            for fn in sorted(os.listdir(run_dir)):
                if fn.startswith("cp_") and fn.endswith(".json") and fn not in done:
                    try:
                        with open(os.path.join(run_dir, fn), encoding="utf-8") as f:
                            cp = json.load(f)
                    except (PermissionError, ValueError):     # being written right now (Windows): retry
                        continue
                    done.add(fn)
                    extra = {}
                    try:
                        extra = HANDLERS.get(cp["name"], lambda d, p: None)(cp["data"], td.pid) or {}
                    except Exception as e:
                        T(f"harness.{cp['name']}", False, error=f"{type(e).__name__}: {e}")
                    tmp = os.path.join(run_dir, f"go_{cp['name']}.json.tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(dict({"ok": True}, **extra), f)
                    os.replace(tmp, os.path.join(run_dir, f"go_{cp['name']}.json"))
            time.sleep(0.05)
    finally:
        rc = td.finish(60)
    T(f"{mode}.exit_code_0", rc == 0 and not td.killed, rc=rc, killed=td.killed)
    T(f"{mode}.no_listener_after_exit", all(not oslisteners.other_listeners(p, 0) for p in base.PORTS))
    T(f"{mode}.other_td_untouched", td.others_untouched())
    err = os.path.join(run_dir, "boot_error.txt")
    T(f"{mode}.no_boot_errors", not os.path.exists(err), error=open(err).read()[-1500:] if os.path.exists(err) else "")
    res_p = os.path.join(run_dir, "td_results.json")
    return json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-mcp-test-results.json"))
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-mcp-tests"), ROOT)
    if os.path.exists(top):
        shutil.rmtree(top)
    datadir = os.path.join(top, "datadir")
    os.makedirs(datadir)
    tox = os.path.abspath(a.tox)
    with open(tox, "rb") as f:
        tox_sha = hashlib.sha256(f.read()).hexdigest()
    if any(oslisteners.other_listeners(p, 0) for p in base.PORTS):
        print(json.dumps({"abort": "a test port is in use"}))
        return
    results = {}
    for mode in ("mcp", "mcp_restart"):
        run_dir = os.path.join(top, mode)
        os.makedirs(run_dir)
        H["run_dir"] = run_dir
        results[mode] = launch(mode, tox, run_dir, datadir)
    secrets_ = [t.split(".", 1)[1].encode() for t in TOKENS.values() if t]
    blob = b""
    for fn in os.listdir(datadir):
        with open(os.path.join(datadir, fn), "rb") as f:
            blob += f.read()
    T("secrets.no_token_in_datadir_files", bool(blob) and not any(s in blob for s in secrets_),
      files=sorted(os.listdir(datadir)))
    logs = ""
    for mode in results:
        lp = os.path.join(top, mode, "td_console.log")
        if os.path.exists(lp):
            logs += open(lp, encoding="utf-8", errors="replace").read()
    T("secrets.no_token_in_console_logs", not any(s.decode() in logs for s in secrets_))
    in_td = [t for m in results.values() for t in (m.get("tests") or [])]
    errs = [e for m in results.values() for e in (m.get("errors") or [])]
    out = {"tox_sha256": tox_sha, "td_build": results.get("mcp", {}).get("td_build"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs,
                     "extra": {m: {k: v for k, v in r.items() if k in ("audit_counts", "kill_param_callback_synchronous",
                                                                         "datadir_files", "frame_stats")}
                               for m, r in results.items()}},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]}}
    out["result"] = "pass" if (out["in_td"]["passed"] == out["in_td"]["total"] and not errs and
                               out["harness"]["passed"] == out["harness"]["total"] and in_td) else "fail"
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    failed = [t["name"] for t in in_td + H["tests"] if not t["ok"]]
    print(json.dumps({"result": out["result"], "in_td": f"{out['in_td']['passed']}/{out['in_td']['total']}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}", "failed": failed,
                      "errors": errs[:2]}, indent=1))


if __name__ == "__main__":
    main()
