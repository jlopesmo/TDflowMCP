"""Run spike S9 (frames while the window is minimized). Usage: python -I spikes/s9/run_s9.py [run_dir]

Launches TouchDesigner with a disposable project, then minimizes and restores **that process's own window**
(found by process id; never another window) and compares frame counters. Windows only (user32 via ctypes).
The window is restored without being activated, so focus is not taken from whatever the user is doing.
"""
import ctypes
import json
import os
import shutil
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402

SW_SHOWNOACTIVATE, SW_MINIMIZE, SW_SHOWMINNOACTIVE = 4, 6, 7
user32 = ctypes.windll.user32


def windows(pid):
    """(hwnd, class, visible) of the top-level windows of one process."""
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            buf = ctypes.create_unicode_buffer(200)
            user32.GetClassNameW(hwnd, buf, 200)
            found.append((hwnd, buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True
    user32.EnumWindows(proto(cb), 0)
    return found


def main_window(pid, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        for hwnd, cls, vis in windows(pid):
            if cls == "TouchDesigner Window" and vis:
                return hwnd
        time.sleep(0.25)
    return None


def read(run_dir):
    for _ in range(20):
        try:
            with open(os.path.join(run_dir, "s9_frames.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(0.02)
    return None


def sample(run_dir, seconds):
    a = read(run_dir)
    time.sleep(seconds)
    b = read(run_dir)
    return {k: b[k] - a[k] for k in ("root", "local", "thread")} if a and b else None


run_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", ".tmp", "s9"))
os.makedirs(run_dir, exist_ok=True)
for f in ("s9_frames.json", "s9_quit", "boot_error.txt"):
    if os.path.exists(os.path.join(run_dir, f)):
        os.remove(os.path.join(run_dir, f))
shutil.copyfile(os.path.join(HERE, "s9_runtime.py"), os.path.join(run_dir, "s9_runtime.py"))
toe = make_toe(run_dir, "s9", boot_script("s9_runtime"))
td = TDProcess(toe, run_dir)
td.keep_running = False        # the harness would otherwise restore the window this spike minimizes on purpose
out = {"seconds_per_phase": 4}
try:
    first = td.wait_file("s9_frames.json", 90)
    time.sleep(2)
    hwnd = main_window(td.pid)
    out["window_found"] = bool(hwnd)
    if not hwnd:
        out["windows_of_process"] = [(c, v) for _, c, v in windows(td.pid)]
    out["perform_mode"] = (first or {}).get("perform")
    if hwnd:
        out["visible"] = sample(run_dir, 4)
        # Behind every other window (bottom of the z-order), not activated: does it still run frames?
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        time.sleep(1.0)
        out["foreground_is_ours_at_bottom"] = bool(user32.GetForegroundWindow() == hwnd)
        out["at_bottom_of_z_order"] = sample(run_dir, 4)
        user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)        # minimize our own window without changing focus
        time.sleep(1.0)
        out["iconic_after_minimize"] = bool(user32.IsIconic(hwnd))
        out["minimized"] = sample(run_dir, 4)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)         # restore without activating
        time.sleep(1.0)
        out["iconic_after_restore"] = bool(user32.IsIconic(hwnd))
        out["restored"] = sample(run_dir, 4)
finally:
    with open(os.path.join(run_dir, "s9_quit"), "w") as f:
        f.write("1")
    rc = td.finish(30)
out.update(rc=rc, killed=td.killed, others_untouched=td.others_untouched())
with open(os.path.join(run_dir, "s9_results.json"), "w") as f:
    json.dump(out, f, indent=1)
print(json.dumps(out, indent=1))
