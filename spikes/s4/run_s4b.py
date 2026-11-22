"""Run spike S4b (undo stack, interleaving, save/reload) in a disposable project, twice.
Usage: python -I spikes/s4/run_s4b.py <run_dir>   (run_dir must be git-ignored, e.g. .tmp/s4b)"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402

run_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", ".tmp", "s4b"))
if os.path.exists(run_dir):
    shutil.rmtree(run_dir)
os.makedirs(run_dir)
shutil.copyfile(os.path.join(HERE, "s4b_runtime.py"), os.path.join(run_dir, "s4b_runtime.py"))
toe = make_toe(run_dir, "s4b", boot_script("s4b_runtime"))
out = {}
td = TDProcess(toe, run_dir, env={"TDFLOW_S4B_PHASE": "1"}, log_name="td1.log")
out["phase1"] = td.wait_file("s4b_1.json", 120)
out["rc1"], out["killed1"] = td.finish(30), td.killed
saved = os.path.join(run_dir, "s4b_saved.toe")
if os.path.exists(saved):
    td2 = TDProcess(saved, run_dir, env={"TDFLOW_S4B_PHASE": "2"}, log_name="td2.log")
    out["phase2"] = td2.wait_file("s4b_2.json", 120)
    out["rc2"], out["killed2"] = td2.finish(30), td2.killed
    out["others_untouched"] = td2.others_untouched()
print(json.dumps(out, indent=1))
