"""S4b spike: undo stack introspection, human/agent interleaving, op identity after undo, save/reload.
Disposable project only (not TDflowMCP source). Phase 1 = in the first launch; phase 2 = after the
harness relaunches the saved disposable file. Results: $TDFLOW_RUN_DIR/s4b_<phase>.json.
"""
import json
import os
import traceback

import td

D = os.environ.get("TDFLOW_RUN_DIR", ".")
PHASE = os.environ.get("TDFLOW_S4B_PHASE", "1")
R = {"phase": PHASE, "steps": [], "errors": []}
S = {"i": 0, "wait": 0}


def stack():
    u = td.ui.undo
    def norm(x):
        try:
            return [str(e) for e in list(x)][-6:]
        except Exception as e:
            return f"<{type(e).__name__}: {x!r}>"
    return {"undo": norm(u.undoStack), "redo": norm(u.redoStack), "state": u.state}


def rec(name, **kw):
    R["steps"].append(dict(name=name, stack=stack(), **kw))


def sb():
    return td.op("/project1/s4b")


def step1():
    s = td.op("/project1").create(td.baseCOMP, "s4b")
    s.create(td.constantCHOP, "c")
    td.ui.undo.clear()
    rec("cleared")


def step2():                                    # agent block
    td.ui.undo.startBlock("tdflow:101")
    sb().create(td.noiseTOP, "agent_op")
    td.ui.undo.endBlock()
    R["agent_op_id"] = sb().op("agent_op").id
    rec("agent_block_done")


def step3():                                    # "human" edit afterwards (a separately named block)
    td.ui.undo.startBlock("user edit")
    sb().op("c").par.value0 = 0.5
    td.ui.undo.endBlock()
    rec("human_block_done")


def step4():                                    # what does a plain undo now revert?
    td.ui.undo.undo()
    rec("after_one_undo", c_value=sb().op("c").par.value0.eval(), agent_op=bool(sb().op("agent_op")))


def step5():
    td.ui.undo.undo()
    rec("after_second_undo", c_value=sb().op("c").par.value0.eval(), agent_op=bool(sb().op("agent_op")))


def step6():                                    # delete + undo: does the restored op keep its id?
    td.ui.undo.startBlock("tdflow:102")
    sb().op("c").destroy()
    td.ui.undo.endBlock()
    cid = R.setdefault("c_id_before", None)
    rec("deleted_c")


def step6a():
    R["c_id_before"] = R.get("c_id_before")


def step7():
    td.ui.undo.undo()
    c = sb().op("c")
    rec("undo_delete", restored=bool(c), id_after=c.id if c else None)


def step8():                                    # block left on stack, then save the disposable file
    td.ui.undo.startBlock("tdflow:103")
    sb().create(td.nullTOP, "before_save")
    td.ui.undo.endBlock()
    rec("before_save")
    path = os.path.join(D, "s4b_saved.toe")
    td.project.save(path)
    rec("after_save", saved=os.path.exists(path))


def step9():
    td.ui.undo.undo()
    rec("undo_after_save", before_save_exists=bool(sb().op("before_save")))


def phase2():
    rec("reloaded", s4b_exists=bool(sb()), before_save_exists=bool(sb() and sb().op("before_save")))
    td.ui.undo.undo()                            # nothing to undo after a reload?
    rec("undo_after_reload", before_save_exists=bool(sb() and sb().op("before_save")))


STEPS = [step1, step2, step3, step4, step5, step6, step7, step8, step9]


def on_frame(me, frame):
    if S.get("done"):
        return
    if S["wait"] > 0:
        S["wait"] -= 1
        return
    S["wait"] = 3
    try:
        if PHASE == "2":
            if S["i"] == 0:
                S["i"] = 1
                return
            phase2()
        elif S["i"] < len(STEPS):
            if STEPS[S["i"]] is step6:
                R["c_id_before"] = sb().op("c").id
            STEPS[S["i"]]()
            S["i"] += 1
            return
    except Exception:
        R["errors"].append(traceback.format_exc()[-1500:])
    S["done"] = True
    with open(os.path.join(D, f"s4b_{PHASE}.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=2, default=str)
    td.project.quit(force=True)
