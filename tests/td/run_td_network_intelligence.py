"""Benchmark the same read-only operator tasks against a packaged baseline or candidate."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import hmac
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import posixpath
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codex_engine
import run_td_gate_tests as gate
import run_td_mcp_tests as mcp
import run_td_onboard_tests as onboard


DESCRIBE = "tdflow_describe_network"
GET = "tdflow_get_operator"
PARAMS = "tdflow_get_parameters"
RESUME = "tdflow_handoff_resume"
METHODS = (DESCRIBE, GET, PARAMS, RESUME, "tdflow_get_errors", "tdflow_list_operators")
EVIDENCE = {}
STATE = {}


def token_for(name, scope):
    response = mcp.admin("POST", "/setup/api/tokens", {"display_name": name, "role": "observer", "scope": scope + "/**"})
    token = (mcp.base.jbody(response) or {}).get("token")
    if response["status"] != 200 or not token:
        raise RuntimeError("Fixture observer creation failed")
    client = mcp.MCP(token)
    agent = client.call("tdflow_whoami")["agent_id"]
    _, result = mcp.perm(agent, {"add_grants": [
        {"scope": "/project1/fx/signals/restricted_canary/**", "effect": "deny", "actions": ["read"]},
        {"scope": "/project1/fx/protected_canary/**", "effect": "deny", "actions": ["read"]},
        {"scope": "/project1/fx/picture/grade/restricted_wire", "effect": "deny", "actions": ["read"]},
        {"scope": "/project1/fx/bounded/hidden_extra", "effect": "deny", "actions": ["read"]},
        {"scope": "/project1/fx/mesh/zz_hidden_feed", "effect": "deny", "actions": ["read"]},
    ]})
    if result["status"] != 200:
        raise RuntimeError("Fixture read exclusions failed")
    return token, agent


@contextmanager
def measure(token):
    """Forward one disposable read-only token to the fixed loopback MCP address."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_error(405)

        def do_POST(self):
            if self.path != "/mcp" or not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()):
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError()
                raw = self.rfile.read(size)
                request = json.loads(raw)
                name = (request.get("params") or {}).get("name") if request.get("method") == "tools/call" else None
                if name is not None and name not in METHODS:
                    raise ValueError()
            except (ValueError, AttributeError):
                self.send_error(400)
                return
            headers = {key: self.headers[key] for key in ("Authorization", "Accept", "Content-Type",
                       "MCP-Protocol-Version", "Mcp-Method", "Mcp-Name", "Mcp-Session-Id") if key in self.headers}
            connection = HTTPConnection("127.0.0.1", 9480, timeout=20)
            start = time.perf_counter()
            try:
                connection.request("POST", "/mcp", raw, headers)
                response = connection.getresponse()
                payload = response.read(2 * 1024 * 1024 + 1)
                if len(payload) > 2 * 1024 * 1024:
                    self.send_error(502)
                    return
                if name:
                    calls.append({"tool": name, "bytes": len(payload), "ms": round((time.perf_counter() - start) * 1000, 2)})
                self.send_response(response.status)
                for key in ("Content-Type", "Mcp-Session-Id"):
                    if response.getheader(key):
                        self.send_header(key, response.getheader(key))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            finally:
                connection.close()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/mcp", calls
        finally:
            server.shutdown()
            thread.join(5)


