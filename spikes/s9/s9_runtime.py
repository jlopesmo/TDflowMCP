"""S9 spike: does TouchDesigner keep running frames while its window is minimized? (disposable; not source)

Inside a disposable project, three independent clocks write their counters to s9_frames.json about ten
times a second:
- ``root``: frames seen by the boot Execute DAT (root timeline);
- ``local``: frames seen by an Execute DAT in a COMP with its own independent Time COMP (what TDflowMCP uses
  since spike S7, so that a paused timeline does not stop it);
- ``thread``: ticks of a plain Python thread that never touches TouchDesigner.
The launcher minimizes and restores this process's own window and compares the counters.
"""
import json
import os
import threading
import time

import td

D = os.environ.get("TDFLOW_RUN_DIR", ".")
S = {"made": False, "root": 0, "thread": 0, "last": 0.0, "stop": False}


def _thread():
    while not S["stop"]:
        S["thread"] += 1
        time.sleep(0.05)
        _write(False)


def _write(from_main):
    now = time.monotonic()
    if now - S["last"] < 0.1:
        return
    S["last"] = now
    snap = {"t": time.time(), "root": S["root"], "local": S.get("local", 0), "thread": S["thread"],
            "prefs": S.get("prefs"), "perform": S.get("perform")}
    tmp = os.path.join(D, "s9_frames.json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, os.path.join(D, "s9_frames.json"))
    except OSError:
        pass


def local_tick():                                 # called from the COMP with independent local time
    S["local"] = S.get("local", 0) + 1


def on_frame(me, frame):
    S["root"] += 1
    if not S["made"]:
        S["made"] = True
        c = td.op("/project1").create(td.baseCOMP, "local_time")
        tm = c.create(td.baseCOMP, "local").create(td.timeCOMP, "time")
        tm.par.independent = True
        tm.par.play = True
        tm.par.rate = 60
        e = c.create(td.executeDAT, "count")
        e.text = "def onFrameStart(frame):\n\t__import__('s9_runtime').local_tick()\n\treturn\n"
        e.par.framestart = True
        e.par.active = True
        try:                                      # read-only: names of preferences that might be relevant
            keys = [str(k) for k in td.ui.preferences.keys()]
            S["prefs"] = sorted(k for k in keys if any(w in k.lower() for w in ("minim", "cook", "background", "power",
                                                                               "focus", "idle", "sleep")))
        except Exception as e2:
            S["prefs"] = "unavailable: " + type(e2).__name__
        try:
            S["perform"] = bool(td.ui.performMode)
        except Exception:
            S["perform"] = None
        threading.Thread(target=_thread, name="s9-heartbeat", daemon=True).start()
    if os.path.exists(os.path.join(D, "s9_quit")):
        S["stop"] = True
        td.project.quit(force=True)
