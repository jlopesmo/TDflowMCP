"""Research Script staging, transfer and restart in new disposable TD processes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import traceback

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "spikes/v17"))
if __package__:
    from spikes.v17 import script_intent as intent
else:
    import script_intent as intent


FAMILIES = ("scriptCHOP", "scriptSOP", "scriptTOP")
CALLBACK = """from os import getpid

def _mark(node, key):
    node.store(key, node.fetch(key, 0) + 1)

def onSetupParameters(node):
    _mark(node, 'research_setup')

def setupParameters(node):
    onSetupParameters(node)

def onPulse(par):
    _mark(par.owner, 'research_pulse')

def onCook(node):
    _mark(node, 'research_cook')
    node.store('research_callback_pid', getpid())

def cook(node):
    onCook(node)
"""
FAULT = CALLBACK.replace("    node.store('research_callback_pid', getpid())",
                         "    node.store('research_callback_pid', getpid())\n"
                         "    raise RuntimeError('disposable callback fault')")
DRAFT = "import ctypes\nimport socket\nraise RuntimeError('this must remain draft text')\n"
TRIGGERS = ("research_setup", "research_pulse", "research_cook")
STATE = {}
RUNTIME = secrets.token_hex(16)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def counts(stage):
    return {kind: stats(stage.op(kind)) for kind in FAMILIES}


def stats(node):
    return {"cooks": int(node.totalCooks), **{key: int(node.fetch(key, 0)) for key in TRIGGERS},
            "callback_pid": node.fetch("research_callback_pid", None)}


def snapshot(stage, node, td):
    par = node.par.callbacks
    mode = str(par.mode).split(".")[-1]
    selected = par.eval() if mode == "CONSTANT" else None
    assert selected is not None and selected.OPType == "textDAT" and not selected.inputs
    assert selected.parent() == stage and not selected.par.file.val
    wires = [{"input": int(port.index), "output": int(link.index),
              "path": link.owner.path, "id": int(link.owner.id)}
             for port in node.inputConnectors for link in port.connections]
    return {"project": "disposable-research", "target": node.path, "type": node.OPType,
            "target_id": int(node.id), "selected_dat": selected.path, "callback_id": int(selected.id),
            "callback_mode": mode, "callback_source_kind": "authored_text",
            "source_sha256": intent.source_preview(selected.text)["sha256"], "parent": stage.path,
            "parent_id": int(stage.id), "inputs": [wire["path"] for wire in wires], "native_wires": wires,
            "cook_disabled": not bool(stage.allowCooking), "bypass": bool(node.bypass),
            "runtime": RUNTIME, "build": str(td.app.build), "policy": "research-fixture", "protected": False}


def approval(facts):
    return intent.digest({"action": "activate", "facts": facts})


def eligible(stage, facts, planned, **flags):
    grant = intent.Grant("fixture-agent", "disposable-research", stage.path, "activate", 2)
    return intent.eligibility(grant, facts, planned, actor="fixture-agent", action="activate", now=1, **flags)


def prepare_stage(project, td):
    stage = project.create(td.baseCOMP, "probe_bundle")
    stage.viewer = False
    stage.allowCooking = False
    first = stage.create(td.constantTOP, "input_a")
    second = stage.create(td.constantTOP, "input_b")
    first.viewer = second.viewer = False
    for kind in FAMILIES:
        node = stage.create(getattr(td, kind), kind)
        node.viewer = False
        factory = next(dat for dat in node.docked if dat.OPType == "textDAT")
        factory.viewer = False
        selected = stage.create(td.textDAT, kind + "_selected")
        selected.viewer = False
        selected.text = CALLBACK
        node.par.callbacks = selected.name
        node.appendCustomPage("Research").appendPulse("Researchpulse")
    stage.op("scriptTOP").inputConnectors[0].connect(first.outputConnectors[0])
    return stage


def export_stage(root, td):
    stage = STATE["stage"]
    before = counts(stage)
    facts = snapshot(stage, stage.op("scriptTOP"), td)
    artifact = root / "probe_bundle.tox"
    artifact.write_bytes(bytes(stage.saveByteArray()))
    write_json(root / "export-facts.json", {"facts": facts, "approval": approval(facts),
                                            "tox_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})
    after_export = counts(stage)
    for kind in FAMILIES:
        stage.op(kind).par.setuppars.pulse()
    after_setup = counts(stage)
    for kind in FAMILIES:
        stage.op(kind).par.Researchpulse.pulse()
    return {"draft_preview": STATE["draft_preview"], "draft_has_no_output_edges": STATE["draft_unlinked"],
            "before_export": before, "after_export": after_export, "after_setup": after_setup,
            "after_custom_pulses": counts(stage)}


def transfer_stage(root, td):
    stage = STATE["stage"]
    script = stage.op("scriptTOP")
    baseline = snapshot(stage, script, td)
    permit = approval(baseline)
    first_observation = STATE["initial"]
    after_wait = counts(stage)
    exported = read_json(root / "export-facts.json")

    selected = script.par.callbacks.eval()
    original_text = selected.text
    alternate = stage.create(td.textDAT, "alternate_authored")
    alternate.viewer = False
    alternate.text = original_text
    script.par.callbacks = alternate.name
    association_changed = eligible(stage, snapshot(stage, script, td), permit)
    factory_still_docked = any(dat.OPType == "textDAT" and dat != alternate for dat in script.docked)
    script.par.callbacks = selected.name
    selected.text = original_text + "\n# owner edit after the snapshot\n"
    source_changed = eligible(stage, snapshot(stage, script, td), permit)
    selected.text = original_text

    port = script.inputConnectors[0]
    port.disconnect()
    port.connect(stage.op("input_b").outputConnectors[0])
    topology_changed = eligible(stage, snapshot(stage, script, td), permit)
    port.disconnect()
    port.connect(stage.op("input_a").outputConnectors[0])
    restored = snapshot(stage, script, td)
    assert restored == baseline
    write_json(root / "restart-facts.json", {"facts": restored, "approval": permit, "path": stage.path})
    restart_toe = root / "cold_restart.toe"
    td.project.save(str(restart_toe))
    assert restart_toe.is_file()
    before_activation = counts(stage)

    # Owner-operated fixture controls. A fault changes only a disposable node's in-memory counter.
    STATE["receiver"].allowCooking = True
    stage.allowCooking = True
    for kind in FAMILIES:
        stage.op(kind).cook(force=True)
    explicit_activation = counts(stage)
    selected.text = FAULT
    try:
        script.cook(force=True)
    except Exception:
        pass
    after_fault = stats(script)
    fault_reported = bool(script.errors())
    selected.text = original_text
    stage.allowCooking = False
    STATE["receiver"].allowCooking = False
    after_rollback = snapshot(stage, script, td)
    counters_after_rollback = stats(script)
    return {"artifact_sha256_verified": STATE["artifact_verified"], "first_transfer": first_observation,
            "after_wait": after_wait, "baseline_gate": eligible(stage, baseline, permit),
            "old_export_approval": eligible(stage, baseline, exported["approval"]),
            "selected_source_matches_export": baseline["source_sha256"] == exported["facts"]["source_sha256"],
            "internal_wire_retained": len(baseline["native_wires"]) == 1
            and baseline["native_wires"][0]["path"] == stage.op("input_a").path,
            "association_changed": association_changed, "factory_still_docked": factory_still_docked,
            "source_changed": source_changed, "input_changed": topology_changed,
            "before_activation": before_activation, "explicit_activation": explicit_activation,
            "fault_reported": fault_reported, "after_fault": after_fault,
            "graph_rollback_matches": after_rollback == baseline, "after_rollback": counters_after_rollback,
            "restart_toe": restart_toe.name}


def begin(mode, root, td, frame):
    project = td.op("/project1")
    if mode == "export":
        drafts = project.create(td.baseCOMP, "detached_drafts")
        drafts.allowCooking = False
        text = drafts.create(td.textDAT, "unlinked_text")
        text.viewer = False
        text.text = DRAFT
        STATE.update(stage=prepare_stage(project, td), draft_preview=intent.source_preview(text.text),
                     draft_unlinked=not text.inputs and not text.outputs)
    elif mode == "transfer":
        receiver = project.create(td.baseCOMP, "receiver")
        receiver.viewer = False
        receiver.allowCooking = False
        artifact = root / "probe_bundle.tox"
        expected = read_json(root / "export-facts.json")["tox_sha256"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected
        stage = receiver.loadTox(str(artifact), unwired=True)
        STATE.update(stage=stage, receiver=receiver, initial=counts(stage), artifact_verified=True)
    else:
        saved = read_json(root / "restart-facts.json")
        stage = td.op(saved["path"])
        STATE.update(stage=stage, initial=counts(stage), saved=saved)
    STATE["frame"] = frame


def observe_restart(td):
    stage = STATE["stage"]
    saved = STATE["saved"]
    facts = snapshot(stage, stage.op("scriptTOP"), td)
    return {"first_restart": STATE["initial"], "after_wait": counts(stage),
            "source_matches": facts["source_sha256"] == saved["facts"]["source_sha256"],
            "cook_disabled": facts["cook_disabled"], "runtime_changed": facts["runtime"] != saved["facts"]["runtime"],
            "old_approval": eligible(stage, facts, saved["approval"]), "fresh_review": eligible(stage, facts, approval(facts))}


def on_frame(me, frame):
    import td

    root = Path(os.environ["TDFLOW_ADVANCED_PROBE_ROOT"])
    mode = os.environ["TDFLOW_ADVANCED_PROBE_MODE"]
    try:
        if not STATE:
            begin(mode, root, td, frame)
            return
        if frame < STATE["frame"] + 16:
            return
        outcome = (export_stage(root, td) if mode == "export" else
                   transfer_stage(root, td) if mode == "transfer" else observe_restart(td))
        result = {"mode": mode, "build": str(td.app.build), "pid": os.getpid(), "evidence": outcome}
    except Exception:
        result = {"error": traceback.format_exc()}
    write_json(Path(os.environ["TDFLOW_RUN_DIR"]) / "observation.json", result)
    td.project.quit(force=True)


def check_evidence(receipts):
    def cold(observed):
        return all(row["cooks"] == 0 and row["callback_pid"] is None
                   and all(row[key] == 0 for key in TRIGGERS) for row in observed.values())

    export = receipts["export"]["result"]["evidence"]
    transfer = receipts["transfer"]["result"]["evidence"]
    restart = receipts["restart"]["result"]["evidence"]
    receiving_pid = receipts["transfer"]["result"]["pid"]
    return {
        "text_preview_unlinked": export["draft_has_no_output_edges"]
        and export["draft_preview"]["imports"] == ["ctypes", "socket"],
        "cold_export": cold(export["before_export"]) and cold(export["after_export"]),
        "setup_reproduces_no_cook_gate_failure": all(row["cooks"] > 0 and row["research_cook"] == 0
                                                   for row in export["after_setup"].values()),
        "cold_transfer_restarts_without_callbacks": all(cold(group) for group in (
            transfer["first_transfer"], transfer["after_wait"], restart["first_restart"], restart["after_wait"])),
        "import_keeps_exact_selected_source_and_internal_wire": transfer["artifact_sha256_verified"]
        and transfer["selected_source_matches_export"] and transfer["internal_wire_retained"],
        "only_exact_intent_reaches_review": transfer["baseline_gate"] == "eligible_for_review"
        and transfer["factory_still_docked"]
        and all(transfer[key] == "changed" for key in ("old_export_approval", "association_changed", "source_changed", "input_changed")),
        "restart_requires_fresh_review": restart["source_matches"] and restart["runtime_changed"]
        and restart["old_approval"] == "changed" and restart["fresh_review"] == "eligible_for_review",
        "all_activated_callbacks_run_in_receiving_process": all(row["research_cook"] == 1
                                                               and row["callback_pid"] == receiving_pid
                                                               for row in transfer["explicit_activation"].values()),
        "graph_rollback_does_not_reverse_callback_effect": transfer["fault_reported"] and transfer["graph_rollback_matches"]
        and transfer["after_rollback"]["research_cook"] > transfer["explicit_activation"]["scriptTOP"]["research_cook"],
    }


def main():
    repo = Path(__file__).resolve().parents[2]
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.path.insert(0, str(repo / "tools"))
    from tdtoe import boot_script, make_toe
    from tdrun import TDProcess

    (repo / ".tmp").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v17-script-", dir=repo / ".tmp")).resolve()
    receipts = {}
    for phase in ("export", "transfer", "restart"):
        run = work / phase
        run.mkdir()
        shutil.copyfile(__file__, run / "advanced_script_probe.py")
        shutil.copyfile(repo / "spikes/v17/script_intent.py", run / "script_intent.py")
        toe = (work / "cold_restart.toe" if phase == "restart" else
               Path(make_toe(str(run), phase, boot_script("advanced_script_probe", start_frame=30))))
        assert toe.resolve().is_relative_to(work)
        td = TDProcess(str(toe), str(run), {"TDFLOW_ADVANCED_PROBE_MODE": phase,
                                          "TDFLOW_ADVANCED_PROBE_ROOT": str(work)})
        report = td.wait_file("observation.json", 90)
        code = td.finish(8)
        receipts[phase] = {"result": report, "exit": code, "others_untouched": td.others_untouched(), "killed": td.killed}
        if (not report or report.get("error") or report.get("build") != "2025.32440"
                or code != 0 or td.killed or not td.others_untouched()):
            break
    complete = len(receipts) == 3 and all(item["result"] and not item["result"].get("error")
                                        and item["result"].get("build") == "2025.32440"
                                        and item["exit"] == 0 and not item["killed"] and item["others_untouched"]
                                        for item in receipts.values())
    checks = check_evidence(receipts) if complete else {}
    report = work / "script-transfer.json"
    write_json(report, {"phases": receipts, "checks": checks})
    print(json.dumps({"report": str(report), "checks": checks}))
    return 0 if complete and all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
