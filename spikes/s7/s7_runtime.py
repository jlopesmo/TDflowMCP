"""S7 spike: do frame callbacks keep running while the project timeline is paused? (disposable; not source)

Inside a disposable project:
- /project1/plain: baseCOMP with an Execute DAT counting frames (follows the root timeline);
- /project1/local_time: baseCOMP with its own local/time Time COMP and an Execute DAT that both counts
  frames and drives the experiment (pause root, wait 3 s wall time, resume, write results, quit).
If local time does NOT decouple, the driver freezes too and the launcher's timeout ends the process
(no results file = "frames stop for components with their own time COMP as well").
"""
import json
import os
import time

import td

D = os.environ.get("TDFLOW_RUN_DIR", ".")
S = {"made": False, "step": 0, "t": 0.0, "res": {}}
COUNTER = ("def onFrameStart(frame):\n\tme.parent().store('n', me.parent().fetch('n', 0) + 1)\n"
           "{extra}\treturn\n")


def make(name, own_time, extra=""):
    c = td.op("/project1").create(td.baseCOMP, name)
    if own_time:
        loc = c.create(td.baseCOMP, "local")
        tm = loc.create(td.timeCOMP, "time")
        S["res"]["timecomp_pars"] = sorted(p.name for p in tm.pars())[:80]
        tm.par.independent = True
        tm.par.play = True
        tm.par.rate = 60
    e = c.create(td.executeDAT, "count")
    e.text = COUNTER.format(extra=extra)
    e.par.framestart = True
    e.par.active = True
    return c


def counts():
    return {n: td.op("/project1/" + n).fetch("n", 0) for n in ("plain", "local_time")}


def on_frame(me, frame):                          # boot DAT (root timeline): set-up only
    if S["made"]:
        return
    S["made"] = True
    make("plain", False)
    make("local_time", True, extra="\t__import__('s7_runtime').driver()\n")
    S["res"]["root_time"] = td.op("/").time.path
    S["res"]["plain_time"] = td.op("/project1/plain").time.path
    S["res"]["local_time"] = td.op("/project1/local_time").time.path
    S["t"] = time.monotonic()


def driver():                                     # runs from /project1/local_time/count every frame
    st, res = S["step"], S["res"]
    S["calls"] = S.get("calls", 0) + 1
    if S["calls"] % 30 == 1:
        with open(os.path.join(D, "s7_progress.json"), "w") as f:
            json.dump({"calls": S["calls"], "step": st, "counts": counts(), "res": res}, f)
    if st == 0 and time.monotonic() - S["t"] > 2:
        res["before_pause"] = counts()
        td.op("/").time.play = False
        S["step"], S["t"] = 1, time.monotonic()
    elif st == 1 and time.monotonic() - S["t"] > 3:
        res["during_pause"] = counts()
        td.op("/").time.play = True
        S["step"], S["t"] = 2, time.monotonic()
    elif st == 2 and time.monotonic() - S["t"] > 1:
        res["after_resume"] = counts()
        S["step"] = 3
        with open(os.path.join(D, "s7_results.json"), "w") as f:
            json.dump(res, f, indent=2)
        td.project.quit(force=True)
