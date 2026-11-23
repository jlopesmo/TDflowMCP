"""Phase 1B part of the in-TD runner (imported by td_runner.py; runs INSIDE a disposable project).

Drives only the component's parameters, plus documented test seams (``launcher``, closing the store
inside the audit worker, a deliberate main-thread block, a human edit inside a ``ui.undo`` block). Network
work is done by the external harness (run_td_mcp_tests.py) at checkpoints. The main thread never waits for
the audit worker: store reads are submitted and polled (``store_op``).
"""
import json
import os
import shutil
import sqlite3
import tempfile
import time

import td


def store_op(ext, until, op, *args, **kw):
    """Generator: run one store operation on the audit worker and poll for it (never blocks TD)."""
    f = ext.worker.submit(op, *args, **kw)
    yield until(f.done, 15, "store_" + op)
    return f.result(0)


def fixtures():
    root = td.op("/project1")
    fx = root.create(td.baseCOMP, "fx")
    lvl = fx.create(td.levelTOP, "level1")
    fx.create(td.noiseTOP, "noise1")
    notes = fx.create(td.textDAT, "notes")
    notes.text = "hello"
    notes.tags.add("tdflowdata")
    tab = fx.create(td.tableDAT, "table1")             # untagged: may be evaluated downstream -> execute
    tab.text = "a\tb\n1\t2"
    t2 = fx.create(td.tableDAT, "table2")              # tagged by the user as data
    t2.tags.add("tdflowdata")
    fx.create(td.nullDAT, "dat_sink")
    fx.create(td.selectTOP, "sel1")                    # has a TOP-reference parameter
    ex = fx.create(td.executeDAT, "exec1")
    ex.par.active = False                      # never runs; only a classification target
    root.create(td.nullTOP, "out1")
    mem = root.create(td.baseCOMP, "mem")
    mem.tags.add("tdflowProject")
    mem.create(td.textDAT, "record1").text = "memory"
    return {"level_opacity": lvl.par.opacity.eval()}


