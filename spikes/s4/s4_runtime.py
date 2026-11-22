"""S4 spike: what TouchDesigner's undo records for Python-driven edits (disposable project; not TDflowMCP source).

Runs inside TouchDesigner from the generated boot DAT, one step per frame. Each case performs an edit
(inside or outside ``ui.undo.startBlock``), then ``ui.undo.undo()`` and ``ui.undo.redo()`` on later
frames, and records the observed state after each step. Results: $TDFLOW_RUN_DIR/s4_results.json.
"""
import json
import os
import traceback

import td

D = os.environ.get("TDFLOW_RUN_DIR", ".")
R = {"build": None, "api": [], "cases": {}, "errors": []}
S = {"i": 0, "steps": None, "wait": 0}


def base():
    return td.op("/project1")


def sandbox():
    s = base().op("s4_sandbox")
    return s


def snapshot():
    s = sandbox()
    if s is None:
        return {"sandbox": False}
    out = {"sandbox": True, "children": sorted(c.name for c in s.children)}
    a, b = s.op("a"), s.op("b")
    if a is not None:
        out["a_conn"] = [c.owner.name for c in a.outputConnectors[0].connections] if a.outputConnectors else []
    if b is not None:
        out["b_par"] = b.par.value0.eval() if hasattr(b.par, "value0") else None
        out["b_mode"] = str(b.par.value0.mode) if hasattr(b.par, "value0") else None
        out["b_expr"] = b.par.value0.expr if hasattr(b.par, "value0") else None
        out["b_inputs"] = [c.name for c in b.inputs]
    c = s.op("renamed") or s.op("c")
    if c is not None:
        out["c_name"] = c.name
    out["custom_pars"] = [p.name for p in s.customPars]
    t = s.op("t")
    out["t_text"] = t.text if t is not None else None
    return out


def setup_sandbox():
    s = base().op("s4_sandbox")
    if s is not None:
        s.destroy()
    s = base().create(td.baseCOMP, "s4_sandbox")
    a = s.create(td.constantCHOP, "a")
    b = s.create(td.constantCHOP, "b")
    s.create(td.textDAT, "c")
    t = s.create(td.textDAT, "t")
    return s


# Each case: name -> (prepare, edit) functions. Edits run inside an undo block unless the name says "noblock".
def e_create():
    sandbox().create(td.textDAT, "created")


def e_destroy():
    sandbox().op("c").destroy()


def e_par():
    sandbox().op("b").par.value0 = 0.75


def e_expr():
    p = sandbox().op("b").par.value0
    p.expr = "absTime.seconds * 0"


def e_wire():
    sandbox().op("a").outputConnectors[0].connect(sandbox().op("b"))


def e_unwire():
    sandbox().op("b").inputConnectors[0].disconnect()


def e_dattext():
    sandbox().op("t").text = "edited"


def e_rename():
    sandbox().op("c").name = "renamed"


def e_custompar():
    pg = sandbox().appendCustomPage("S4")
    pg.appendFloat("S4float")


def snapshot_t():
    t = sandbox().op("t")
    return t.text if t else None


CASES = [
    ("create", e_create, True), ("destroy", e_destroy, True), ("par_value", e_par, True),
    ("par_expression", e_expr, True), ("wire", e_wire, True), ("dat_text", e_dattext, True),
    ("rename", e_rename, True), ("custom_par", e_custompar, True),
    ("par_value_noblock", e_par, False), ("create_noblock", e_create, False),
    ("multi_edit_block", lambda: (e_par(), e_dattext(), e_create()), True),
]


def build_steps():
    steps = []
    for name, edit, block in CASES:
        steps.append(("prepare", name, None, block))
        steps.append(("edit", name, edit, block))
        steps.append(("undo", name, None, block))
        steps.append(("redo", name, None, block))
        steps.append(("undo2", name, None, block))
    # unwire needs an existing wire: prepare connects outside undo, then clears the stack
    steps.append(("prepare_wired", "unwire", None, True))
    steps.append(("edit", "unwire", e_unwire, True))
    steps.append(("undo", "unwire", None, True))
    steps.append(("redo", "unwire", None, True))
    steps.append(("done", None, None, None))
    return steps


def rec(name, key, value):
    R["cases"].setdefault(name, {})[key] = value


def on_frame(me, frame):
    if S["steps"] is None:
        R["build"] = td.app.build
        R["api"] = sorted(a for a in dir(td.ui.undo) if not a.startswith("_"))
        R["globalState_initial"] = getattr(td.ui.undo, "globalState", None)
        S["steps"] = build_steps()
        return
    if S["wait"] > 0:
        S["wait"] -= 1
        return
    if S["i"] >= len(S["steps"]):
        return
    kind, name, edit, block = S["steps"][S["i"]]
    S["i"] += 1
    S["wait"] = 2                      # let TD settle between steps
    u = td.ui.undo
    try:
        if kind in ("prepare", "prepare_wired"):
            setup_sandbox()
            if kind == "prepare_wired":
                e_wire()
            u.clear()
            rec(name, "before", snapshot())
            rec(name, "block", block)
        elif kind == "edit":
            if block:
                u.startBlock("s4 " + name)
            try:
                edit()
            finally:
                if block:
                    u.endBlock()
            rec(name, "after_edit", snapshot())
            rec(name, "t_text_after_edit", snapshot_t())
        elif kind in ("undo", "undo2"):
            u.undo()
            rec(name, "after_" + kind, snapshot())
        elif kind == "redo":
            u.redo()
            rec(name, "after_redo", snapshot())
        elif kind == "done":
            with open(os.path.join(D, "s4_results.json"), "w", encoding="utf-8") as f:
                json.dump(R, f, indent=2, default=str)
            td.project.quit(force=True)
    except Exception:
        R["errors"].append({"step": kind, "case": name, "tb": traceback.format_exc()[-1500:]})
        rec(name or "?", "error_" + kind, traceback.format_exc()[-400:])
