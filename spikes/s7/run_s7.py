"""Run spike S7 (paused timeline vs. frame callbacks). Usage: python -I spikes/s7/run_s7.py <run_dir>"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402

run_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", ".tmp", "s7"))
os.makedirs(run_dir, exist_ok=True)
for f in ("s7_results.json", "boot_error.txt"):
    if os.path.exists(os.path.join(run_dir, f)):
        os.remove(os.path.join(run_dir, f))
shutil.copyfile(os.path.join(HERE, "s7_runtime.py"), os.path.join(run_dir, "s7_runtime.py"))
toe = make_toe(run_dir, "s7", boot_script("s7_runtime"))
td = TDProcess(toe, run_dir)
res = td.wait_file("s7_results.json", 60)
rc = td.finish(5)
print(json.dumps({"rc": rc, "killed": td.killed, "others_untouched": td.others_untouched(), "results": res}, indent=1))