def run(comp, ext, T, frames, until, checkpoint, R, restart=False):
    calls = []
    ext.launcher = lambda url: (calls.append(url), True)[1]
    if restart:
        yield from _restart(comp, ext, T, frames, until, checkpoint, R)
        return
    fx = fixtures()
    yield frames(5)
    # Enable MCP before any agent exists: refused with a reason, toggle reverts.
    comp.par.Enablemcp = True
    yield until(lambda: comp.par.Status.eval() == "Setup" and not comp.par.Enablemcp.eval(), 15, "enable_refused")
    yield frames(10)
    T("enable.refused_without_agent", not comp.par.Enablemcp.eval() and "agent.token" in comp.par.Statusdetail.eval(),
      detail=comp.par.Statusdetail.eval(), status=comp.par.Status.eval())
    T("enable.no_browser_for_enable", calls == [])
    g = yield checkpoint("setup_ready", {"pid": os.getpid()})
    code = (g or {}).get("match_code")
    yield until(lambda: code and code in comp.par.Pairingrequest.eval(), 10, "pairing_param")
    T("pairing.param_shows_code", bool(code) and code in comp.par.Pairingrequest.eval(),
      param=comp.par.Pairingrequest.eval())
    comp.par.Approvepairing.pulse()
    yield until(lambda: comp.par.Pairingrequest.eval() == "", 10, "pairing_approved")   # after a durable record
    T("pairing.approved_in_td", comp.par.Pairingrequest.eval() == "")
    yield checkpoint("pair_approved", {})
    # tokens are created by the harness through the paired admin API
    yield checkpoint("tokens_created_wait", {})
    T("agents.summary", comp.par.Agentsummary.eval().startswith(("5 agent", "6 agent")), summary=comp.par.Agentsummary.eval())
    comp.par.Enablemcp = True
    yield until(lambda: comp.par.Status.eval() == "Enabled", 15, "enable")
    T("enable.enabled", comp.par.Status.eval() == "Enabled" and comp.par.Enablemcp.eval(),
      status=comp.par.Status.eval(), detail=comp.par.Statusdetail.eval())
    T("enable.store_in_test_datadir", ext.worker is not None and ext.worker.datadir == os.environ.get("TDFLOW_DATADIR"))
    g = yield checkpoint("enabled", {"pid": os.getpid(), "level_opacity": fx["level_opacity"]})

    # Human edit interleaved after an agent change (harness made one just before this checkpoint).
    u = td.ui.undo
    u.startBlock("user edit")
    td.op("/project1/fx/noise1").par.amp = 0.25
    u.endBlock()
    yield frames(3)
    yield checkpoint("human_edited", {"amp": td.op("/project1/fx/noise1").par.amp.eval()})
    T("interleave.human_edit_kept", abs(td.op("/project1/fx/noise1").par.amp.eval() - 0.25) < 1e-6)
    lvl_after_conflict = td.op("/project1/fx/level1").par.opacity.eval()
    R["level_after_conflict"] = lvl_after_conflict

    # Kill switch: plain suspend, then a request already queued while the main thread was blocked.
    comp.par.Suspendagents = True
    yield frames(3)
    T("kill.suspended_status", comp.par.Status.eval() == "Suspended", status=comp.par.Status.eval())
    yield checkpoint("suspended", {})
    comp.par.Suspendagents = False
    yield frames(3)
    yield checkpoint("resumed", {})
    last_seq = (yield from store_op(ext, until, "events", limit=1))[0]["seq"]
    yield checkpoint("queue_block", {})                   # harness sends a call 0.5 s after this answer
    time.sleep(2.0)                                       # the request is now queued (main thread blocked)
    comp.par.Suspendagents = True                         # kill switch flipped before the job is dispatched
    R["kill_param_callback_synchronous"] = ext.suspended  # informational; OnFrame mirrors the parameter first
    yield frames(10)
    new = [e for e in (yield from store_op(ext, until, "events", limit=50)) if e["seq"] > last_seq]
    T("kill.queued_request_not_executed", not any(e["kind"] in ("intent", "outcome") for e in new),
      new_kinds=[e["kind"] for e in new])
    yield checkpoint("queue_checked", {})
    comp.par.Suspendagents = False
    yield frames(5)

    # Spike S9: TouchDesigner runs no frames while its window is minimized. The harness minimizes this process's
    # window for longer than the 20 s orphan watchdog, probes the endpoint, then restores it. Nothing in here
    # runs meanwhile (no frames), so the checks below see the state right after the restore.
    yield checkpoint("minimized", {}, seconds=240)
    yield frames(20)
    tr = ext.ctl.transport
    st = tr.stats() if tr is not None else {}
    T("minimized.MCP_STILL_ENABLED_AFTER_RESTORE", comp.par.Status.eval() == "Enabled" and ext.mcp_on and tr is not None
      and not tr.is_closed(), status=comp.par.Status.eval(), detail=comp.par.Statusdetail.eval())
    T("minimized.listener_kept_not_orphan_closed", st.get("not_cooking", 0) >= 2 and st.get("paused_kept", 0) >= 1
      and not st.get("orphan_closed"), stats={k: st.get(k) for k in ("not_cooking", "paused_kept", "orphan_closed", "timeouts")})
    T("minimized.BINDING_RE_VERIFIED_IN_THE_BACKGROUND", st.get("paused_reverifies", 0) >= 1
      and not st.get("paused_reverify_failed"), stats={k: st.get(k) for k in ("paused_reverifies", "paused_reverify_failed")})
    evs = yield from store_op(ext, until, "events", "error_code='TDFLOW_NOT_COOKING'", (), 20)
    T("minimized.refusals_audited_as_denials", len(evs) >= 2 and all(e["kind"] == "deny" for e in evs),
      kinds=[(e["kind"], e["tool"]) for e in evs])
    T("minimized.nothing_executed_while_minimized", abs(td.op("/project1/fx/level1").par.opacity.eval() - 0.12) < 1e-6,
      opacity=td.op("/project1/fx/level1").par.opacity.eval())

    # Audit store down → every call fails closed, MCP turns itself off; turning it on again reopens.
    before = td.op("/project1/fx/level1").par.opacity.eval()
    yield from store_op(ext, until, "close")              # test seam: the store dies inside the worker
    yield checkpoint("audit_down", {})
    yield frames(5)
    T("audit.fail_closed_no_change", td.op("/project1/fx/level1").par.opacity.eval() == before)
    T("audit.mcp_turned_off", comp.par.Status.eval() != "Enabled" and not comp.par.Enablemcp.eval(),
      status=comp.par.Status.eval(), detail=comp.par.Statusdetail.eval())
    T("audit.no_silent_reopen", not ext.worker.available)
    comp.par.Enablemcp = True                             # explicit user action: reopen and re-check
    yield until(lambda: comp.par.Status.eval() == "Enabled", 15, "reenable_after_audit_down")
    T("audit.reopened", comp.par.Status.eval() == "Enabled" and ext.worker.available)
    yield checkpoint("audit_up", {})

    # Integrity: verify, checkpoint, and detect tampering on a copy of the database.
    v1 = yield from store_op(ext, until, "verify")
    cp = yield from store_op(ext, until, "checkpoint")
    v2 = yield from store_op(ext, until, "verify")
    T("audit.chain_ok", v1["ok"] and v2["ok"] and v2["verified_through"] == cp["seq"], v1=v1, v2=v2)
    tmp = tempfile.mkdtemp(prefix="tdflow-tamper-", dir=os.environ.get("TDFLOW_RUN_DIR"))
    dd = ext.worker.datadir
    for f in ("tdflowmcp.sqlite", "audit.key", "checkpoints.jsonl"):
        shutil.copyfile(os.path.join(dd, f), os.path.join(tmp, f))
    for f in ("tdflowmcp.sqlite-wal", "tdflowmcp.sqlite-shm"):
        if os.path.exists(os.path.join(dd, f)):
            shutil.copyfile(os.path.join(dd, f), os.path.join(tmp, f))
    S = __import__("sys").modules["tdflowmcp.store"].Store
    copy = S(tmp).open()
    copy.conn.execute("DROP TRIGGER events_no_update")
    copy.conn.execute("UPDATE events SET outcome='forged' WHERE seq=(SELECT MAX(seq) FROM events WHERE kind='outcome')")
    vt = copy.verify()
    copy.close()
    T("audit.tamper_detected", not vt["ok"] and vt["first_bad_seq"] is not None, verify=vt)
    shutil.rmtree(tmp, ignore_errors=True)
    events = yield from store_op(ext, until, "events", limit=2000)
    R["audit_counts"] = {k: sum(1 for e in events if e["kind"] == k) for k in {e["kind"] for e in events}}
    T("audit.intent_before_every_outcome", _intent_pairs(events), counts=R["audit_counts"])
    sess = yield from store_op(ext, until, "sessions")
    T("sessions.recorded", len(sess) >= 1, sessions=len(sess))
    tokens_rows = yield from store_op(ext, until, "token_rows")
    diag = ext.Diagnostics()
    T("threads.dispatch_on_main_thread_only", diag["dispatch_threads"]["other"] == 0 and diag["dispatch_threads"]["main"] > 0,
      dispatch=diag["dispatch_threads"])
    T("frame.no_errors", diag["frame_errors"] == 0, last=diag["last_frame_error"])
    params = {p.name: str(p.eval()) for p in comp.customPars}
    yield checkpoint("scan", {"params": params, "tell": ext.tell_text, "tellagent_dat": comp.op("tellagent").text,
                              "events": events, "token_rows": [list(r) for r in tokens_rows], "diagnostics": diag})
    # Revocation is immediate (harness revoked one token); disabling MCP closes /mcp.
    yield checkpoint("revoked", {})
    comp.par.Enablemcp = False
    yield frames(5)
    T("disable.status", comp.par.Status.eval() == "Setup", status=comp.par.Status.eval())
    yield checkpoint("disabled", {})
    R["datadir_files"] = sorted(os.listdir(ext.worker.datadir))
    R["frame_stats_onframe"] = ext.FrameStats()


def _intent_pairs(events):
    """Every executed call has an intent before its outcome (same request id)."""
    by = {}
    for e in sorted(events, key=lambda x: x["seq"]):
        if e["kind"] in ("intent", "outcome") and e.get("request_id"):
            by.setdefault((e["request_id"], e["tool"]), []).append(e["kind"])
    return bool(by) and all(v[0] == "intent" and "outcome" in v for v in by.values())


def _restart(comp, ext, T, frames, until, checkpoint, R):
    comp.par.Enablemcp = True
    yield until(lambda: comp.par.Status.eval() == "Enabled", 15, "enable_after_restart")
    T("restart.enabled_with_persisted_agents", comp.par.Status.eval() == "Enabled",
      status=comp.par.Status.eval(), detail=comp.par.Statusdetail.eval())
    v = yield from store_op(ext, until, "verify")
    T("restart.audit_chain_ok", v["ok"] and v["checkpoints"] >= 1, verify=v)
    yield checkpoint("restart_enabled", {"pid": os.getpid()})
    comp.par.Enablemcp = False
    yield frames(5)
