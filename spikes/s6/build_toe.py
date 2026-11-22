"""Build the disposable s6.toe from text with toecollapse (S6 spike; not TDflowMCP source).

Same method as S5: top-level container files are copied from an expanded copy of a Derivative sample
(<run dir>/learn/sample.toe.dir); /project1 contains only:
  s6_boot           Execute DAT (onStart / onFrameStart) that runs s6_runtime each frame
  setup_index_html  Text DAT  \
  setup_app_css     Text DAT   > the Setup Assistant assets, served by the embedded transport
  setup_app_js      Text DAT  /
"""
import os
import shutil
import struct
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
LEARN = os.path.join(HERE, "learn", "sample.toe.dir")
BUILD = os.path.join(HERE, "build")
DIR = os.path.join(BUILD, "s6.toe.dir")
COLLAPSE = r"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\toecollapse.exe"

BOOT = """# TDflowMCP S6 disposable boot (spike only)
import os, sys, traceback
def _rt():
	d = os.environ.get('TDFLOW_S6_DIR')
	if not d:
		return None
	if d not in sys.path:
		sys.path.insert(0, d)
	s = os.environ.get('TDFLOW_SRC')
	if s and s not in sys.path:
		sys.path.insert(0, s)
	import s6_runtime
	return s6_runtime
def _mark(name):
	d = os.environ.get('TDFLOW_S6_DIR')
	if d:
		try:
			open(os.path.join(d, 'results', 'boot_' + name + '.txt'), 'w').write('1')
		except Exception:
			pass
def onStart():
	_mark('onstart')
	return
def onFrameStart(frame):
	if frame < 30:
		return
	if frame == 30:
		_mark('frame30')
	try:
		rt = _rt()
		if rt is not None:
			rt.on_frame(me, frame)
	except Exception:
		d = os.environ.get('TDFLOW_S6_DIR')
		if d:
			open(os.path.join(d, 'results', 'boot_error.txt'), 'a').write(traceback.format_exc())
	return
"""


def dat_text_blob(text):
    """Expanded-toe DAT text storage: b'2\\n*' + int32be(1,1,1,1,2,len) + UTF-8 bytes (S5 finding)."""
    body = text.encode("utf-8")
    return b"2\n*" + struct.pack(">6I", 1, 1, 1, 1, 2, len(body)) + body


def asset(name):
    with open(os.path.join(HERE, "assets", name), encoding="utf-8", newline="") as f:
        return f.read()


TEXT_DATS = {"setup_index_html": asset("index.html"), "setup_app_css": asset("app.css"),
             "setup_app_js": asset("app.js")}
FILES = {
    ".build": "version 099\nbuild 2025.32440\ntime Sun Nov 22 12:00:00 2026\nosname Windows\nosversion 10\n",
    ".parm": "?\n?\n",
    "project1/s6_boot.n": "DAT:execute\ntile 0 0 130 90\nflags =  viewer 1 parlanguage 0\ncolor 0.67 0.67 0.67 \nend\n",
    "project1/s6_boot.parm": "?\nstart 0 on\nframestart 0 on\nlanguage 0 python\n?\n",
    "project1/s6_boot.text": BOOT,
}
for i, (name, text) in enumerate(TEXT_DATS.items()):
    FILES[f"project1/{name}.n"] = (f"DAT:text\ntile {200 + 160 * i} 0 130 90\nflags =  viewer 1 parlanguage 0\n"
                                   "color 0.55 0.55 0.55 \nend\n")
    FILES[f"project1/{name}.parm"] = "?\n?\n"
    FILES[f"project1/{name}.text"] = text
COPY = [".start", ".grps", ".root", "project1.n", "project1.panel", "perform.n", "perform.parm",
        "local.n", "local.parm", "local.panel"]

if __name__ == "__main__":
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(os.path.join(DIR, "project1"))
    for name in COPY:
        shutil.copyfile(os.path.join(LEARN, name), os.path.join(DIR, name))
    for name, text in FILES.items():
        path = os.path.join(DIR, name)
        if name.endswith(".text"):
            with open(path, "wb") as f:
                f.write(dat_text_blob(text))
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    with open(os.path.join(DIR, "project1.parm"), "w", encoding="utf-8", newline="\n") as f:
        f.write("?\npageindex1 0 1\nw 0 800\nh 0 600\n?\n")
    order = [".build", ".start", ".grps", ".root", ".parm", "project1.n", "project1.parm", "project1.panel",
             "perform.n", "perform.parm", "local.n", "local.parm", "local.panel"] + [
        f"project1/{n}{ext}" for n in ["s6_boot"] + list(TEXT_DATS) for ext in (".n", ".parm", ".text")]
    with open(os.path.join(BUILD, "s6.toe.toc"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(order) + "\n")
    r = subprocess.run([COLLAPSE, "s6.toe"], cwd=BUILD, capture_output=True, text=True)
    print("rc", r.returncode, r.stdout[-500:], r.stderr[-500:])
    toe = os.path.join(BUILD, "s6.toe")
    print("exists", os.path.exists(toe), os.path.getsize(toe) if os.path.exists(toe) else None)