def live(work, data, candidate, label):
    token, _ = STATE[label]
    expected = {"top_source": data["plate"], "top_stage": data["level"], "chop_stage": data["motion"],
                "control_operator": data["rack"], "control_parameter": "Fade", "control_value": 0, "fault_operator": data["fault"],
                "owner_change_operator": data["rack"], "owner_change_parameter": "Fade"}
    prompt = (
        "Inspect this synthetic TouchDesigner project using only TDflowMCP read tools. "
        f"The readable project component starts at {data['root']}. Find the two TOPs immediately upstream of the "
        "final display through its nested components, in signal order. Then find the CHOP that processes the "
        "oscillator (chop_stage). Trace the zero-opacity problem to the exact controlling operator, parameter and numeric value. "
        "When you read parameters, request only the named TOP opacity parameter and its named controller parameter. "
        "Locate the separate TOP with a recorded error. Resume task review_fade to report the exact operator and "
        "parameter changed outside TDflowMCP. Make no changes. "
        "Output exactly one JSON object. Use absolute paths for operator values and exact case for parameter names. "
        "Keys: " + ", ".join(expected) + ".")
    if candidate:
        prompt += " Prefer compact multi-level description, dependency inspection and purpose/error filters where applicable."
    started = time.perf_counter()
    result, usage, api_ms = "", None, None
    with measure(token) as (url, calls):
        if label == "claude":
            config = work / "claude-mcp.json"
            config.write_text(json.dumps({"mcpServers": {"tdflow": {
                "type": "http", "url": url, "headers": {"Authorization": "Bearer ${TDFLOW_TOKEN}"}}}}), encoding="utf-8")
            exe = shutil.which("claude")
            if not exe:
                raise RuntimeError("Installed Claude Code client unavailable")
            rc, stdout, _ = onboard.run_claude(
                exe, ["-p", prompt, "--mcp-config", str(config), "--strict-mcp-config", "--allowedTools",
                      *("mcp__tdflow__" + name for name in METHODS), *onboard.ISOLATION,
                      "--output-format", "stream-json", "--verbose"], str(work),
                dict(os.environ, TDFLOW_TOKEN=token), 235)
            events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
            event = next((event for event in reversed(events) if event.get("type") == "result"), {})
            result, usage, api_ms = event.get("result", ""), event.get("usage"), event.get("duration_api_ms")
            success = rc == 0 and not event.get("is_error") and onboard.isolated_ok(onboard.init_tools(stdout))
        else:
            exe = codex_engine.cli_engine()
            if not exe:
                raise RuntimeError("Installed Codex CLI unavailable")
            settings = ('mcp_servers={tdflow={url="' + url + '",bearer_token_env_var="TDFLOW_TOKEN",'
                        "enabled_tools=" + json.dumps(list(METHODS)) + "}}")
            disabled = ("shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use")
            flags = [flag for name in disabled for flag in ("--disable", name)]
            proc = subprocess.run(
                [exe, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
                 "--sandbox", "read-only", *flags, "-c", 'web_search="disabled"', "-c", settings, "--json", prompt],
                cwd=work, env=dict(os.environ, TDFLOW_TOKEN=token), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=235)
            events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
            items = [event.get("item", {}) for event in events if event.get("type") == "item.completed"]
            result = next((item.get("text", "") for item in reversed(items) if item.get("type") == "agent_message"), "")
            usage = next((event.get("usage") for event in reversed(events) if event.get("type") == "turn.completed"), None)
            success = proc.returncode == 0 and not any(item.get("type") == "command_execution" for item in items)
    try:
        answer = json.loads(result[result.index("{"):result.rindex("}") + 1])
    except (ValueError, TypeError):
        answer = {}
    score = {key: answer.get(key) == value for key, value in expected.items()}
    report = {"client_ok": bool(success and calls), "correct": sum(score.values()), "total": len(score),
              "answers": score, "calls": len(calls), "response_bytes": sum(record["bytes"] for record in calls),
              "rpc_ms": round(sum(record["ms"] for record in calls), 2), "elapsed_s": round(time.perf_counter() - started, 2),
              "api_ms": api_ms, "usage": usage, "tools": calls}
    return report


def seed(data):
    builder = mcp.MCP(mcp.TOKENS["B"])
    checkpoint = builder.call("tdflow_task_checkpoint", {"task_id": "review_fade", "title": "Review grade fade",
                "status": "in_progress", "scopes": [data["rack"]], "based_on_rev": 0})
    gate.T("intelligence.checkpoint_seeded", mcp.ok(checkpoint), code=checkpoint.get("code"))
    for name in ("scripted", "claude", "codex"):
        STATE[name] = token_for("Network benchmark " + name, data["root"])


