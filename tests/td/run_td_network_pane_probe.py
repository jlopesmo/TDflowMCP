"""Inspect the Network Editor API in a disposable TouchDesigner project."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback


_BEFORE = None
_CAPTURE_NAMES = ("screenshot", "capture", "saveImage", "saveByteArray", "numpyArray", "pixelBuffer")


def observe(td):
    panes = []
    for pane in td.ui.panes:
        row = {"id": int(pane.id), "kind": str(pane.type), "open": bool(pane.open),
               "owner": pane.owner.path if pane.owner else None,
               "bounds_available": hasattr(pane, "bottomLeft") and hasattr(pane, "topRight"),
               "image_members": sorted(name for name in dir(pane)
                                       if any(term in name.lower() for term in ("capture", "screen", "pixel", "image"))),
               "capture_members": [name for name in _CAPTURE_NAMES if callable(getattr(pane, name, None))]}
        if hasattr(pane, "zoom"):
            row["view"] = [float(pane.x), float(pane.y), float(pane.zoom), bool(pane.maximize)]
            row["content_controls"] = [name for name in ("showParameters", "showBackdropTOPs", "showDataLinks")
                                       if hasattr(pane, name)]
        panes.append(row)
    return panes


def on_frame(me, frame):
    import td

    global _BEFORE
    try:
        if _BEFORE is None:
            if not any(pane.open and hasattr(pane, "zoom") for pane in td.ui.panes):
                return
            _BEFORE = frame, observe(td)
            return
        if frame < _BEFORE[0] + 8:
            return
        after = observe(td)
        if after != _BEFORE[1]:
            _BEFORE = frame, after
            return
        result = {"build": str(td.app.build), "before": _BEFORE[1], "panes": after,
                  "pane_state_unchanged": after == _BEFORE[1],
                  "desktop_pixels_read": False}
    except Exception:
        result = {"error": traceback.format_exc()}
    path = Path(os.environ["TDFLOW_RUN_DIR"]) / "observation.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    td.project.quit(force=True)


def main():
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=repo / ".tmp/v16-pane-observation.json")
    out = parser.parse_args().out.resolve()
    sys.path.insert(0, str(repo / "tools"))
    from tdtoe import boot_script, make_toe
    from tdrun import TDProcess

    (repo / ".tmp").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v16-pane-", dir=repo / ".tmp"))
    shutil.copyfile(__file__, work / "pane_probe.py")
    toe = make_toe(str(work), "network-pane", boot_script("pane_probe", start_frame=20))
    td = TDProcess(toe, str(work))
    report = td.wait_file("observation.json", 80)
    code = td.finish(8)
    print(json.dumps({"exit": code, "others_untouched": td.others_untouched(), "observation": report}))
    if not report or report.get("error") or report.get("build") != "2025.32440" or code != 0 or td.killed or not td.others_untouched():
        return 1
    if not report["pane_state_unchanged"] or not any("view" in pane for pane in report["panes"]):
        return 1
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
