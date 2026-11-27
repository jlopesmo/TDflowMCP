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


def external_output(data, verify_connectors):
    scenario = REPORT["D"]
    builder = mcp.MCP(mcp.TOKENS["B"])
    if verify_connectors:
        src = scenario.call(builder, "tdflow_get_operator", path=data["source"], connectors=True)
        dst = scenario.call(builder, "tdflow_get_operator", path=data["consumer"], connectors=True)
        outputs = src.get("connectors", {}).get("outputs", {}).get("rows")
        inputs = dst.get("connectors", {}).get("inputs", {}).get("rows")
        scenario.check("exposed_native_top_port", outputs == [{
            "index": 0, "peers": [], "complete": True,
            "exposed_output": {"path": data["source"] + "/output", "type": "outTOP", "family": "TOP"},
        }] and src.get("family") == "COMP")
        scenario.check("empty_consumer_input", inputs == [{"index": 0, "peers": [], "complete": True}])
    result = scenario.call(builder, "tdflow_connect", src=data["source"], dst=data["consumer"])
    scenario.check("native_comp_to_top_refused", result.get("code") == "INVALID_ARGS")


def verify_external_output(data):
    scenario = REPORT["D"]
    builder = mcp.MCP(mcp.TOKENS["B"])
    read = scenario.call(builder, "tdflow_get_operator", path=data["consumer"], connectors=True)
    rows = read.get("connectors", {}).get("inputs", {}).get("rows")
    scenario.check("exact_owner_wire_verified", rows == [{
        "index": 0, "peers": [{"path": data["source"], "index": 0}], "complete": True,
    }])
    create = mcp.admin("POST", "/setup/api/tokens", {
        "display_name": "Connector reader", "role": "observer", "scope": data["consumer"],
    })
    token = (gate.base.jbody(create) or {}).get("token")
    scenario.check("connector_probe_credential_created", bool(token))
    reader = mcp.MCP(token)
    partial = scenario.call(reader, "tdflow_get_operator", path=data["consumer"], connectors=True)
    rows = partial.get("connectors", {}).get("inputs", {}).get("rows")
    scenario.check("hidden_connector_peer_omitted",
                   rows == [{"index": 0, "peers": [], "complete": False}]
                   and json.dumps(data["source"]) not in json.dumps(partial))


def checkpoint_grants(data, phase):
    scenario = REPORT.get("checkpoint") or Scenario("checkpoint")
    actor = mcp.MCP(mcp.TOKENS["E"])
    if phase == "prepare":
        snap = scenario.call(actor, "tdflow_experiment_create", path=data["original"], name="checkpoint_copy", inert=True)
        edit = scenario.call(actor, SET, path=data["original"] + "/source", values={"amp": .75})
        scenario.check("prepared_constant_checkpoint", mcp.ok(snap) and mcp.ok(edit))
        return
    result = scenario.call(actor, "tdflow_experiment_promote", path=data["checkpoint"])
    if phase == "denied":
        scenario.check("restore_respects_parameter_denylist", result.get("rule_id") == "G3")
    else:
        scenario.check("restore_after_grant_allows_parameter", mcp.ok(result) and result.get("verified") is True)


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


def prepare_grant_tests(data):
    s = Scenario("control_grants")
    client = mcp.MCP(mcp.TOKENS["E"])
    args = {"path": data["path"], "page": "Controls",
            "definitions": [{"name": "Locked", "type": "float", "label": "Approved change"}]}
    s.check("editable_before_restriction", mcp.ok(s.approve(client, CUSTOM, **args)))
    args["definitions"] = [{"name": "Locked", "type": "float", "label": "Pending change"}]
    request = s.call(client, CUSTOM, **args)
    s.check("owner_approved_before_restriction", request.get("code") == "APPROVAL_REQUIRED"
            and mcp.approve_request(request["approval_id"])["status"] == 200)
    PENDING["restricted_replay"] = args


