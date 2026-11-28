"""Check the curated operator descriptions against disposable TouchDesigner metadata."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def on_frame(me, frame):
    import td

    work = Path(os.environ["TDFLOW_RUN_DIR"])
    types = json.loads((work / "types.json").read_text(encoding="utf-8"))
    parent = td.op("/project1").create(td.baseCOMP, "guidance_fixture")
    parent.allowCooking = False
    report = {"build": str(td.app.build), "types": {}, "errors": []}
    for name in types:
        try:
            instance = parent.create(getattr(td, name), "op_" + name)
            instance.viewer = False
            if instance.OPType != name:
                report["errors"].append({"type": name, "actual": instance.OPType})
            report["types"][name] = {"family": instance.family, "parameters": {
                p.name: [p.label, p.style] for p in instance.pars()}}
        except Exception as error:
            report["errors"].append({"type": name, "error": type(error).__name__})
    (work / "guidance.json").write_text(json.dumps(report), encoding="utf-8")
    td.project.quit(force=True)


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "tools"), str(root / "src")]
    from tdflowmcp import opguides
    from tdtoe import boot_script, make_toe
    from tdrun import TDProcess

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    (root / ".tmp").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="td-guidance-", dir=root / ".tmp"))
    shutil.copyfile(__file__, work / "operator_guidance.py")
    types = sorted(opguides.GUIDED_TYPES)
    (work / "types.json").write_text(json.dumps(types), encoding="utf-8")
    toe = make_toe(str(work), "operator-guidance", boot_script("operator_guidance", start_frame=20))
    process = TDProcess(toe, str(work))
    report = process.wait_file("guidance.json", 90)
    rc = process.finish(10)
    if not report:
        raise RuntimeError("TouchDesigner returned no catalog metadata")
    mismatches, checked = [], 0
    for model in sorted(opguides.GUIDED_TYPES):
        for parameter in opguides.guidance_for(model)["parameters"]:
            name = parameter["name"]
            wanted = [parameter["label"], parameter["style"]]
            found = report["types"].get(model, {}).get("parameters", {}).get(name)
            checked += 1
            if found != wanted:
                mismatches.append({"type": model, "parameter": name, "expected": wanted, "actual": found})
    report.update(mismatches=mismatches, parameters_checked=checked, other_processes_untouched=process.others_untouched(),
                  exit=rc, killed=process.killed)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**{k: v for k, v in report.items() if k != "types"}, "types": len(report["types"])}))
    return 0 if (report["build"] == opguides.VERIFIED_BUILD and len(report["types"]) == len(types)
                 and not mismatches and not report["errors"] and rc == 0 and not process.killed
                 and report["other_processes_untouched"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
