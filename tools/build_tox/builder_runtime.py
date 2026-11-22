"""Builder that runs INSIDE a disposable TouchDesigner project (spike S2 / tools/build_tox).

Reads $TDFLOW_BUILD_PLAN, constructs /project1/TDflowMCP from it, waits a few frames for the
extension to initialise, verifies the embedded texts and parameters, saves the .tox, writes
$TDFLOW_RUN_DIR/build_result.json and quits. It never opens or saves any other project.
"""
import hashlib
import json
import os
import traceback

import td

S = {"step": 0, "n": 0, "res": {"ok": False, "checks": {}}}


def _sha(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _plan():
    with open(os.environ["TDFLOW_BUILD_PLAN"], encoding="utf-8") as f:
        return json.load(f)


def _dat(parent, name, text, x, y, kind=None):
    d = parent.create(kind or td.textDAT, name)
    d.text = text
    d.nodeX, d.nodeY = x, y
    return d


def _par(page, spec):
    t, name, label = spec["type"], spec["name"], spec.get("label", spec["name"])
    if t == "Str":
        p = page.appendStr(name, label=label)[0]
    elif t == "Int":
        p = page.appendInt(name, label=label)[0]
        if "min" in spec:
            p.min = p.normMin = spec["min"]
            p.clampMin = True
        if "max" in spec:
            p.max = p.normMax = spec["max"]
            p.clampMax = True
    elif t == "Pulse":
        p = page.appendPulse(name, label=label)[0]
    elif t == "Toggle":
        p = page.appendToggle(name, label=label)[0]
    elif t == "Menu":
        p = page.appendMenu(name, label=label)[0]
        p.menuNames = spec["menu"]
        p.menuLabels = [m.capitalize() for m in spec["menu"]]
    else:
        raise ValueError(t)
    if "default" in spec:
        p.default = spec["default"]
        p.val = spec["default"]              # S5: .default alone does not set the value
    if spec.get("readonly"):
        p.readOnly = True
    if spec.get("help"):
        p.help = spec["help"]
    return p


def build(plan):
    root = td.op("/project1")
    comp = root.create(td.baseCOMP, plan["name"])
    for tag in plan["tags"]:
        comp.tags.add(tag)
    comp.comment = f"TDflowMCP {plan['version']} (development build, Phase 1A)"
    # Independent local timeline (spike S7): the component keeps running its frame callbacks, and so
    # keeps serving, while the user pauses the project timeline.
    loc = comp.create(td.baseCOMP, "local")
    loc.nodeX, loc.nodeY = -200, 150
    tm = loc.create(td.timeCOMP, "time")
    tm.par.independent = True
    tm.par.play = True
    src = comp.create(td.baseCOMP, "src")
    src.nodeX, src.nodeY = 0, -300
    for i, (name, m) in enumerate(sorted(plan["modules"].items())):
        _dat(src, m["dat"], m["text"], (i % 4) * 160, -(i // 4) * 120)
    for i, (name, d) in enumerate(plan["dats"].items()):
        _dat(comp, name, d["text"], 200 + (i % 4) * 160, -(i // 4) * 120)
    _dat(comp, "manifest", json.dumps(plan["embedded_manifest"], indent=1, sort_keys=True), 0, 0)
    cb = plan["callbacks"]
    fe = _dat(comp, "frame_exec", cb["frame_exec"]["text"], 0, 150, td.executeDAT)
    for par, val in (("start", False), ("create", False), ("framestart", True), ("frameend", False),
                     ("exit", True), ("active", True)):
        if hasattr(fe.par, par):
            setattr(fe.par, par, val)
    pe = _dat(comp, "par_exec", cb["par_exec"]["text"], 160, 150, td.parameterexecuteDAT)
    pe.par.op = ".."
    pe.par.pars = "*"
    for par, val in (("custom", True), ("builtin", False), ("valuechange", True), ("onpulse", True),
                     ("expressionchange", False), ("exportchange", False), ("enablechange", False),
                     ("active", True)):
        if hasattr(pe.par, par):
            setattr(pe.par, par, val)
    page = comp.appendCustomPage("TDflowMCP")
    for spec in plan["parameters"]:
        _par(page, spec)
    ext = plan["extension"]
    comp.par.extension1 = f"op('./{ext['dat']}').module.{ext['class']}(me)"
    comp.par.promoteextension1 = bool(ext.get("promote"))
    S["parexec_pars"] = sorted(p.name for p in pe.pars() if not p.isCustom)[:60]
    return comp


def verify(plan, comp):
    c = {}
    man = json.loads(comp.op("manifest").text)
    bad = [n for n, m in man["modules"].items() if _sha(comp.op("src").op(m["dat"]).text) != m["sha256"]]
    bad += [n for n, d in man["dats"].items() if n != "tellagent" and _sha(comp.op(n).text) != d["sha256"]]
    bad += [n for n, d in man["callbacks"].items() if _sha(comp.op(n).text) != d["sha256"]]
    c["embedded_hash_mismatches"] = bad
    c["params"] = [p.name for p in comp.customPars]
    c["params_expected"] = [s["name"] for s in plan["parameters"]]
    ext = getattr(comp.ext, "TDflowMCPExt", None)
    c["extension_ok"] = ext is not None
    c["status_param"] = comp.par.Status.eval()
    c["port_param"] = comp.par.Port.eval()
    c["version_param"] = comp.par.Version.eval()
    c["listener"] = None if ext is None else ext.ctl.state.value
    c["frame_errors"] = None if ext is None else ext.frame_errors
    tm = comp.op("local/time")
    c["independent_time"] = bool(tm is not None and tm.par.independent.eval() and tm.par.play.eval())
    return c


def on_frame(me, frame):
    S["n"] += 1
    res = S["res"]
    try:
        if S["step"] == 0:
            res["td_build"] = td.app.build
            S["plan"] = _plan()
            S["comp"] = build(S["plan"])
            S["step"], S["at"] = 1, S["n"]
        elif S["step"] == 1 and S["n"] - S["at"] >= 20:
            comp = S["comp"]
            comp.par.reinitextensions.pulse()
            S["step"], S["at"] = 2, S["n"]
        elif S["step"] == 2 and S["n"] - S["at"] >= 20:
            comp, plan = S["comp"], S["plan"]
            res["checks"] = verify(plan, comp)
            res["checks"]["parexec_builtin_pars"] = S.get("parexec_pars")
            ok = (not res["checks"]["embedded_hash_mismatches"] and res["checks"]["extension_ok"]
                  and res["checks"]["params"] == res["checks"]["params_expected"]
                  and res["checks"]["status_param"] == "Off" and res["checks"]["listener"] == "Off"
                  and res["checks"]["independent_time"])
            if ok:
                comp.save(plan["out"])
                res["saved"] = os.path.exists(plan["out"])
            res["ok"] = bool(ok and res.get("saved"))
            S["step"] = 3
            _write(res)
            td.project.quit(force=True)
    except Exception:
        res["error"] = traceback.format_exc()[-3000:]
        S["step"] = 3
        _write(res)
        td.project.quit(force=True)


def _write(res):
    p = os.path.join(os.environ["TDFLOW_RUN_DIR"], "build_result.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=str)
    os.replace(p + ".tmp", p)
