"""Verify scoped network diagrams against a packaged TDflowMCP component."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_td_gate_tests as gate
import run_td_mcp_tests as mcp


TOOL = "tdflow_capture_network"
RESULTS = {}
EVIDENCE = {}


def image_call(client, path):
    started = time.perf_counter()
    status, response, _ = client.rpc("tools/call", {"name": TOOL, "arguments": {"path": path}})
    result = (response or {}).get("result") or {}
    content = [part for part in result.get("content", []) if part.get("type") == "image"]
    payload = result.get("structuredContent") or {}
    png = base64.b64decode(content[0].get("data", "")) if len(content) == 1 else b""
    return status, payload, png, round(1000 * (time.perf_counter() - started), 1)


def inspect(data, work):
    path = data["path"]
    builder = mcp.MCP(mcp.TOKENS["B"])
    response = mcp.admin("POST", "/setup/api/tokens", {
        "display_name": "Restricted network diagram", "role": "observer", "scope": path + "/**",
    })
    reader = mcp.MCP((mcp.base.jbody(response) or {}).get("token"))
    agent = reader.call("tdflow_whoami").get("agent_id")
    gate.T("vision.restricted_credential", response["status"] == 200 and bool(agent))
    deny = [{"scope": data[key], "effect": "deny", "actions": ["read"]} for key in ("hidden", "factory")]
    _, applied = mcp.perm(agent, {"add_grants": deny})
    gate.T("vision.narrowed_read_grants", applied["status"] == 200)
    status, scoped, png, duration = image_call(reader, path)
    RESULTS["before"] = scoped, png
    RESULTS["reader"] = reader
    RESULTS["agent"] = agent
    gate.T("vision.diagram_and_structured_data", status == 200
           and scoped.get("representation") == "rendered_network_diagram"
           and scoped.get("width") == 960 and scoped.get("height") == 640
           and png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (960, 640)
           and scoped.get("bytes") == len(png), duration_ms=duration)
    nodes = {row["name"]: row for row in scoped.get("nodes", [])}
    gate.T("vision.exactly_twenty_scoped_direct_children", len(nodes) == 20 and scoped.get("partial") is False
           and all("num_children" not in row for row in nodes.values()))
    raw = json.dumps(scoped)
    gate.T("vision.protected_and_private_facts_absent",
           all(data[key] not in raw for key in ("hidden", "factory", "protected"))
           and all(word not in raw for word in ("nested_fixture_text", "reserved_fixture_content", "filtered")))
    edge = {"source": nodes.get("aa_source", {}).get("id"), "target": nodes.get("bb_stage", {}).get("id"),
            "output_index": 0, "input_index": 0}
    exact = builder.call("tdflow_get_operator", {"path": path + "/bb_stage", "connectors": True})
    gate.T("vision.reverse_wire_position_and_ports_agree",
           nodes.get("aa_source", {}).get("x") == 410 and nodes.get("bb_stage", {}).get("x") == -340
           and edge in scoped.get("edges", []) and exact.get("connectors", {}).get("inputs", {}).get("rows") == [
               {"index": 0, "peers": [{"path": path + "/aa_source", "index": 0}], "complete": True}
           ])
    indexed = {"source": nodes.get("aa_source", {}).get("id"), "target": nodes.get("node_03", {}).get("id"),
               "output_index": 0, "input_index": 1}
    edges = scoped.get("edges", [])
    gate.T("vision.mixed_family_branches_and_secondary_port", len(edges) == 11 and indexed in edges)
    _, limited, refused, _ = image_call(builder, path)
    gate.T("vision.shared_rate_limit", limited.get("code") == "RATE_LIMITED" and not refused)
    _, outside, refused, _ = image_call(reader, "/project1/fx")
    gate.T("vision.out_of_scope_request_refused", outside.get("code") == "DENIED" and not refused)
    _, protected, refused, _ = image_call(builder, data["protected"])
    gate.T("vision.protected_request_refused", protected.get("code") == "DENIED" and not refused)
    time.sleep(1.05)
    _, empty, empty_png, _ = image_call(builder, data["empty"])
    gate.T("vision.empty_network", empty.get("nodes") == [] and empty.get("edges") == []
           and empty.get("partial") is False and bool(empty_png))
    time.sleep(1.05)
    _, large, large_png, large_ms = image_call(builder, data["large"])
    gate.T("vision.bounded_large_network", len(large.get("nodes", [])) == 64 and large.get("partial") is True
           and 0 < len(large_png) <= 192 * 1024 and large_ms < 1500, duration_ms=large_ms)
    events = (mcp.base.jbody(mcp.admin("POST", "/api/audit/search", {"tool": TOOL, "limit": 40})) or {}).get("events", [])
    event_text = json.dumps(events)
    gate.T("vision.audited_without_image_or_nodes", any(event.get("outcome") == "ok" for event in events)
           and '"nodes"' not in event_text and (not png or base64.b64encode(png[:24]).decode("ascii") not in event_text))
    if png:
        (work / "scoped-diagram.png").write_bytes(png)
    EVIDENCE.update(scoped_nodes=len(nodes), scoped_edges=len(scoped.get("edges", [])), scoped_ms=duration,
                    large_ms=large_ms, png_sha256=hashlib.sha256(png).hexdigest(), png_bytes=len(png))
    time.sleep(1.05)


def hidden_changed(data):
    reader = RESULTS["reader"]
    _, matching, png, _ = image_call(reader, data["path"])
    gate.T("vision.hidden_geometry_and_wire_cannot_change_pixels_or_facts",
           bool(png) and (matching, png) == RESULTS["before"])
    _, stale, refused, _ = image_call(mcp.MCP(mcp.TOKENS["B"]), data["stale"])
    gate.T("vision.stale_path_returns_no_image", stale.get("code") == "NOT_FOUND" and not refused)
    agents = (mcp.base.jbody(mcp.admin("GET", "/setup/api/agents")) or {}).get("agents", [])
    token_id = next(agent for agent in agents if agent["id"] == RESULTS["agent"])["tokens"][0]["token_id"]
    revoked = mcp.admin("POST", "/setup/api/tokens/revoke", {"token_id": token_id})
    status, _, refused, _ = image_call(reader, data["path"])
    gate.T("vision.revoked_credential_returns_no_image", revoked["status"] == 200 and status == 401 and not refused)


def live_clients(data, work, project):
    import network_vision_clients as clients

    path, png = data["path"], RESULTS["before"][1]
    if not png:
        return {"repair_performed": False}
    time.sleep(1.05)
    EVIDENCE["codex_delivery"] = clients.codex_delivery(work, project, path)
    EVIDENCE["claude_vision"] = clients.claude_interpretation(project, path, png)
    repair = EVIDENCE["claude_repair"] = clients.claude_repair(project, path)
    return {"repair_performed": repair["verified"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tox", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live-clients", action="store_true")
    args = parser.parse_args()
    if any(gate.oslisteners.other_listeners(port, 0) for port in gate.base.PORTS):
        raise SystemExit("A TouchDesigner test port is already in use.")
    work = Path(tempfile.mkdtemp(prefix="td-network-visual-", dir=Path(gate.ROOT) / ".tmp"))
    run, project = work / "run", work / "project"
    run.mkdir()
    project.mkdir()
    shutil.copyfile(Path(__file__).with_name("td_runner_network_visual.py"), run / "td_runner_network_visual.py")
    handlers = {"setup_ready": mcp.cp_setup_ready, "pair_approved": mcp.cp_pair_approved,
                "vision_inspect": lambda data, _pid: inspect(data, work),
                "vision_hidden_changed": lambda data, _pid: hidden_changed(data),
                "vision_client": lambda data, _pid: live_clients(data, work, project) if args.live_clients
                else {"repair_performed": False}}
    tox = args.tox.resolve(strict=True)
    result = gate.launch("network_visual", str(tox), str(run), str(project),
                         {"TDFLOW_PRIVATE_ROOT": str(work / "private")}, handlers,
                         deadline_s=780 if args.live_clients else 300)
    checks = result.get("tests", []) + gate.H["tests"]
    receipt = {"tox_sha256": hashlib.sha256(tox.read_bytes()).hexdigest(),
               "version": result.get("component_version"), "td_build": result.get("td_build"),
               "checks": checks, "errors": result.get("errors") or [], "evidence": EVIDENCE}
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    failed = [item["name"] for item in checks if not item["ok"]]
    print(json.dumps({"checks": len(checks), "failed": failed, "errors": receipt["errors"], "evidence": EVIDENCE}))
    return 0 if result.get("tests") and not failed and not receipt["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
