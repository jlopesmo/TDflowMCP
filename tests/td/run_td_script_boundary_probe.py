"""Issue #24 research fixture for data-only transfer and a lost native outcome."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import traceback

if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo / "spikes/v17"), str(Path(__file__).resolve().parent)]

if __package__:
    from spikes.v17.recovery_ledger import Ledger, Refused
    from spikes.v17.script_intent import source_preview
    from spikes.v17.transfer_review import HOST_WARNING, Lease, binding, read_manifest, verify_transfer
    from .run_td_advanced_script_probe import FAMILIES, counts, prepare_stage, read_json, write_json
else:
    from recovery_ledger import Ledger, Refused
    from run_td_advanced_script_probe import FAMILIES, counts, prepare_stage, read_json, write_json
    from script_intent import source_preview
    from transfer_review import HOST_WARNING, Lease, binding, read_manifest, verify_transfer


BUILD = "2025.32440"
POLICY = "issue-24-disposable"
AGENT = "fixture-agent"
PHASES = ("source", "owner", "fault", "crash", "restart")
CALLBACK = """from os import environ, fsync, getpid
from pathlib import Path

def mark(label):
    with (Path(environ['TDFLOW_RUN_DIR']) / 'fixture-effect.txt').open('a', encoding='ascii') as handle:
        handle.write(label + ':' + str(getpid()) + '\\n')
        handle.flush()
        fsync(handle.fileno())

mark('module')

def onSetupParameters(node):
    node.store('research_setup', node.fetch('research_setup', 0) + 1)

def setupParameters(node):
    onSetupParameters(node)

def onPulse(par):
    node = par.owner
    node.store('research_pulse', node.fetch('research_pulse', 0) + 1)

def onCook(node):
    node.store('research_cook', node.fetch('research_cook', 0) + 1)
    node.store('research_callback_pid', getpid())
    mark(node.name)
    if environ['TDFLOW_V17_PHASE'] == 'fault' and node.name == 'scriptTOP':
        raise RuntimeError('disposable callback fault')

def cook(node):
    onCook(node)