def suite(data, candidate):
    client = mcp.MCP(STATE["scripted"][0])
    def call(name, arguments):
        status, reply, raw = client.rpc("tools/call", {"name": name, "arguments": arguments})
        result = (reply or {}).get("result", {})
        row = {"tool": name, "status": status, "bytes": len(raw.get("body", b"")), "ms": raw.get("ms"),
               "ok": result.get("isError") is False}
        return result.get("structuredContent", {}), row

    tasks = {}
    def record(name, calls, verify):
        replies, rows = zip(*(call(tool, args) for tool, args in calls))
        passed = verify(replies) and all(row["ok"] for row in rows)
        tasks[name] = {"correct": passed, "calls": len(rows), "response_bytes": sum(row["bytes"] for row in rows),
                       "rpc_ms": round(sum(row["ms"] or 0 for row in rows), 2), "tools": rows}
        gate.T("intelligence." + name, passed)
        return replies

    def described(path, **opts):
        return DESCRIBE, {"path": path, **opts}

    def wiring(rows):
        links = {tuple(edge[:2]) for row in rows for edge in row["edges"]}
        return {(data["plate"], data["level"]), (data["level"], data["output"]),
                (data["oscillator"], data["motion"])} <= links

    if candidate:
        record("understand", [described(data["root"], compact={"depth": 3, "limit": 80, "dependencies": True})],
               wiring)
        record("trace", [described(data["root"], compact={"depth": 3, "limit": 80, "dependencies": True,
              "parameters": [{"path": data["level"], "names": ["opacity"]}]}),
              (PARAMS, {"path": data["rack"], "names": ["Fade"]})],
               lambda rows: (data["level"], data["output"]) in {tuple(edge[:2]) for edge in rows[0]["edges"]}
               and rows[1]["parameters"][0]["value"] == 0
               and {"operator": data["level"], "parameter": "opacity", "mode": "EXPRESSION",
                    "candidates": [{"operator": data["rack"], "parameter": "Fade"}], "unresolved": False}
               in rows[0]["parameter_references"])
        record("search", [described(data["root"], compact={"depth": 3, "state": "error"})],
               lambda rows: data["fault"] in {row["path"] for row in rows[0]["operators"]})
    else:
        record("understand", [described(data[key]) for key in ("root", "signals", "picture", "group")], wiring)
        record("trace", [described(data["group"]), (GET, {"path": data["output"], "connectors": True}),
                          (PARAMS, {"path": data["level"], "names": ["opacity"]}),
                          (PARAMS, {"path": data["rack"], "names": ["Fade"]})],
               lambda rows: (data["level"], data["output"]) in map(tuple, rows[0]["edges"])
               and data["rack"] in rows[2]["parameters"][0]["expr"] and rows[3]["parameters"][0]["value"] == 0)
        record("search", [described(data[key], diagnostics=True) for key in ("root", "picture", "group")],
               lambda rows: data["fault"] in rows[-1]["with_errors"])
    record("resume", [(RESUME, {"task_id": "review_fade"})],
           lambda rows: rows[0]["fingerprint"]["changed_outside_tdflowmcp"]
           and data["rack"] in json.dumps(rows[0]["fingerprint"]["parameter_changes"])
           and "Fade" in json.dumps(rows[0]["fingerprint"]["parameter_changes"]))
    EVIDENCE["scripted"] = tasks
    scoped, _ = call(DESCRIBE, {"path": data["root"], **({"compact": {"depth": 3, "dependencies": True}} if candidate else {})})
    direct, _ = call(DESCRIBE, {"path": data["private"]})
    gate.T("intelligence.direct_private_denied", direct.get("code") == "DENIED")
    gate.T("intelligence.network_omits_private_path", data["private"] not in json.dumps(scoped))
    EVIDENCE["legacy_reports_filtered_count"] = "filtered" in scoped
    if candidate:
        for modern in (False, True):
            status, reply, _ = client.rpc("tools/list", modern=modern)
            tools = (reply or {}).get("result", {}).get("tools", [])
            names = sorted(tool["name"] for tool in tools)
            describe = next((tool for tool in tools if tool["name"] == DESCRIBE), {})
            gate.T("intelligence.exact_catalog_" + ("modern" if modern else "legacy"),
                   status == 200 and names == mcp.EXPECTED_TOOLS
                   and "compact" in describe.get("inputSchema", {}).get("properties", {}))
    return {"candidate": candidate}


def run_clients(data, work, candidate):
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {}
        for label in ("claude", "codex"):
            client_work = work / label
            client_work.mkdir()
            jobs[label] = pool.submit(live, client_work, data, candidate, label)
        results = {label: job.result() for label, job in jobs.items()}
    EVIDENCE["clients"] = results
    for label, result in results.items():
        gate.T("intelligence.live_" + label, result["client_ok"] and (not candidate or result["correct"] == result["total"]),
               correct=result["correct"], total=result["total"])


def compact(path, **options):
    client = mcp.MCP(STATE["scripted"][0])
    result = client.call(DESCRIBE, {"path": path, "compact": options})
    return result


