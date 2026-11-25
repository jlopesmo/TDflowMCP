"""Run spike S4 (undo semantics) in a disposable TouchDesigner project. Usage: python -I spikes/s4/run_s4.py <run_dir>"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402

run_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", ".tmp", "s4"))
os.makedirs(run_dir, exist_ok=True)
for f in ("s4_results.json", "boot_error.txt"):
    if os.path.exists(os.path.join(run_dir, f)):
        os.remove(os.path.join(run_dir, f))
shutil.copyfile(os.path.join(HERE, "s4_runtime.py"), os.path.join(run_dir, "s4_runtime.py"))
toe = make_toe(run_dir, "s4", boot_script("s4_runtime"))
td = TDProcess(toe, run_dir)
res = td.wait_file("s4_results.json", 180)
rc = td.finish(30)
print(json.dumps({"rc": rc, "killed": td.killed, "others_untouched": td.others_untouched(),
                  "got_results": res is not None, "errors": (res or {}).get("errors", [])[:3]}, indent=1))