def restricted_grant_tests(data):
    s = REPORT["control_grants"]
    client = mcp.MCP(mcp.TOKENS["E"])
    path = data["path"]
    reply = s.call(client, CUSTOM, **PENDING["restricted_replay"])
    s.check("policy_change_denies_approved_replay", reply.get("rule_id") == "G3" and not reply.get("approval_id"))
    undo = s.call(client, "tdflow_undo")
    s.check("undo_respects_current_denylist", undo.get("rule_id") == "G3")
    reorder = s.call(client, CUSTOM, path=path, page="Controls", order=["Open", "Locked"])
    s.check("reorder_requires_all_control_permissions", reorder.get("rule_id") == "G3")
    mixed = s.call(client, CUSTOM, path=path, page="Controls", definitions=[
        {"name": "Open", "type": "float", "label": "Mixed"}, {"name": "Locked", "type": "float", "label": "Mixed"}])
    s.check("mixed_edit_denied_before_approval", mixed.get("rule_id") == "G3" and not mixed.get("approval_id"))
    return {}


def allowed_grant_tests(data):
    s = REPORT["control_grants"]
    client = mcp.MCP(mcp.TOKENS["E"])
    path = data["path"]
    result = s.approve(client, CUSTOM, path=path, page="Controls", order=["Locked", "Open"],
                       definitions=[{"name": "Open", "type": "float", "label": "Allowed change"}])
    s.check("allowed_control_with_unchanged_order", mcp.ok(result))


def allowed_grant_undo(data):
    s = REPORT["control_grants"]
    client = mcp.MCP(mcp.TOKENS["E"])
    result = s.call(client, "tdflow_undo")
    s.check("allowed_control_undo", mcp.ok(result) and result.get("verified") is True)


def page_limit_tests(data):
    s = Scenario("page_limit")
    client = mcp.MCP(mcp.TOKENS["E"])
    args = {"path": data["path"], "page": "Controls"}
    slots = [f"Slot{i}" for i in range(1, 81)]
    result = s.approve(client, CUSTOM, **args, definitions=[
        {"name": "Slot1", "type": "float", "default": 2}, {"name": "Slot80", "type": "float"}])
    s.check("add_eightieth_and_edit_existing", result.get("parameters") == slots)
    result = s.approve(client, CUSTOM, **args, definitions=[{"name": "Slot80", "type": "float", "label": "Last slot"}])
    s.check("edit_on_full_page", result.get("parameters") == slots)
    result = s.call(client, CUSTOM, **args, definitions=[{"name": "Slot81", "type": "float"}])
    s.check("reject_eighty_first_before_approval", result.get("code") == "INVALID_ARGS" and not result.get("approval_id"))
    order = list(reversed(slots))
    result = s.approve(client, CUSTOM, **args, order=order)
    s.check("reorder_at_capacity", result.get("parameters") == order)


def page_limit_undo():
    s = REPORT["page_limit"]
    result = s.call(mcp.MCP(mcp.TOKENS["E"]), "tdflow_undo")
    s.check("undo_reorder_at_capacity", mcp.ok(result) and result.get("verified") is True)


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
    parser.add_argument("--verify-connectors", action="store_true")
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
                "component_workflows": lambda _d, _p: dict(scenarios(args.baseline),
                                                           verify_connectors=args.verify_connectors),
                "component_external_wire": lambda d, _p: external_output(d, args.verify_connectors),
                "component_external_wire_verified": lambda d, _p: verify_external_output(d),
                "component_checkpoint_prepare": lambda d, _p: checkpoint_grants(d, "prepare"),
                "component_checkpoint_denied": lambda d, _p: checkpoint_grants(d, "denied"),
                "component_checkpoint_allowed": lambda d, _p: checkpoint_grants(d, "allowed"),
                "component_diagnostics": lambda data, _p: diagnostics(data),
                "component_definition_tests": lambda _d, _p: definition_tests(),
                "component_definition_stale": lambda _d, _p: stale_tests(),
                "component_definition_rollback": lambda _d, _p: rollback_test(),
                "component_grant_prepare": lambda d, _p: prepare_grant_tests(d),
                "component_grant_denials": lambda d, _p: restricted_grant_tests(d),
                "component_grant_allow": lambda d, _p: allowed_grant_tests(d),
                "component_grant_undo": lambda d, _p: allowed_grant_undo(d),
                "component_page_limit": lambda d, _p: page_limit_tests(d),
                "component_page_undo": lambda _d, _p: page_limit_undo(),
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