"""
STATE = {}
EPOCH = secrets.token_hex(16)


def effects(run):
    file = run / "fixture-effect.txt"
    return file.read_text(encoding="ascii").splitlines() if file.is_file() else []


def wires(node):
    return sorted((connection.owner.name, int(connection.index), int(port.index))
                  for port in node.inputConnectors for connection in port.connections)


def manifest_for(stage):
    rows = []
    for family in FAMILIES:
        node = stage.op(family)
        selected = node.par.callbacks.eval()
        rows.append(dict(family=family, operator=node.name, callback=selected.name, source=selected.text,
                         wires=wires(node)))
    return json.dumps({"schema": 1, "operators": rows}, ensure_ascii=True).encode("ascii")


def observed(stage, build, project, phase):
    rows = []
    for family in FAMILIES:
        node = stage.op(family)
        mode = str(node.par.callbacks.mode).split(".")[-1]
        if mode != "CONSTANT":
            raise ValueError("Dynamic callback selection is outside the fixture policy.")
        selected = node.par.callbacks.eval()
        if (selected is None or selected.OPType != "textDAT" or selected.parent() != stage
                or selected.inputs or selected.par.file.val):
            raise ValueError("Only an unwired, authored local callback DAT can be reviewed.")
        rows.append({"family": family, "path": node.path, "id": int(node.id), "callback_path": selected.path,
                     "callback_id": int(selected.id), "callback_mode": mode, "source_kind": "authored_text",
                     "source": source_preview(selected.text), "parent": stage.path, "parent_id": int(stage.id),
                     "dependencies": [connection.owner.path for port in node.inputConnectors
                                      for connection in port.connections], "wires": wires(node),
                     "outputs": sorted((connection.owner.path, int(port.index), int(connection.index))
                                       for port in node.outputConnectors for connection in port.connections),
                     "protected": False, "bypass": bool(node.bypass), "viewer": bool(node.viewer),
                     "value": node.par.Researchvalue.val})
    return {"project": project, "build": build, "epoch": EPOCH, "policy": POLICY, "operators": rows,
            "parent_cooking": bool(stage.allowCooking), "project_cooking": bool(stage.parent().allowCooking),
            "phase_dependency": phase, "trigger": "owner force cook", "modules": fixture_modules()}


def fixture_modules():
    loaded = {}
    for name in ("os", "pathlib"):
        spec = sys.modules[name].__spec__
        if spec.origin in ("built-in", "frozen"):
            loaded[name] = {"kind": spec.origin, "python": list(sys.version_info[:3])}
        else:
            loaded[name] = {"kind": "stdlib bytes", "sha256": hashlib.sha256(spec.loader.get_data(spec.origin)).hexdigest()}
    return loaded


def safe_construct(project, td):
    stage = prepare_stage(project, td)
    for family in FAMILIES:
        node = stage.op(family)
        selected = node.par.callbacks.eval()
        selected.text = CALLBACK
        node.customPages[0].appendFloat("Researchvalue")
    for family, native_type in (("scriptCHOP", td.constantCHOP), ("scriptSOP", td.boxSOP)):
        inp = stage.create(native_type, family + "_input")
        inp.viewer = False
        stage.op(family).inputConnectors[0].connect(inp.outputConnectors[0])
    return stage


def evidence_for(stage, build, project, phase, action, manifest_hash, **stop):
    lease = Lease(action + "-lease", AGENT, project, stage.path, action, 20)
    live = observed(stage, build, project, phase)
    if action == "author":
        # This digest refers to the receiving project and the inert manifest, not future runtime IDs.
        live["operators"] = []
    return binding(action, AGENT, lease, manifest_hash, live, now=10, policy=POLICY, **stop)


def authored_binding(project, build, phase, manifest_hash):
    lease = Lease("author-lease", AGENT, project, "/project1/probe_bundle", "author", 20)
    live = dict(project=project, build=build, epoch=EPOCH, policy=POLICY, operators=[],
                parent_cooking=False, project_cooking=True, phase_dependency=phase, trigger="owner force cook",
                modules=fixture_modules())
    return binding("author", AGENT, lease, manifest_hash, live, now=10, policy=POLICY)


def trigger_matrix(stage, run):
    def sample():
        return {"counts": counts(stage), "effects": effects(run)}

    journal = {"before": sample()}
    for family in FAMILIES:
        stage.op(family).par.setuppars.pulse()
    journal["setup_pulses"] = sample()
    for family in FAMILIES:
        stage.op(family).par.Researchpulse.pulse()
    journal["custom_pulses"] = sample()
    for family in FAMILIES:
        node = stage.op(family)
        node.par.Researchvalue = 0.25
        node.bypass = True
        node.bypass = False
        port = node.inputConnectors[0]
        source = port.connections[0]
        port.disconnect()
        port.connect(source)
    journal["parameter_bypass_and_inputs"] = sample()
    return journal


def refusal(callback, error):
    try:
        callback()
    except error:
        return True
    return False


def receive(stage, td, build, root, run, phase):
    project, manifest_hash = STATE["project"], STATE["manifest"]
    ledger = Ledger(root / (phase + ".sqlite"))
    live = observed(stage, build, project, phase)
    author = evidence_for(stage, build, project, phase, "author", manifest_hash)
    assert author == STATE["author"]
    permit = evidence_for(stage, build, project, phase, "activate", manifest_hash)
    receipt = {"manifest": manifest_hash, "initial": STATE["initial"], "cold": counts(stage),
               "cold_effects": effects(run), "warning": HOST_WARNING, "snapshot": live}
    ledger.owner_review("activate", "activate", permit, 20)
    if phase == "owner":
        node = stage.op("scriptTOP")
        original = node.par.callbacks.eval()
        alternate = stage.create(td.textDAT, "same_text_alternate")
        alternate.viewer = False
        alternate.text = original.text
        node.par.callbacks = alternate.name
        receipt["stale_selected_dat"] = refusal(lambda: ledger.begin(
            "changed", "author", author, "activate", evidence_for(stage, build, project, phase, "activate", manifest_hash),
            now=10), Refused)
        node.par.callbacks = original.name
        node.par.callbacks.expr = "'scriptTOP_selected'"
        receipt["dynamic_callback_denied"] = refusal(
            lambda: observed(stage, build, project, phase), ValueError)
        node.par.callbacks.mode = type(node.par.callbacks.mode).CONSTANT
        node.par.callbacks = original.name
        receipt["revoked_before_dispatch"] = refusal(lambda: evidence_for(
            stage, build, project, phase, "activate", manifest_hash, revoked=True), ValueError)
        ledger.owner_review("race-author", "author", author, 20)
        ledger.owner_review("race-activate", "activate", permit, 20)
        ledger.begin("edit-after-intent", "race-author", author, "race-activate", permit, now=10)
        node.par.callbacks = alternate.name
        receipt["late_edit_cancelled"] = evidence_for(stage, build, project, phase, "activate", manifest_hash) != permit
        assert receipt["late_edit_cancelled"]
        ledger.finish("edit-after-intent", "cancelled")
        receipt["late_edit_effects"] = effects(run)
        node.par.callbacks = original.name
        assert read_manifest(manifest_for(stage))[1] == manifest_hash
        assert observed(stage, build, project, phase) == live
    ledger.begin("native", "author", author, "activate", permit, now=10)
    if evidence_for(stage, build, project, phase, "activate", manifest_hash) != permit:
        ledger.finish("native", "cancelled")
        raise RuntimeError("Native facts changed after recording the exact intent.")
    stage.allowCooking = True
    for family in ("scriptTOP",) if phase == "crash" else FAMILIES:
        stage.op(family).cook(force=True)
    receipt.update(activated=counts(stage), effects_after=effects(run), fault=bool(stage.op("scriptTOP").errors()))
    stage.allowCooking = False
    receipt["graph_restored"] = observed(stage, build, project, phase) == live
    receipt["effects_after_graph_restore"] = effects(run)
    if phase == "crash":
        write_json(run / "pending.json", {"pid": os.getpid(), "build": build, "receipt": receipt})
        return None
    ledger.finish("native", "fault" if receipt["fault"] else "observed")
    receipt["ledger"] = ledger.status()
    receipt["replay_refused"] = refusal(lambda: ledger.begin("replay", "author", author, "activate", permit, now=10), Refused)
    return receipt


def begin(root, run, td, frame, phase):
    project = td.op("/project1")
    project_id = "fixture-" + phase
    build = str(td.app.build)
    if phase in ("owner", "fault", "crash"):
        source = read_json(root / "source-facts.json")
        manifest, received = verify_transfer((root / "draft.json").read_bytes(), source["bytes_sha256"], source["manifest"])
        # The research runner does not execute arbitrary content, even if its checksum matches.
        assert all(row["source"] == CALLBACK for row in manifest["operators"])
        author = authored_binding(project_id, build, phase, received)
        Ledger(root / (phase + ".sqlite")).owner_review("author", "author", author, 20)
        STATE.update(manifest=received, author=author)
    stage = safe_construct(project, td)
    if phase in ("owner", "fault", "crash"):
        assert read_manifest(manifest_for(stage))[1] == STATE["manifest"]
    STATE.update(stage=stage, frame=frame, project=project_id, initial=counts(stage), initial_effects=effects(run))


def on_frame(me, frame):
    import td

    root = Path(os.environ["TDFLOW_V17_PROBE"])
    run = Path(os.environ["TDFLOW_RUN_DIR"])
    phase = os.environ["TDFLOW_V17_PHASE"]
    try:
        if not STATE:
            begin(root, run, td, frame, phase)
            return
        if frame < STATE["frame"] + 16 or STATE.get("pending"):
            return
        stage = STATE["stage"]
        build = str(td.app.build)
        if phase == "source":
            raw = manifest_for(stage)
            data, manifest_hash = read_manifest(raw)
            (root / "draft.json").write_bytes(raw)
            export = observed(stage, build, STATE["project"], phase)
            write_json(root / "source-facts.json", {"manifest": manifest_hash, "bytes_sha256": hashlib.sha256(raw).hexdigest(),
                                                   "epoch": EPOCH, "project": STATE["project"]})
            result = {"manifest": manifest_hash, "facts": export, "imports": data["operators"][0]["source_facts"]["imports"],
                      "triggers": trigger_matrix(stage, run)}
        elif phase == "restart":
            saved = read_json(root / "crash" / "pending.json")
            old = saved["receipt"]["snapshot"]
            result = {"ledger": Ledger(root / "crash.sqlite").status(), "new_epoch": old["epoch"] != EPOCH,
                      "fresh_project": old["project"] != STATE["project"], "cold": counts(stage), "effects": effects(run),
                      "prior_effects_read_only": effects(root / "crash"), "restart_source": read_manifest(manifest_for(stage))[1]}
            prior_lease = Lease("activate-lease", AGENT, old["project"], stage.path, "activate", 20)
            result["cross_project_denied"] = refusal(lambda: binding(
                "activate", AGENT, prior_lease, result["restart_source"], observed(stage, build, STATE["project"], phase),
                now=10, policy=POLICY), ValueError)
        else:
            result = receive(stage, td, build, root, run, phase)
            if result is None:
                STATE["pending"] = True
                return
        report = {"build": build, "pid": os.getpid(), "result": result}
    except Exception:
        report = {"error": traceback.format_exc()}
    write_json(run / "observation.json", report)
    td.project.quit(force=True)


def checks_for(reports, recovery):
    def cold(sample):
        return all(row == {"cooks": 0, "research_setup": 0, "research_pulse": 0, "research_cook": 0,
                           "callback_pid": None} for row in sample.values())

    def activated(report, families):
        state = report["result"]
        return all(state["activated"][name]["callback_pid"] == report["pid"]
                   and f"{name}:{report['pid']}" in state["effects_after"] for name in families)

    source, owner, fault, crash, restart = (reports[phase]["result"]
                                            for phase in ("source", "owner", "fault", "crash", "restart"))
    return {
        "data_manifest_source_and_native_receivers_match": all(
            row["manifest"] == source["manifest"] for row in (owner, fault, crash)) and
            restart["restart_source"] == source["manifest"] and source["imports"] == ["os", "pathlib"],
        "all_native_scopes_start_cold": all(cold(row["initial"]) and cold(row["cold"]) and not row["cold_effects"]
                                           for row in (owner, fault, crash)) and cold(restart["cold"]) and not restart["effects"],
        "disabled_parent_does_not_prevent_setup_cooks": all(
            row["cooks"] > 0 for row in source["triggers"]["setup_pulses"]["counts"].values()),
        "same_source_wrong_dat_and_dynamic_callback_refused": owner["stale_selected_dat"] and owner["dynamic_callback_denied"],
        "revocation_refused_before_dispatch": owner["revoked_before_dispatch"],
        "late_edit_after_intent_cancelled_without_effects": owner["late_edit_cancelled"] and not owner["late_edit_effects"],
        "fresh_owner_activation_executed_all_families": activated(reports["owner"], FAMILIES)
        and owner["warning"] == HOST_WARNING and owner["ledger"] == [False, {"native": "observed", "edit-after-intent": "cancelled"}],
        "separate_reviews_consumed_without_replay": owner["replay_refused"] and fault["replay_refused"],
        "fault_and_graph_restore_preserve_external_marker": fault["fault"] and fault["graph_restored"]
        and activated(reports["fault"], FAMILIES) and fault["effects_after_graph_restore"] == fault["effects_after"]
        and fault["ledger"] == [True, {"native": "fault"}],
        "native_process_exit_yields_unknown_and_cold_restart": activated(reports["crash"], ("scriptTOP",))
        and recovery == 1 and restart["ledger"] == [True, {"native": "unknown"}]
        and restart["new_epoch"] and restart["fresh_project"] and restart["cross_project_denied"]
        and restart["prior_effects_read_only"] == crash["effects_after_graph_restore"],
    }


def phase_accepted(phase, report, pid, status, recovery):
    if (not report or report.get("build") != BUILD or report.get("pid") != pid or "result" not in report
            or not status["other_processes_untouched"]):
        return False
    if phase == "crash":
        return status["exit"] != 0 and status["killed"] and recovery == 1
    return status["exit"] == 0 and not status["killed"]


def run_accepted(processes):
    return tuple(processes) == PHASES and all(row["accepted"] for row in processes.values())


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.path.insert(0, str(repo / "tools"))
    from tdtoe import boot_script, make_toe
    from tdrun import TDProcess

    scratch = repo / ".tmp"
    scratch.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v17-boundary-", dir=scratch)).resolve()
    sources = {Path(__file__), Path(__file__).with_name("run_td_advanced_script_probe.py"),
               *(repo / "spikes/v17" / name for name in ("script_intent.py", "transfer_review.py", "recovery_ledger.py"))}
    frozen_sources = {path: path.read_bytes() for path in sorted(sources)}
    reports = {}
    processes = {}
    recovery = 0
    for phase in PHASES:
        run = work / phase
        run.mkdir()
        if phase in ("owner", "fault", "crash"):
            Ledger.create(work / (phase + ".sqlite"))
        for source, content in frozen_sources.items():
            (run / source.name).write_bytes(content)
        toe = Path(make_toe(str(run), phase, boot_script(Path(__file__).stem, start_frame=30))).resolve()
        assert toe.is_relative_to(work)
        process = TDProcess(str(toe), str(run), {"TDFLOW_V17_PROBE": str(work), "TDFLOW_V17_PHASE": phase})
        try:
            if phase == "crash":
                pending = process.wait_file("pending.json", 25)
                if pending and pending["pid"] == process.pid and pending["build"] == BUILD:
                    state = Ledger(work / "crash.sqlite").status()
                    code = process.finish(0)
                    if state == (False, {"native": "dispatching"}) and process.killed and code != 0 and not process.alive():
                        recovery = Ledger(work / "crash.sqlite").recover_after_exit()
                        reports[phase] = {"build": pending["build"], "pid": pending["pid"], "result": pending["receipt"]}
                else:
                    reports[phase] = process.wait_file("observation.json", 1)
                    code = process.finish(1)
            else:
                reports[phase] = process.wait_file("observation.json", 30)
                code = process.finish(8)
        finally:
            if process.alive():
                process.finish(0)
        others_untouched = process.others_untouched()
        status = {"exit": code, "killed": process.killed, "other_processes_untouched": others_untouched}
        status["accepted"] = phase_accepted(phase, reports.get(phase), process.pid, status, recovery)
        processes[phase] = status
        if not status["accepted"]:
            break
    complete = run_accepted(processes)
    checks = checks_for(reports, recovery) if complete else {}
    report = work / "boundary-evidence.json"
    write_json(report, {"processes": processes, "reports": reports, "checks": checks,
                        "source_sha256": {path.relative_to(repo).as_posix(): hashlib.sha256(content).hexdigest()
                                          for path, content in frozen_sources.items()}})
    print(json.dumps({"report": str(report), "checks": checks,
                      "errors": {phase: (row or {}).get("error", "no observation")
                                 for phase, row in reports.items() if not row or "error" in row}}))
    return 0 if complete and all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