def snapshot(data):
    return compact(data["root"], depth=3, limit=80, dependencies=True, parameters=[
        {"path": data["level"], "names": ["opacity", "contrast"]},
        {"path": data["rack"], "names": ["Backup", "Fade", "extension1", "bad_name"]},
        {"path": data["hidden_user"], "names": ["opacity"]},
        {"path": data["dynamic"], "names": ["opacity"]},
        {"path": data["selector"], "names": ["top"]},
        {"path": data["private"], "names": ["Secret"]},
    ])


def security(data):
    root = data["root"]
    result = snapshot(data)
    STATE["projection"] = result
    refs = {(ref["operator"], ref["parameter"]): ref for ref in result["parameter_references"]}
    gate.T("intelligence.readable_literal_and_bind", refs[(data["level"], "opacity")]["candidates"]
           == [{"operator": data["rack"], "parameter": "Fade"}]
           and refs[(data["rack"], "Backup")]["mode"] == "BIND"
           and refs[(data["rack"], "Backup")]["candidates"] == [{"operator": data["rack"], "parameter": "Fade"}])
    gate.T("intelligence.literal_chop_channel_is_candidate", refs[(data["level"], "contrast")]["candidates"]
           == [{"operator": data["motion"], "channel": "chan1"}])
    gate.T("intelligence.omits_unreadable_parameter_and_source_text",
           refs[(data["hidden_user"], "opacity")]["unresolved"] and refs[(data["dynamic"], "opacity")]["unresolved"]
           and all((data[key], name) not in refs for key, name in (("private", "Secret"), ("selector", "top"),
                                                                  ("rack", "extension1"), ("rack", "bad_name")))
           and all(word not in json.dumps(result) for word in (data["private"], data["protected"], data["hidden_wire"],
                                                               "op(", "filtered", "Secret")))
    gate.T("intelligence.exact_wire_endpoints", [data["plate"], data["level"], 0, 0] in result["edges"]
           and [data["level"], data["output"], 0, 0] in result["edges"])
    page1 = compact(root, depth=3, limit=9, dependencies=True)
    page2 = compact(root, depth=3, limit=9, offset=page1["next_offset"])
    paths = [row["path"] for row in page1["operators"] + page2["operators"]]
    gate.T("intelligence.pages_stable_and_scoped", page1["next_offset"] == 9 and page2["next_offset"] == 18
           and page1["total"] == page2["total"] == result["total"] and paths == sorted(set(paths))
           and all(all(path in paths[:9] for path in wire[:2]) for wire in page1["edges"]))
    purpose = compact(root, depth=3, query="remap incoming")
    gate.T("intelligence.curated_purpose_search", purpose.get("purpose_search") is True
           and [row["path"] for row in purpose["operators"]] == [data["motion"]])
    errors = compact(root, depth=3, state="error")
    gate.T("intelligence.error_search_omits_diagnostic_text", data["fault"] in {row["path"] for row in errors["operators"]}
           and all(set(row) <= {"path", "type", "error", "warning"} for row in errors["operators"]))
    broad = mcp.MCP(mcp.TOKENS["B"])
    protected = broad.call(DESCRIBE, {"path": data["protected"], "compact": {}})
    projected = broad.call(DESCRIBE, {"path": root, "compact": {"depth": 3}})
    outsider = compact("/project1")
    absent_outside = compact("/project1/unknown")
    gate.T("intelligence.protected_and_outside_scopes_denied",
           protected.get("code") == outsider.get("code") == absent_outside.get("code") == "DENIED"
           and data["protected"] not in json.dumps(projected))
    gate.T("intelligence.invalid_root_and_combined_views_refused",
           compact(data["level"]).get("code") == "INVALID_ARGS"
           and broad.call(DESCRIBE, {"path": root, "compact": {}, "diagnostics": True}).get("code") == "INVALID_ARGS")
    events = (mcp.base.jbody(mcp.admin("POST", "/api/audit/search", {"tool": DESCRIBE, "limit": 40})) or {}).get("events", [])
    gate.T("intelligence.audited_with_no_response_payload", any(e.get("outcome") == "ok" for e in events)
           and all(marker not in json.dumps(events) for marker in ('"parameter_references"', '"operators"', '"candidates"')))


def revoked(data):
    _, aid = STATE["scripted"]
    agents = (mcp.base.jbody(mcp.admin("GET", "/setup/api/agents")) or {}).get("agents", [])
    token_id = next(agent for agent in agents if agent["id"] == aid)["tokens"][0]["token_id"]
    response = mcp.admin("POST", "/setup/api/tokens/revoke", {"token_id": token_id})
    gate.T("intelligence.revoked_token_refused", response["status"] == 200
           and compact(data["root"]).get("_http") == 401)


