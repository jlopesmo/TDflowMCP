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


class TDProcess:
    def __init__(self, toe: str, run_dir: str, env: Optional[Dict[str, str]] = None, log_name: str = "td_console.log"):
        self.toe, self.run_dir = toe, run_dir
        self.preexisting = td_pids()
        e = dict(os.environ, TDFLOW_RUN_DIR=run_dir, TOUCH_TEXT_CONSOLE="1")
        e.update(env or {})
        self._log = open(os.path.join(run_dir, log_name), "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen([TD_EXE, toe], cwd=os.path.dirname(toe), env=e, stdout=self._log,
                                     stderr=subprocess.STDOUT)
        self.pid = self.proc.pid
        self.killed = False

    def alive(self) -> bool:
        return self.proc.poll() is None

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
