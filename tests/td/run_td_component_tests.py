"""Run the same reusable-component scenarios over real MCP on a baseline or candidate .tox."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_td_gate_tests as gate
import run_td_mcp_tests as mcp


ROOT = Path(gate.ROOT)
PARENT = "/project1/fx"
SET = "tdflow_set_parameters"
CREATE = "tdflow_create_operator"
CUSTOM = "tdflow_edit_custom_parameters"
REPORT = {}
PENDING = {}


class Scenario:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.approvals = 0
        REPORT[name] = self

    def call(self, client, tool, **args):
        reply = client.call(tool, args)
        self.calls.append({"tool": tool, "ok": mcp.ok(reply), "code": reply.get("code") or reply.get("_code")})
        return reply

    def check(self, label, condition):
        gate.T(f"{self.name}.{label}", condition)

    def approve(self, client, tool, **args):
        reply = self.call(client, tool, **args)
        self.check(tool + "_approval", reply.get("code") == "APPROVAL_REQUIRED")
        if reply.get("code") == "APPROVAL_REQUIRED":
            self.approvals += 1
            self.check(tool + "_owner_decision", mcp.approve_request(reply["approval_id"])["status"] == 200)
            return self.call(client, tool, **args)
        return reply

    def create(self, client, parent, kind, name):
        result = self.call(client, CREATE, parent=parent, type=kind, name=name)
        self.check("create_" + name, mcp.ok(result))
        return f"{parent}/{name}"

    def connect(self, client, source, destination, index=0):
        result = self.call(client, "tdflow_connect", src=source, dst=destination, dst_index=index)
        self.check("connect_" + destination.rsplit("/", 1)[-1], mcp.ok(result))


def scenarios(baseline):
    builder = mcp.MCP(mcp.TOKENS["B"])
    operator = mcp.MCP(mcp.TOKENS["E"])
    status, inventory, _ = builder.rpc("tools/list")
    tools = {tool["name"] for tool in (inventory or {}).get("result", {}).get("tools", [])}
    gate.T("component_workflow.tool_inventory", status == 200 and
           ({"tdflow_edit_custom_parameters", "tdflow_list_operator_types"} <= tools) == (not baseline))

    visual = Scenario("A")
    network = visual.create(builder, PARENT, "baseCOMP", "visual")
    if not baseline:
        catalog = visual.call(builder, "tdflow_list_operator_types", path=network, family="TOP", query="procedural noise")
        visual.check("discovery", mcp.ok(catalog) and [entry["type"] for entry in catalog["types"]] == ["noiseTOP"])
    noise = visual.create(builder, network, "noiseTOP", "noise")
    output = visual.create(builder, network, "nullTOP", "out")
    if baseline:
        visual.connect(builder, noise, output)
    else:
        tint = visual.create(builder, network, "constantTOP", "tint")
        multiply = visual.create(builder, network, "multiplyTOP", "multiply")
        visual.connect(builder, noise, multiply)
        visual.connect(builder, tint, multiply, index=1)
        visual.connect(builder, multiply, output)
    definitions = [{"name": name, "type": "float", "default": value}
                   for name, value in (("Speed", 1), ("Intensity", .8), ("Scale", 2), ("Red", .7),
                                       ("Green", .4), ("Blue", 1))]
    controls(visual, operator, network, definitions, baseline)
    if not baseline:
        actual = visual.call(builder, "tdflow_get_parameters", path=noise, names=["amp", "period", "tx"])
        visual.check("instance_parameters", mcp.ok(actual) and len(actual["parameters"]) == 3)
        visual.check("bind_motion", mcp.ok(visual.approve(operator, SET, path=noise, expressions={
            "amp": "parent().par.Intensity", "period": "parent().par.Scale", "tx": "absTime.seconds * parent().par.Speed"})))
        visual.check("bind_color", mcp.ok(visual.approve(operator, SET, path=tint, expressions={
            "colorr": "parent().par.Red", "colorg": "parent().par.Green", "colorb": "parent().par.Blue"})))
    expose_output(visual, builder, network, output, baseline)

    procedural = Scenario("B")
    network = procedural.create(builder, PARENT, "baseCOMP", "procedural")
    script = procedural.call(operator, CREATE, parent=network, type="scriptTOP", name="script")
    procedural.check("script_lifecycle_refused", script.get("rule_id") == "G2")
    color = procedural.create(builder, network, "constantTOP", "source")
    output = procedural.create(builder, network, "levelTOP", "out")
    logic = procedural.create(builder, network, "textDAT", "logic")
    procedural.connect(builder, color, output)
    controls(procedural, operator, network, [{"name": "Amount", "type": "float", "default": .5}], baseline)
    code = "def intensity():\n    return 0.4\n" if baseline else "def intensity(value):\n    return value * .8\n"
    preview = procedural.call(operator, "tdflow_get_dat_text", path=logic, proposed_text=code)
    procedural.check("code_preview", (preview.get("python_validation") or {}).get("ok") is True)
    edit = procedural.approve(operator, "tdflow_set_dat_text", path=logic, text=code, language="python",
                              expected_sha256=preview.get("source_sha256"))
    procedural.check("code_written", mcp.ok(edit))
    expression = "op('logic').module.intensity()" if baseline else "op('logic').module.intensity(float(parent().par.Amount))"
    applied = procedural.approve(operator, SET, path=output, expressions={"opacity": expression})
    procedural.check("expression_written", mcp.ok(applied))

    repair = Scenario("C")
    network = PARENT + "/repair"
    output = network + "/out"
    found = repair.call(builder, "tdflow_get_errors", path=network)
    repair.check("problem_visible", mcp.ok(found) and bool(found.get("problems")))
    description = repair.call(builder, "tdflow_describe_network", path=network)
    repair.check("missing_wire_visible", mcp.ok(description) and [network + "/source", output] not in description["edges"])
    fixed = repair.approve(operator, SET, path=output, values={"opacity": .5})
    repair.check("invalid_expression_replaced", mcp.ok(fixed))
    repair.connect(builder, network + "/source", output)

    reusable = Scenario("D")
    network = PARENT + "/reusable"
    source = reusable.create(builder, network, "constantTOP", "source")
    output = reusable.create(builder, network, "nullTOP", "out")
    reusable.connect(builder, source, output)
    existing = reusable.call(builder, "tdflow_get_parameters", path=network, page="Owner")
    reusable.check("inspect_existing_control", mcp.ok(existing) and any(p["name"] == "Keep" for p in existing["parameters"]))
    controls(reusable, operator, network, [{"name": "Amount", "type": "float", "default": .5}], baseline)
    if not baseline:
        reusable.check("bind_reusable_control", mcp.ok(reusable.approve(operator, SET, path=source,
                                                                       expressions={"colorr": "parent().par.Amount"})))
    expose_output(reusable, builder, network, output, baseline)
    return {"baseline": baseline}


def controls(scenario, operator, path, definitions, baseline):
    if baseline:
        result = scenario.call(operator, SET, path=path, values={p["name"]: p["default"] for p in definitions})
        scenario.check("parameter_definition_unavailable", result.get("code") == "INVALID_ARGS")
        return
    result = scenario.approve(operator, "tdflow_edit_custom_parameters", path=path, page="Controls",
                              definitions=definitions)
    scenario.check("controls_created", mcp.ok(result))


def expose_output(scenario, builder, path, source, baseline):
    result = scenario.call(builder, CREATE, parent=path, type="outTOP", name="output")
    if baseline:
        scenario.check("component_output_unavailable", result.get("rule_id") == "G2")
        return
    scenario.check("component_output_created", mcp.ok(result))
    scenario.connect(builder, source, path + "/output")


def diagnostics(data):
    repair = REPORT["C"]
    actor = mcp.MCP(mcp.TOKENS["B"])
    errors = repair.call(actor, "tdflow_get_errors", path=PARENT + "/repair")
    repair.check("error_gone", mcp.ok(errors) and not errors.get("problems"))
    unlocked = repair.call(actor, "tdflow_capture_top", path=data["output"])
    repair.check("unlocked_capture_refused", unlocked.get("code") == "TOP_LOCK_REQUIRED")
    locked = repair.call(actor, "tdflow_capture_top", path=data["snapshot"])
    repair.check("owner_locked_snapshot_visible", mcp.ok(locked) and locked.get("bytes", 0) > 0)


def definition_tests():
    s = Scenario("security")
    builder, operator = (mcp.MCP(mcp.TOKENS[key]) for key in ("B", "E"))
    path = PARENT + "/definitions"
    definitions = [{"name": "Owner", "type": "string", "default": "original"},
                   {"name": "Gain", "type": "float", "range": [-2, 2], "default": .5},
                   {"name": "Count", "type": "int", "default": 2},
                   {"name": "Enabled", "type": "toggle", "default": True},
                   {"name": "Mode", "type": "menu", "default": "soft",
                    "choices": [{"name": "hard"}, {"name": "soft", "label": "Soft"}]},
                   {"name": "Title", "type": "string", "default": "Flow"},
                   {"name": "Reset", "type": "pulse"}]
    args = {"path": path, "page": "Controls", "definitions": definitions}
    s.check("builder_cannot_define", s.call(builder, CUSTOM, **args).get("rule_id") == "R1")
    s.check("protected_component_denied", s.call(operator, CUSTOM, **dict(args, path="/project1/TDflowMCP")).get("rule_id") == "H3")
    s.check("parent_scope_denied", s.call(operator, CUSTOM, **dict(args, path="/project1")).get("_isError") is True)
    invalid = [{"name": "Clone", "type": "string", "default": "owner"}]
    s.check("reserved_name_refused", s.call(operator, CUSTOM, **dict(args, definitions=invalid)).get("code") == "INVALID_ARGS")
    answer = s.approve(operator, CUSTOM, **args)
    s.check("six_styles_and_native_order", mcp.ok(answer) and answer.get("parameters") == [d["name"] for d in definitions])
    s.check("durable_undo_advertised", answer.get("reversible") is True and bool(answer.get("audit_seq")))
    s.check("change_current_value", mcp.ok(s.call(builder, SET, path=path, values={"Gain": .8})))
    edit = [{"name": "Gain", "type": "float", "label": "Intensity", "default": .6, "range": [0, 2]}]
    order = list(reversed([item["name"] for item in definitions]))
    edited = s.approve(operator, CUSTOM, **dict(args, definitions=edit, order=order))
    s.check("metadata_edit_and_order", mcp.ok(edited) and edited.get("parameters") == order)
    visual = PARENT + "/visual"
    connected = s.call(builder, "tdflow_disconnect", src=visual + "/out", dst=visual + "/output")
    s.check("connected_component_output_cannot_be_rewired", connected.get("code") == "COMP_OUTPUT_IN_USE")
    broad = mcp.MCP(mcp.TOKENS["D"])
    protected = s.call(broad, CREATE, parent="/project1", type="outTOP", name="no_port")
    s.check("protected_ancestor_cannot_add_output", protected.get("rule_id") == "H5")
    inspected = s.call(operator, "tdflow_get_parameters", path=path, names=["Gain"])
    row = (inspected.get("parameters") or [{}])[0]
    s.check("metadata_keeps_current_value", row.get("label") == "Intensity" and row.get("value") == .8
            and row.get("default") == .6 and row.get("slider_range") == [0, 2])
    restored = s.call(operator, "tdflow_undo")
    s.check("verified_definition_undo", mcp.ok(restored) and restored.get("verified") is True)
    old = (s.call(operator, "tdflow_get_parameters", path=path, names=["Gain"]).get("parameters") or [{}])[0]
    s.check("undo_restores_metadata", old.get("label") == "Gain" and old.get("value") == .8
            and old.get("default") == .5 and old.get("slider_range") == [-2, 2])

    denied = s.call(operator, CUSTOM, **dict(args, page="Cancelled", definitions=[{"name": "One", "type": "float"}]))
    rows = (mcp.base.jbody(mcp.admin("GET", "/api/approvals")) or {}).get("approvals", [])
    row = next((a for a in rows if a.get("id") == denied.get("approval_id")), {})
    response = mcp.admin("POST", "/api/approvals/decide", {"id": row.get("id"), "decision": "denied",
                                                        "args_sha256": row.get("args_sha256"), "review_sha256": row.get("review_sha256")})
    s.check("owner_denial_recorded", response["status"] == 200)
    s.check("denied_call_stays_denied", s.call(operator, CUSTOM, **dict(args, page="Cancelled",
            definitions=[{"name": "One", "type": "float"}])).get("code") == "APPROVAL_DENIED")

    for key, pending in (("stale", dict(args, definitions=edit)),
                         ("replacement", dict(args, path=PARENT + "/replacement", definitions=edit))):
        req = s.call(operator, CUSTOM, **pending)
        s.check(key + "_approved", req.get("code") == "APPROVAL_REQUIRED"
                and mcp.approve_request(req["approval_id"])["status"] == 200)
        PENDING[key] = pending


def stale_tests():
    s = REPORT["security"]
    operator = mcp.MCP(mcp.TOKENS["E"])
    for key in ("stale", "replacement"):
        result = s.call(operator, CUSTOM, **PENDING[key])
        s.check(key + "_rejected", result.get("code") == "APPROVAL_TARGETS_CHANGED")


def rollback_test():
    s = REPORT["security"]
    operator = mcp.MCP(mcp.TOKENS["E"])
    result = s.approve(operator, CUSTOM, path=PARENT + "/definitions", page="Rollback",
                       definitions=[{"name": "Example", "type": "float", "default": .3}])
    s.check("failed_transaction_restored", result.get("code") == "EXECUTION_FAILED" and result.get("restored") is True)


def revocation_test():
    s = REPORT["security"]
    operator = mcp.MCP(mcp.TOKENS["E"])
    args = {"path": PARENT + "/definitions", "page": "Revoked", "definitions": [{"name": "Example", "type": "int"}]}
    req = s.call(operator, CUSTOM, **args)
    s.check("revoked_call_initially_approved", req.get("code") == "APPROVAL_REQUIRED"
            and mcp.approve_request(req["approval_id"])["status"] == 200)
    agents = (mcp.base.jbody(mcp.admin("GET", "/setup/api/agents")) or {}).get("agents", [])
    token_id = next(a for a in agents if a["display_name"].startswith("Agent E"))["tokens"][0]["token_id"]
    s.check("token_revoked", mcp.admin("POST", "/setup/api/tokens/revoke", {"token_id": token_id})["status"] == 200)
    s.check("revocation_prevents_redemption", s.call(operator, CUSTOM, **args).get("_http") == 401)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tox", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    if any(gate.oslisteners.other_listeners(port, 0) for port in gate.base.PORTS):
        raise SystemExit("A TouchDesigner test port is already in use.")
    work = Path(tempfile.mkdtemp(prefix="td-component-", dir=ROOT / ".tmp"))
    run = work / "run"
    project = work / "project"
    run.mkdir()
    project.mkdir()
    shutil.copyfile(Path(__file__).with_name("td_runner_component.py"), run / "td_runner_component.py")
    handlers = {"setup_ready": mcp.cp_setup_ready, "pair_approved": mcp.cp_pair_approved,
                "component_workflows": lambda _d, _p: scenarios(args.baseline),
                "component_diagnostics": lambda data, _p: diagnostics(data),
                "component_definition_tests": lambda _d, _p: definition_tests(),
                "component_definition_stale": lambda _d, _p: stale_tests(),
                "component_definition_rollback": lambda _d, _p: rollback_test(),
                "component_definition_revocation": lambda _d, _p: revocation_test()}
    tox = args.tox.resolve(strict=True)
    result = gate.launch("component_workflow", str(tox), str(run), str(project),
                         {"TDFLOW_PRIVATE_ROOT": str(work / "private")}, handlers, deadline_s=240)
    tests = result.get("tests", [])
    assertions = tests + gate.H["tests"]
    receipt = {"tox_sha256": hashlib.sha256(tox.read_bytes()).hexdigest(), "baseline": args.baseline,
               "td_build": result.get("td_build"), "component_version": result.get("component_version"),
               "scenarios": {name: {"mcp_calls": len(s.calls), "approvals": s.approvals, "calls": s.calls}
                             for name, s in REPORT.items()},
               "in_td": tests, "harness": gate.H["tests"], "errors": result.get("errors", [])}
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    failed = [t["name"] for t in assertions if not t["ok"]]
    print(json.dumps({"checks": len(assertions), "failed": failed, "errors": receipt["errors"],
                      "scenarios": {name: [len(s.calls), s.approvals] for name, s in REPORT.items()}}))
    return 0 if tests and not failed and not receipt["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
