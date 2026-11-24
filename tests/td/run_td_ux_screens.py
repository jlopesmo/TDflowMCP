"""Visual usability review of the component's parameter pages (owner UX assignment, 2026-11-23).

    python -I tests/td/run_td_ux_screens.py --capture <capture_td.ps1>

Opens a disposable project, shows each custom parameter page in TouchDesigner's parameter window at three stages
(fresh, agent created, MCP enabled) and captures **only the windows of that TouchDesigner process** (never the
desktop) into .tmp/td-ux-screens/ (git-ignored). The images are for human/agent review, not committed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_gate_tests as g  # noqa: E402

base, T, H = g.base, g.T, g.H
ROOT = base.ROOT
STATE = {}


def cp_screen(data, pid):
    out = os.path.join(STATE["top"], f"{data['stage']}-{data['page']}")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        STATE["capture"], "-ProcId", str(pid), "-Out", out], capture_output=True, text=True, timeout=60)
    STATE.setdefault("files", []).append(r.stdout.strip())
    return {}


class AnyScreen(dict):
    """Every checkpoint ('screen_<stage>_<page>') is a capture."""

    def get(self, key, default=None):
        return cp_screen if str(key).startswith("screen_") else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--capture", required=True)
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-ux-screens"), ROOT)
    if os.path.exists(top):
        shutil.rmtree(top)
    os.makedirs(top)
    STATE.update(top=top, capture=os.path.abspath(a.capture))
    proj = tempfile.mkdtemp(prefix="tdflow-ux-project-")
    run_dir = os.path.join(top, "run")
    os.makedirs(run_dir)
    res = g.launch("native_screens", os.path.abspath(a.tox), run_dir, proj,
                   {"TDFLOW_PRIVATE_ROOT": os.path.join(top, "private")}, AnyScreen(), deadline_s=400)
    shutil.rmtree(proj, ignore_errors=True)
    print("\n".join(STATE.get("files", [])))
    print({"errors": (res.get("errors") or [])[:2]})


if __name__ == "__main__":
    main()
