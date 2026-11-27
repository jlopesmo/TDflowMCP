"""Disposable TD lifecycle and local operator metadata probe for Issue #14."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback


CALLBACKS = """def onSetupParameters(scriptOp):
    scriptOp.store('probe_setup', scriptOp.fetch('probe_setup', 0) + 1)

def setupParameters(scriptOp):
    onSetupParameters(scriptOp)

def onPulse(par):
    par.owner.store('probe_pulse', par.owner.fetch('probe_pulse', 0) + 1)

def onCook(scriptOp):
    scriptOp.store('probe_cook', scriptOp.fetch('probe_cook', 0) + 1)
"""

_STAGE = {}


def counts(node):
    return {"cooks": int(node.totalCooks),
            **{key: int(node.fetch("probe_" + key, 0)) for key in ("setup", "pulse", "cook")}}


def save_result(result, td):
    path = Path(os.environ["TDFLOW_RUN_DIR"]) / "observation.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    td.project.quit(force=True)


def on_frame(me, frame):
    import td

    try:
        if not _STAGE:
            build_stage(td, frame)
        elif frame >= _STAGE["frame"] + 8 and "after_inspection" not in _STAGE["phases"]:
            inspect_stage()
        elif frame >= _STAGE["frame"] + 16 and "after_setup_pulses" not in _STAGE["phases"]:
            observe_setup()
        elif frame >= _STAGE["frame"] + 24 and "after_custom_pulses" not in _STAGE["phases"]:
            observe_pulses()
        elif frame >= _STAGE["frame"] + 32:
            observe_association(td)
    except Exception:
        save_result({"error": traceback.format_exc()}, td)


def build_stage(td, frame):
    root = td.op("/project1")
    stage = root.create(td.baseCOMP, "lifecycle_probe")
    stage.allowCooking = False
    fixture = root.create(td.baseCOMP, "parameter_metadata")
    fixture.allowCooking = False
    nodes = {}
    for name in ("scriptCHOP", "scriptSOP", "scriptTOP"):
        node = stage.create(getattr(td, name))
        node.viewer = False
        factory = next(d for d in node.docked if d.OPType == "textDAT")
        assert not factory.inputs, "factory DAT has input connections"
        factory.text = CALLBACKS
        node.appendCustomPage("Evidence").appendPulse("Probeconfirm")
        nodes[name] = node, factory
    parameters = {}
    for name in ("constantTOP", "noiseTOP", "levelTOP", "nullTOP", "outTOP", "noiseCHOP", "mathCHOP",
                 "nullCHOP", "parameterexecuteDAT", "textDAT"):
        node = fixture.create(getattr(td, name))
        node.viewer = False
        parameters[name] = [{"name": par.name, "label": par.label, "style": par.style} for par in node.pars()]
    _STAGE.update(frame=frame, stage=stage, nodes=nodes, parameters=parameters,
                  phases={"before_inspection": {name: counts(node) for name, (node, _) in nodes.items()}})


def inspect_stage():
    associations = {}
    for name, (node, factory) in _STAGE["nodes"].items():
        mode = str(node.par.callbacks.mode).split(".")[-1]
        configured = node.par.callbacks.eval() if mode == "CONSTANT" else None
        associations[name] = {"mode": mode, "factory_selected": configured == factory,
                              "factory_docked": factory in node.docked,
                              "text_sha256": hashlib.sha256(factory.text.encode()).hexdigest()}
    _STAGE["associations"] = associations
    _STAGE["phases"]["after_inspection"] = {
        name: counts(node) for name, (node, _) in _STAGE["nodes"].items()
    }
    for node, _ in _STAGE["nodes"].values():
        node.par.setuppars.pulse()


def observe_setup():
    _STAGE["phases"]["after_setup_pulses"] = {name: counts(node) for name, (node, _) in _STAGE["nodes"].items()}
    for node, _ in _STAGE["nodes"].values():
        node.par.Probeconfirm.pulse()


def observe_pulses():
    import td

    _STAGE["phases"]["after_custom_pulses"] = {name: counts(node) for name, (node, _) in _STAGE["nodes"].items()}
    stage = _STAGE["stage"]
    alt = stage.create(td.textDAT, "alternate_callbacks")
    alt.text = "# Empty callback source for an owner-side association check.\n"
    _STAGE["alternate"] = alt
    for node, _ in _STAGE["nodes"].values():
        node.par.callbacks = alt.path


def observe_association(td):
    alternate = _STAGE["alternate"]
    changes = {}
    for name, (node, factory) in _STAGE["nodes"].items():
        configured = node.par.callbacks.eval()
        changes[name] = {"selected_alternate": configured == alternate, "factory_still_docked": factory in node.docked}
    save_result({"build": str(td.app.build), "stage_cooking_enabled": bool(_STAGE["stage"].allowCooking),
                 "phases": _STAGE["phases"], "association": _STAGE["associations"], "association_change": changes,
                 "parameters": _STAGE["parameters"]}, td)


def main():
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=repo / ".tmp/v15-lifecycle-observations.json")
    results = parser.parse_args().out.resolve()
    sys.path.insert(0, str(repo / "tools"))
    from tdtoe import boot_script, make_toe
    from tdrun import TDProcess

    (repo / ".tmp").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v15-lifecycle-", dir=repo / ".tmp"))
    shutil.copyfile(__file__, work / "script_probe.py")
    toe = make_toe(str(work), "lifecycle", boot_script("script_probe", start_frame=20))
    td = TDProcess(toe, str(work))
    report = td.wait_file("observation.json", 80)
    code = td.finish(8)
    if not report or report.get("error") or report.get("build") != "2025.32440" or code != 0 or td.killed or not td.others_untouched():
        print(json.dumps({"exit": code, "others_untouched": td.others_untouched(), "report": report}, default=str))
        return 1
    results.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "parameters"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
