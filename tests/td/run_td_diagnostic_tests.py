"""Replay diagnostic workflows against a packaged TDflowMCP binary."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_td_gate_tests as gate
import run_td_mcp_tests as mcp
import run_td_onboard_tests as onboard


FX = "/project1/fx"
DESCRIBE = "tdflow_describe_network"
CALLS = {name: [] for name in "ABCD"}
EVIDENCE = {}


def call(case, name, **args):
    now = time.perf_counter()
    client = mcp.MCP(mcp.TOKENS["E"])
    status, reply, _ = client.rpc("tools/call", {"name": name, "arguments": args})
    result = (reply or {}).get("result") or {}
    data = result.get("structuredContent") or {}
    CALLS[case].append({"tool": name, "ms": round(1000 * (time.perf_counter() - now), 1),
                        "ok": status == 200 and result.get("isError") is False,
                        "code": data.get("code")})
    return data, result.get("content", [])


def capture(case, path):
    data, content = call(case, "tdflow_capture_top", path=path)
    image = next((part for part in content if part.get("type") == "image"), {})
    raw = base64.b64decode(image.get("data", ""))
    gate.T("visual.capture." + path.rsplit("/", 1)[-1], bool(raw))
    return [data.get("width"), data.get("height")], hashlib.sha256(raw).hexdigest()


def describe(case, path, candidate):
    args = {"path": path, "diagnostics": True} if candidate else {"path": path}
    return call(case, DESCRIBE, **args)[0]


def visual_evidence(candidate):
    c = FX + "/visual"
    if not candidate:
        left = capture("C", c + "/left")
        time.sleep(1.05)
        right = capture("C", c + "/right")
        gate.T("C.safe_outputs_differ", left[0] == right[0] and left[1] != right[1])
        refused, _ = call("C", "tdflow_capture_top", path=c + "/unlocked")
        gate.T("C.unlocked_refused", refused.get("code") == "TOP_LOCK_REQUIRED")
        return {"dimensions": left[0], "encoded_images_differ": left[1] != right[1]}

    compare = "tdflow_compare_tops"
    result, content = call("C", compare, left=c + "/left", right=c + "/right")
    gate.T("C.measured_pixel_difference", result.get("status") == "compared"
           and result["changed_pixel_fraction"] == 1 and .13 < result["mean_absolute_rgb_delta"] < .2)
    gate.T("C.comparison_has_no_image_attachment", all(part["type"] != "image" for part in content))
    rate, _ = call("C", "tdflow_capture_top", path=c + "/left")
    gate.T("C.comparison_shares_capture_rate_limit", rate.get("code") == "RATE_LIMITED")
    for side, path, expected in (("right", c + "/unlocked", "TOP_LOCK_REQUIRED"),
                                 ("left", c + "/unlocked", "TOP_LOCK_REQUIRED"),
                                 ("right", "/project1/TDflowMCP", "DENIED"),
                                 ("right", "/project1/outside_diagnostics", "DENIED"),
                                 ("right", c + "/bypassed/locked", "TOP_LOCK_REQUIRED"),
                                 ("right", c + "/oversized", "TOP_COMPARE_SIZE")):
        args = {"left": c + "/left", "right": c + "/right", side: path}
        refusal, _ = call("C", compare, **args)
        gate.T(f"C.refused.{side}.{path}", refusal.get("code") == expected)
    other = FX + "/cost/observed"
    different, _ = call("C", compare, left=c + "/left", right=other)
    gate.T("C.dimension_mismatch_has_no_pixel_metrics", different.get("status") == "different_dimensions"
           and "changed_pixel_fraction" not in different)
    time.sleep(1.05)
    cached, _ = call("C", compare, left=other, right=other)
    gate.T("C.locked_callback_upstream_sampled", cached.get("status") == "compared"
           and cached["changed_pixel_fraction"] == 0)
    time.sleep(1.05)
    dark, _ = call("C", compare, left=c + "/black", right=c + "/left")
    gate.T("C.near_black_fraction", dark.get("status") == "compared"
           and dark["left"]["near_black_fraction"] == 1 and dark["right"]["near_black_fraction"] == 0)
    return {key: result.get(key) for key in ("left", "right", "sample_ms", "mean_absolute_rgb_delta",
                                              "changed_pixel_fraction")}


def inspection(candidate):
    a = FX + "/broken"
    output = a + "/output"
    network = describe("A", a, candidate)
    errors = {} if candidate else call("A", "tdflow_get_errors", path=a)[0]
    node, _ = call("A", "tdflow_get_operator", path=output)
    params, _ = call("A", "tdflow_get_parameters", path=output, names=["opacity"])
    gate.T("A.observed_failure", output in network.get("with_errors", []) and (candidate or bool(errors.get("problems"))))
    gate.T("A.missing_input_and_expression", not node.get("inputs") and
           any(row.get("expr") == "1 / 0" for row in params.get("parameters", [])))
    EVIDENCE["A"] = {"errors": network.get("with_errors"), "input_paths": node.get("inputs")}
    if candidate:
        finding = next((item for item in network["diagnostics"]["findings"] if item["path"] == output), {})
        gate.T("A.structured_missing_input", "missing_required_inputs" in finding.get("observations", [])
               and finding.get("wiring", {}).get("connected_inputs") == 0)
        EVIDENCE["A"]["finding"] = finding

    b = FX + "/cost"
    network = describe("B", b, candidate)
    hot = next(row for row in network["operators"] if row["path"] == b + "/expensive")
    cheap = next(row for row in network["operators"] if row["path"] == b + "/cheap")
    gate.T("B.measured_cost_difference", hot["cook_ms"] >= 8 and hot["cook_ms"] > cheap["cook_ms"])
    EVIDENCE["B"] = {"expensive_ms": hot["cook_ms"], "cheap_ms": cheap["cook_ms"]}
    if candidate:
        performance = network["diagnostics"]["performance"]
        gate.T("B.ranked_cpu_samples", performance["units"] == "ms" and performance["sample_count"] >= 2
               and performance["top"][0]["path"] == hot["path"] and performance["top"][0]["cook_abs_frame"] is not None)
        EVIDENCE["B"]["sample_count"] = performance["sample_count"]
    EVIDENCE["C"] = visual_evidence(candidate)

    d = FX + "/restore"
    network = describe("D", d, candidate)
    gate.T("D.regression_is_visible", d + "/final" in network.get("with_errors", []))
    return {"candidate": candidate}


def unsupported_runtime():
    c = FX + "/visual"
    refused, content = call("C", "tdflow_compare_tops", left=c + "/left", right=c + "/right")
    gate.T("C.unverified_runtime_refuses_comparison", refused.get("code") == "TOP_READ_UNVERIFIED"
           and "left" not in refused and all(part["type"] != "image" for part in content))


def run_live(work, token, prompt, methods, *, endpoint=None):
    exe = shutil.which("claude")
    if not exe:
        return False, [], "", 0
    config = work / "diagnostic-mcp.json"
    config.write_text(json.dumps({"mcpServers": {"tdflow": {
        "type": "http", "url": endpoint or mcp.O + "/mcp", "headers": {"Authorization": "Bearer ${TDFLOW_TOKEN}"}
    }}}), encoding="utf-8")
    allowed = ["mcp__tdflow__" + name for name in methods]
    start = time.perf_counter()
    rc, stdout, _ = onboard.run_claude(
        exe, ["-p", prompt, "--mcp-config", str(config), "--strict-mcp-config", "--allowedTools", *allowed,
              *onboard.ISOLATION, "--output-format", "stream-json", "--verbose"], str(work),
        dict(os.environ, TDFLOW_TOKEN=token), 210)
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    uses = [part for event in events if event.get("type") == "assistant"
            for part in (event.get("message") or {}).get("content", []) if part.get("type") == "tool_use"]
    result = next((event for event in reversed(events) if event.get("type") == "result"), {})
    ok = rc == 0 and not result.get("is_error") and onboard.isolated_ok(onboard.init_tools(stdout))
    return ok, uses, str(result.get("result", ""))[:2400].replace(token, "[redacted]"), round(time.perf_counter() - start, 1)


def live_assessment(work, candidate):
    """Use a real observer session before the fixture applies the controlled repairs."""
    methods = [DESCRIBE, "tdflow_get_operator", "tdflow_get_errors", "tdflow_get_parameters", "tdflow_capture_top"]
    if candidate:
        methods.append("tdflow_compare_tops")
    prompt = ("Inspect four disposable TouchDesigner scenarios through MCP without changing them. "
              "A: explain the broken output in /project1/fx/broken and propose the smallest repair. "
              "B: rank expensive operators in /project1/fx/cost using observed CPU timing and its limits. "
              "C: compare the left and right locked TOPs in /project1/fx/visual and distinguish pixels from causes. "
              "D: diagnose /project1/fx/restore and explain a repair and how to verify it. "
              "Use actual tools for all four. Report four short observations labelled A, B, C, D. "
              "Keep explanations factual and avoid unsupported causes. "
              + ("Use diagnostics=true and the TOP comparison tool where applicable." if candidate else
                 "Use network summaries and captures. Consecutive captures must be at least one second apart."))
    ok, uses, summary, seconds = run_live(work, mcp.TOKENS["C"], prompt, methods)
    scopes = {value.split("/")[3] for part in uses for value in (part.get("input") or {}).values()
              if isinstance(value, str) and value.startswith(FX + "/") and len(value.split("/")) > 3}
    gate.T("live_diagnostics.isolated_and_complete", ok and scopes >= {"broken", "cost", "visual", "restore"})
    EVIDENCE["live"] = {"seconds": seconds,
                        "tool_calls": [part.get("name") for part in uses], "summary": summary}


def live_repair(work, path):
    response = mcp.admin("POST", "/setup/api/tokens", {
        "display_name": "Diagnostic repair fixture", "role": "builder", "scope": path + "/**",
        "actions": ["read", "update"]})
    token = (mcp.base.jbody(response) or {}).get("token")
    gate.T("D.live_repair_credential", response["status"] == 200 and bool(token))
    if not token:
        return
    prompt = (f"Diagnose the disconnected Level TOP final in the disposable network {path} using "
              "tdflow_describe_network (diagnostics=true). You are authorized to restore its original input "
              "by connecting original to final, exactly one wire. Make no other edits. Reinspect that "
              "network with the same tool after connecting. Report the verified edge and whether any reported error remains.")
    ok, uses, summary, seconds = run_live(work, token, prompt, [DESCRIBE, "tdflow_connect"])
    methods = [part.get("name") for part in uses if str(part.get("name")).startswith("mcp__")]
    connect = "mcp__tdflow__tdflow_connect"
    connection = [part.get("input", {}) for part in uses if part.get("name") == connect]
    describe = "mcp__tdflow__" + DESCRIBE
    gate.T("D.live_diagnose_repair_verify", ok and len(connection) == 1
           and connection[0].get("src") == path + "/original" and connection[0].get("dst") == path + "/final"
           and describe in methods[:methods.index(connect)] and describe in methods[methods.index(connect) + 1:])
    EVIDENCE["live_repair"] = {"seconds": seconds, "tool_calls": methods, "summary": summary}


def repairs(project, use_live_repair):
    a = FX + "/broken"
    args = {"path": a + "/output", "expressions": {"opacity": "1.0"}}
    approval, _ = call("A", "tdflow_set_parameters", **args)
    gate.T("A.approval_required", approval.get("code") == "APPROVAL_REQUIRED")
    if approval.get("approval_id"):
        gate.T("A.approval_accepted", mcp.approve_request(approval["approval_id"])["status"] == 200)
        call("A", "tdflow_set_parameters", **args)
    call("A", "tdflow_connect", src=a + "/source", dst=a + "/output")
    d = FX + "/restore"
    if use_live_repair:
        live_repair(project, d)
    else:
        call("D", "tdflow_connect", src=d + "/original", dst=d + "/final")


def verification():
    a = FX + "/broken"
    d = FX + "/restore"
    for case, network, output, source in (("A", a, "output", "source"), ("D", d, "final", "original")):
        facts, _ = call(case, DESCRIBE, path=network)
        gate.T(case + ".repair_verified_over_mcp",
               not facts.get("with_errors") and [network + "/" + source, network + "/" + output] in facts.get("edges", []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tox", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--live-agent", action="store_true")
    parser.add_argument("--live-repair", action="store_true")
    args = parser.parse_args()
    if args.live_repair and not args.candidate:
        parser.error("--live-repair requires --candidate")
    if any(gate.oslisteners.other_listeners(port, 0) for port in gate.base.PORTS):
        raise SystemExit("A TouchDesigner test port is already in use.")
    root = Path(gate.ROOT)
    work = Path(tempfile.mkdtemp(prefix="td-diagnostic-", dir=root / ".tmp"))
    run, project = work / "run", work / "project"
    run.mkdir()
    project.mkdir()
    shutil.copyfile(Path(__file__).with_name("td_runner_diagnostics.py"), run / "td_runner_diagnostics.py")
    handlers = {"setup_ready": mcp.cp_setup_ready, "pair_approved": mcp.cp_pair_approved,
                "diagnostic_inspection": lambda _d, _p: inspection(args.candidate),
                "diagnostic_unsupported_runtime": lambda _d, _p: unsupported_runtime(),
                "diagnostic_live": lambda _d, _p: live_assessment(project, args.candidate) if args.live_agent else None,
                "diagnostic_repairs": lambda _d, _p: repairs(project, args.live_repair),
                "diagnostic_verification": lambda _d, _p: verification()}
    tox = args.tox.resolve(strict=True)
    result = gate.launch("diagnostic_workflow", str(tox), str(run), str(project),
                         {"TDFLOW_PRIVATE_ROOT": str(work / "private")}, handlers,
                         deadline_s=600 if args.live_agent or args.live_repair else 300)
    checks = result.get("tests", []) + gate.H["tests"]
    receipt = {"tox_sha256": hashlib.sha256(tox.read_bytes()).hexdigest(),
               "version": result.get("component_version"), "calls": CALLS, "evidence": EVIDENCE,
               "checks": checks, "errors": result.get("errors") or []}
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    failed = [check["name"] for check in checks if not check["ok"]]
    print(json.dumps({"checks": len(checks), "failed": failed, "errors": receipt["errors"], "evidence": EVIDENCE}))
    return 0 if result.get("tests") and not failed and not receipt["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