def suspended(data):
    result = compact(data["root"])
    gate.T("intelligence.kill_switch", (result.get("code") or result.get("_code")) == "SUSPENDED"
           and "operators" not in result, code=result.get("code") or result.get("_code"))


def minimized(data, pid):
    from tdrun import PROCS

    process = PROCS[pid]
    process.keep_running = False
    try:
        gate.T("intelligence.fixture_window_minimized", process.set_minimized(True))
        time.sleep(2.8)
        result = compact(data["group"])
        gate.T("intelligence.minimized_read_refused", (result.get("code") or result.get("_code")) == "TDFLOW_NOT_COOKING"
               and "operators" not in result)
    finally:
        restored = process.set_minimized(False)
        process.keep_running = True
    time.sleep(1.5)
    gate.T("intelligence.restored_read_recovers", restored and mcp.ok(compact(data["group"])))


def truncated(data, compare=False):
    result = compact(data["path"], dependencies=True)
    if compare:
        gate.T("intelligence.hidden_wire_does_not_change_edge_truncation", result == STATE["truncated"])
    else:
        STATE["truncated"] = result
        gate.T("intelligence.wire_output_truncated", result.get("edges_truncated") is True
               and len(result["edges"]) == 128 and result["total"] == 24)


def readable_limit(data, compare=False):
    result = compact(data["path"], query="item_000", limit=1)
    if compare:
        gate.T("intelligence.hidden_sibling_does_not_spend_readable_budget", result == STATE["near_limit"])
    else:
        STATE["near_limit"] = result
        gate.T("intelligence.search_at_readable_budget", mcp.ok(result) and result["total"] == 1,
               code=result.get("code"), total=result.get("total"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tox", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--live-clients", action="store_true")
    args = parser.parse_args()
    tox = args.tox.resolve(strict=True)
    if any(gate.oslisteners.other_listeners(port, 0) for port in gate.base.PORTS):
        raise SystemExit("A TouchDesigner fixture port is in use.")
    work = Path(tempfile.mkdtemp(prefix="td-network-intelligence-", dir=Path(gate.ROOT) / ".tmp"))
    run, project = work / "run", work / "project"
    run.mkdir()
    project.mkdir()
    shutil.copyfile(HERE / "td_runner_network_intelligence.py", run / "td_runner_network_intelligence.py")
    result = gate.launch("network_intelligence", str(tox), str(run), str(project),
                         {"TDFLOW_PRIVATE_ROOT": str(work / "private")},
                         {"setup_ready": mcp.cp_setup_ready, "pair_approved": mcp.cp_pair_approved,
                          "intelligence_seed": lambda data, _pid: seed(data),
                          "intelligence_benchmark": lambda data, _: suite(data, args.candidate),
                          "intelligence_clients": lambda data, _: run_clients(data, work, args.candidate) if args.live_clients else None,
                          "intelligence_security": lambda data, _: security(data),
                          "intelligence_hidden_changed": lambda data, _: gate.T("intelligence.hidden_edits_omit_counts_paths_and_edges",
                                                                                 snapshot(data) == STATE["projection"]),
                          "intelligence_truncated": lambda data, _: truncated(data),
                          "intelligence_truncated_after_hidden": lambda data, _: truncated(data, compare=True),
                          "intelligence_before_hidden_sibling": lambda data, _: readable_limit(data),
                          "intelligence_after_hidden_sibling": lambda data, _: readable_limit(data, compare=True),
                          "intelligence_bounded": lambda data, _: gate.T("intelligence.generic_work_refusal",
                                                                        compact(data["path"]).get("code") == "NETWORK_INSPECTION_LIMIT"),
                          "intelligence_suspended": lambda data, _: suspended(data),
                          "intelligence_minimized": minimized,
                          "intelligence_revoke": lambda data, _: revoked(data)},
                         deadline_s=800)
    checks = result.get("tests", []) + gate.H["tests"]
    report = {"tox_sha256": hashlib.sha256(tox.read_bytes()).hexdigest(), "td_build": result.get("td_build"),
              "version": result.get("component_version"), "checks": checks, "errors": result.get("errors", []),
              "evidence": EVIDENCE}
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failures = [check["name"] for check in checks if not check["ok"]]
    print(json.dumps({"checks": len(checks), "failed": failures, "errors": report["errors"], "evidence": EVIDENCE}))
    return 0 if result.get("tests") and not failures and not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
