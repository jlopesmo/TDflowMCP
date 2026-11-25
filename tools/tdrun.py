"""Launch TouchDesigner with a disposable .toe and supervise it (stdlib only; runs OUTSIDE TouchDesigner).

Safety rules (Phase 1 experiment rule):
- only the generated disposable file is opened;
- other TouchDesigner processes that were already running are recorded and never touched;
- on timeout only the process tree this module started is terminated (``taskkill /T`` on our PID);
- the caller decides the run directory (git-ignored ``.tmp/`` or ``build/``).
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

from tdtoe import TD_EXE


def td_pids() -> List[int]:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh", "/fi", "IMAGENAME eq TouchDesigner.exe"],
                         capture_output=True, text=True).stdout
    pids = []
    for row in csv.reader(io.StringIO(out)):
        if len(row) > 1 and row[0].lower() == "touchdesigner.exe":
            pids.append(int(row[1]))
    return sorted(pids)


SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 4, 7
MAIN_WINDOW_CLASS = "TouchDesigner Window"
PROCS: Dict[int, "TDProcess"] = {}               # pid -> the TDProcess this module started


def own_windows(pid: int) -> List[int]:
    """Visible main windows of ONE process (ours), by process id. Never another process's windows."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[int] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, buf, 128)
            if buf.value == MAIN_WINDOW_CLASS:
                found.append(hwnd)
        return True
    user32.EnumWindows(proto(cb), 0)
    return found


class TDProcess:
    def __init__(self, toe: str, run_dir: str, env: Optional[Dict[str, str]] = None, log_name: str = "td_console.log"):
        self.toe, self.run_dir = toe, run_dir
        # Spike S9: TouchDesigner runs no frames while its window is minimized, so a test would stall (this was
        # the "unexplained hang"). While ``keep_running`` is set, a minimized test window is restored without
        # being activated (focus stays where the user is working) and the restore is counted.
        self.keep_running, self.restores, self._tended = True, 0, 0.0
        self._win_lock = threading.Lock()         # the tender and a deliberate minimize never interleave
        self.preexisting = td_pids()
        e = dict(os.environ, TDFLOW_RUN_DIR=run_dir, TOUCH_TEXT_CONSOLE="1")
        e.update(env or {})
        self._log = open(os.path.join(run_dir, log_name), "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen([TD_EXE, toe], cwd=os.path.dirname(toe), env=e, stdout=self._log,
                                     stderr=subprocess.STDOUT)
        self.pid = self.proc.pid
        self.killed = False
        PROCS[self.pid] = self                    # checkpoint handlers get only the pid
        # Checkpoint handlers can run for minutes without calling ``alive()``, so a daemon thread tends the
        # window for as long as our process runs.
        threading.Thread(target=self._tender, name="tdrun-window", daemon=True).start()

    def _tender(self) -> None:
        while self.proc.poll() is None:
            self.tend_window()
            time.sleep(0.5)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def tend_window(self) -> None:
        """Called from the polling loops (at most twice a second): un-minimize our own test window."""
        if not self.keep_running or sys.platform != "win32" or time.monotonic() - self._tended < 0.5:
            return
        self._tended = time.monotonic()
        try:
            import ctypes
            user32 = ctypes.windll.user32
            with self._win_lock:
                if not self.keep_running:
                    return
                for hwnd in own_windows(self.pid):
                    if user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                        self.restores += 1
        except Exception:
            pass

    def set_minimized(self, minimized: bool) -> bool:
        """Tests of the minimized behaviour: minimize or restore OUR window without changing focus."""
        if sys.platform != "win32":
            return False
        import ctypes
        user32 = ctypes.windll.user32
        with self._win_lock:
            if minimized:
                self.keep_running = False             # under the lock: the tender cannot undo it
            wins = own_windows(self.pid)
            for hwnd in wins:
                user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE if minimized else SW_SHOWNOACTIVATE)
            return bool(wins) and all(bool(user32.IsIconic(h)) == minimized for h in wins)

    def wait_file(self, name: str, seconds: float) -> Optional[dict]:
        p = os.path.join(self.run_dir, name)
        end = time.time() + seconds
        while time.time() < end:
            if os.path.exists(p):
                for _ in range(20):
                    try:
                        with open(p, encoding="utf-8") as f:
                            return json.load(f)
                    except (ValueError, OSError):
                        time.sleep(0.05)
            if not self.alive():
                return None
            time.sleep(0.05)
        return None

    def finish(self, seconds: float = 60) -> int:
        end = time.time() + seconds
        while time.time() < end and self.alive():
            time.sleep(0.2)
        if self.alive():
            self.killed = True
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True)
            self.proc.wait(timeout=30)
        self._log.close()
        return self.proc.returncode

    def others_untouched(self) -> bool:
        """Every TouchDesigner process that was running before we started is still running."""
        now = set(td_pids())
        return set(self.preexisting) <= now
