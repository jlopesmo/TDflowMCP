"""Build the disposable s5.toe from text with toecollapse (S5 spike; not TDflowMCP source).

Top-level container files are copied from an expanded copy of Derivative's PythonExamples sample
(.tmp/s5/learn), everything under /project1 is replaced by a single Execute DAT that runs s5_runtime.
"""
import os
import shutil
import subprocess

S5 = os.path.dirname(os.path.abspath(__file__))
LEARN = os.path.join(S5, "learn", "sample.toe.dir")
BUILD = os.path.join(S5, "build")
DIR = os.path.join(BUILD, "s5.toe.dir")
COLLAPSE = r"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\toecollapse.exe"

BOOT = """# TDflowMCP S5 disposable boot (spike only)
import os, sys, traceback
def _boot(origin):
	d = os.environ.get('TDFLOW_S5_DIR')
	if not d:
		return
	try:
		open(os.path.join(d, 'results', 'boot_called_' + origin + '.txt'), 'w').write('env_set=True')
	except Exception:
		pass
	st = me.fetch('s5_booted', False, storeDefault=False) if hasattr(me, 'fetch') else False
	if st:
		return
	me.store('s5_booted', True)
	try:
		os.makedirs(os.path.join(d, 'results'), exist_ok=True)
		open(os.path.join(d, 'results', 'boot_started.txt'), 'w').write(origin)
		if d not in sys.path:
			sys.path.insert(0, d)
		import s5_runtime
		s5_runtime.phase_a(me)
	except Exception:
		open(os.path.join(d, 'results', 'boot_error.txt'), 'w').write(traceback.format_exc())
def onStart():
	_boot('onStart')
	return
def onFrameStart(frame):
	if frame > 30:
		_boot('onFrameStart')
	return
"""

FILES = {
    ".build": "version 099\nbuild 2025.32440\ntime Sun Nov 22 12:00:00 2026\nosname Windows\nosversion 10\n",
    ".parm": "?\n?\n",
    "project1/s5_boot.n": "DAT:execute\ntile 0 0 130 90\nflags =  viewer 1 parlanguage 0\ncolor 0.67 0.67 0.67 \nend\n",
    "project1/s5_boot.parm": "?\nstart 0 on\nframestart 0 on\nlanguage 0 python\n?\n",
    "project1/s5_boot.text": BOOT,
}
COPY = [".start", ".grps", ".root", "project1.n", "project1.panel", "perform.n", "perform.parm",
        "local.n", "local.parm", "local.panel"]

if os.path.exists(BUILD):
    shutil.rmtree(BUILD)
os.makedirs(os.path.join(DIR, "project1"))
toc = []
for name in COPY:
    shutil.copyfile(os.path.join(LEARN, name), os.path.join(DIR, name))
    toc.append(name)
import struct


def dat_text_blob(text):
    """Expanded-toe DAT text storage: b'2\\n*' + int32be(1,1,1,1,2,len) + UTF-8 bytes.
    Format inferred from the expanded Derivative sample and the bridge .tox (S5 finding)."""
    body = text.encode("utf-8")
    return b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


for name, text in FILES.items():
    path = os.path.join(DIR, name)
    if name.endswith(".text"):
        with open(path, "wb") as f:
            f.write(dat_text_blob(text))
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    toc.append(name)
with open(os.path.join(DIR, "project1.parm"), "w", encoding="utf-8", newline="\n") as f:
    f.write("?\npageindex1 0 1\nw 0 800\nh 0 600\n?\n")
toc.append("project1.parm")
order = [".build", ".start", ".grps", ".root", ".parm", "project1.n", "project1.parm", "project1.panel",
         "perform.n", "perform.parm", "local.n", "local.parm", "local.panel",
         "project1/s5_boot.n", "project1/s5_boot.parm", "project1/s5_boot.text"]
with open(os.path.join(BUILD, "s5.toe.toc"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(order) + "\n")
r = subprocess.run([COLLAPSE, "s5.toe"], cwd=BUILD, capture_output=True, text=True)
print("rc", r.returncode, r.stdout[-500:], r.stderr[-500:])
print("exists", os.path.exists(os.path.join(BUILD, "s5.toe")),
      os.path.getsize(os.path.join(BUILD, "s5.toe")) if os.path.exists(os.path.join(BUILD, "s5.toe")) else None)
