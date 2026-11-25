"""Generate a disposable TouchDesigner .toe from text (stdlib only; used by the builder, tests and spikes).

The expanded-toe format was established in spike S5: plain-text node files, plus DAT text stored as
``b"2\\n*" + int32be(1,1,1,1,2,len) + UTF-8``, collapsed with Derivative's ``toecollapse``. The
top-level container files are minimal ones written here (no Derivative sample is needed).

Every generated project contains only ``/project1`` with the DATs passed in; it is never a user project.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
from typing import Dict, Optional

TD_BIN = os.environ.get("TDFLOW_TD_BIN", r"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin")
TD_EXE = os.path.join(TD_BIN, "TouchDesigner.exe")
COLLAPSE = os.path.join(TD_BIN, "toecollapse.exe")
EXPAND = os.path.join(TD_BIN, "toeexpand.exe")

_TOP = {
    ".start": "cookrate 60\nclock -f 1 -s 1 -o 0 -w 0\nrealtime on\nviewers off\n",
    ".grps": "-2\n0\n",
    ".root": "v 0 0 1\nend\n",
    ".parm": "?\n?\n",
    "project1.n": "COMP:container\ntile 0 0 160 130\nflags =  current on viewer 1\ncolor 0.55 0.55 0.55 \nend\n",
    "project1.parm": "?\n?\n",
}


def dat_text_blob(text: str) -> bytes:
    body = text.encode("utf-8")
    return b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


def make_toe(out_dir: str, name: str, boot_text: str, text_dats: Optional[Dict[str, str]] = None,
             build: str = "2025.32440") -> str:
    """Write ``<out_dir>/<name>.toe`` whose /project1/boot Execute DAT runs ``boot_text`` (onStart +
    onFrameStart enabled). ``text_dats`` adds plain Text DATs to /project1. Returns the .toe path."""
    work = os.path.join(out_dir, name + ".toe.dir")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(os.path.join(work, "project1"))
    files: Dict[str, object] = dict(_TOP)
    files[".build"] = f"version 099\nbuild {build}\ntime Sun Nov 22 12:00:00 2026\nosname Windows\nosversion 10\n"
    files["project1/boot.n"] = "DAT:execute\ntile 0 0 130 90\nflags =  viewer 1 parlanguage 0\ncolor 0.67 0.67 0.67 \nend\n"
    files["project1/boot.parm"] = "?\nstart 0 on\nframestart 0 on\nexit 0 on\nlanguage 0 python\n?\n"
    files["project1/boot.text"] = dat_text_blob(boot_text)
    for i, (dname, text) in enumerate((text_dats or {}).items()):
        files[f"project1/{dname}.n"] = (f"DAT:text\ntile {200 + 160 * i} 0 130 90\nflags =  viewer 1 parlanguage 0\n"
                                        "color 0.55 0.55 0.55 \nend\n")
        files[f"project1/{dname}.parm"] = "?\n?\n"
        files[f"project1/{dname}.text"] = dat_text_blob(text)
    for rel, content in files.items():
        p = os.path.join(work, rel)
        if isinstance(content, bytes):
            with open(p, "wb") as f:
                f.write(content)
        else:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
    with open(os.path.join(out_dir, name + ".toe.toc"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(files) + "\n")
    r = subprocess.run([COLLAPSE, name + ".toe"], cwd=out_dir, capture_output=True, text=True)
    toe = os.path.join(out_dir, name + ".toe")
    if r.returncode != 0 or not os.path.exists(toe):
        raise RuntimeError(f"toecollapse failed rc={r.returncode}: {r.stdout[-300:]} {r.stderr[-300:]}")
    return toe


def boot_script(module: str, entry: str = "on_frame", start_frame: int = 30) -> str:
    """Boot DAT text: add $TDFLOW_RUN_DIR (and $TDFLOW_SRC) to sys.path, then call
    ``<module>.<entry>(me, frame)`` every frame from ``start_frame``; errors go to run_dir/boot_error.txt."""
    return f"""# TDflowMCP generated boot (disposable project)
import os, sys, traceback
def _mod():
	d = os.environ.get('TDFLOW_RUN_DIR')
	if not d:
		return None
	for p in (d, os.environ.get('TDFLOW_SRC') or ''):
		if p and p not in sys.path:
			sys.path.insert(0, p)
	return __import__('{module}')
def onStart():
	return
def onFrameStart(frame):
	if frame < {start_frame} and not me.fetch('booted', False):
		return
	me.store('booted', True)
	try:
		m = _mod()
		if m is not None:
			m.{entry}(me, frame)
	except Exception:
		d = os.environ.get('TDFLOW_RUN_DIR')
		if d:
			open(os.path.join(d, 'boot_error.txt'), 'a').write(traceback.format_exc())
	return
def onExit():
	try:
		m = _mod()
		if m is not None and hasattr(m, 'on_exit'):
			m.on_exit(me)
	except Exception:
		pass
	return
"""
