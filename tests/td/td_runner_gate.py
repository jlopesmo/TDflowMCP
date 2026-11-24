"""Audit-worker gate and project-local storage, in TouchDesigner (imported by td_runner.py; runs INSIDE a
disposable project under .tmp/). Modes:

- ``gate``: project-local storage decision, pairing + tokens, idle / load / slow-disk frame impact, kill
  switch flipped while an intent is being written, queue overflow, an injected outcome-write failure.
- ``gate_crash``: same .toe path (identity recovered from the private registry), load, then the harness
  hard-kills TouchDesigner.
- ``gate_restart``: recovery after the crash (chain, keyed checkpoints, every reported success durable),
  then move / rename, copy and data-location changes inside the session (Save As).
- ``perf``: the same measured load for the Phase 1B build and the audit-worker build (``TDFLOW_DATADIR``).

The main thread never waits for the worker here either: store reads are submitted and polled.
"""
import os
import sqlite3
import sys

import td

M = __import__("td_runner_mcp")
L = __import__("td_runner").S                 # frame-interval label (td_runner.frame_stats)


def label(name):
    L["label"] = name


def store_op(ext, until, op, *args, **kw):
    return M.store_op(ext, until, op, *args, **kw)


def opacity():
    return td.op("/project1/fx/level1").par.opacity.eval()


def _comp_digest(comp):
    """Digest of the component's code and structure (DAT texts, operator paths/types, non-status parameters)."""
    import hashlib
    h = hashlib.sha256()
    for o in sorted([comp] + list(comp.findChildren()), key=lambda x: x.path):
        h.update(f"{o.path}|{o.OPType}\n".encode())
        if o.family == "DAT" and o.name not in ("tellagent", "diagnostics", "agents_view", "approval_view"):
            h.update(o.text.encode("utf-8", "replace"))
    for p in comp.customPars:
        # status values, user toggles, and the Agents page's selection (managed by the component as agents change)
        if p.readOnly or p.name in ("Enablemcp", "Suspendagents", "Agentselect", "Agentscope"):
            continue
        h.update(f"{p.name}={p.eval()}\n".encode())
    return h.hexdigest()


def unmatched_intents(events):
    """Intents (with a request id) that have no outcome: executions not accounted for durably."""
    by = {}
    for e in events:
        if e["kind"] in ("intent", "outcome") and e.get("request_id"):
            by.setdefault((e["request_id"], e["tool"], e.get("token_id")), []).append(e["kind"])
    return [k for k, v in by.items() if "outcome" not in v]


def run(mode, comp, ext, T, frames, until, checkpoint, R):
    if mode != "native_screens":
        # No real TouchDesigner dialog in automated runs: the native modes record dialogs and click their buttons;
        # every other mode takes the no-dialog path (parameters), as before.
        ext.connector.dialog_opener = lambda title, text, buttons, cb: False
    if mode == "probe_dialog":
        yield from probe_dialog(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "perf":
        yield from perf(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate":
        yield from gate(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate_crash":
        yield from crash(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate_restart":
        yield from restart(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "onboard":
        yield from onboard(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "native":
        yield from native_onboard(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "native_restart":
        yield from native_restart(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "native_restart2":
        yield from native_restart2(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "native_recover":
        yield from native_recover(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "native_screens":
        yield from native_screens(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "memory":
        yield from memory(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "memory_persist":
        yield from memory_persist(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "memory_restart":
        yield from memory_restart(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "memory_repo":
        yield from memory_repo(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "dogfood":
        yield from dogfood(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate_bench":
        yield from bench(comp, ext, T, frames, until, checkpoint, R)


def open_and_pair(comp, ext, T, frames, until, checkpoint):
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 15, "open")
    g = yield checkpoint("setup_ready", {"pid": os.getpid()})
    code = (g or {}).get("match_code")
    yield until(lambda: code and code in comp.par.Pairingrequest.eval(), 10, "pairing_param")
    comp.par.Approvepairing.pulse()
    yield until(lambda: comp.par.Pairingrequest.eval() == "", 10, "pairing_approved")
    yield checkpoint("pair_approved", {})


def enable(comp, until, name):
    comp.par.Enablemcp = True
    yield until(lambda: comp.par.Status.eval() == "Enabled", 20, name)


def measured(ext, name, cp_name, checkpoint, data=None):
    """Run a harness load at a checkpoint while frames are labelled; returns (harness reply, OnFrame stats)."""
    fs = getattr(ext, "FrameStats", None)
    if fs:
        fs(reset=True)
    label(name)
    g = yield checkpoint(cp_name, data or {})
    label("between")
    return g, (fs(reset=True) if fs else None)


# ---- perf: identical for both builds (only parameters + harness traffic) ------------------------
def perf(comp, ext, T, frames, until, checkpoint, R):
    M.fixtures()
    yield frames(5)
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "perf_enable")
    T("perf.enabled", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())
    fs = getattr(ext, "FrameStats", None)
    if fs:
        fs(reset=True)
    label("idle")
    yield frames(240)
    idle = fs(reset=True) if fs else None
    label("between")
    g, load = yield from measured(ext, "load", "load", checkpoint)
    R["perf"] = {"onframe_idle": idle, "onframe_load": load, "harness": g, "build": comp.par.Version.eval(),
                 "worker": hasattr(ext, "worker")}
    comp.par.Enablemcp = False
    yield frames(5)


# ---- gate --------------------------------------------------------------------------------------
def gate(comp, ext, T, frames, until, checkpoint, R):
    fx = M.fixtures()
    yield frames(5)
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup" and ext.worker is not None and ext.worker.ready.is_set(),
                15, "open_gate")
    yield frames(3)
    d = ext.storage
    toe = os.path.join(str(td.project.folder), str(td.project.name))
    folder = os.path.dirname(toe)
    exp = os.path.join(folder, ".tdflowmcp", os.path.splitext(os.path.basename(toe))[0])
    priv_root = os.environ["TDFLOW_PRIVATE_ROOT"]
    T("storage.project_local_default", d is not None and d.status == "NEW" and
      os.path.normcase(d.data_dir) == os.path.normcase(exp), status=d and d.status, reason=d and d.reason)
    gi = os.path.join(folder, ".tdflowmcp", ".gitignore")
    T("storage.gitignore_catch_all", os.path.exists(gi) and "*" in open(gi, encoding="utf-8").read().split())
    files = sorted(os.listdir(exp)) if os.path.isdir(exp) else []
    T("storage.project_folder_holds_only_audit_db", bool(files) and all(f.startswith("tdflowmcp.sqlite") for f in files),
      files=files)
    T("storage.private_holds_credentials_keys_checkpoints", os.path.normcase(d.private_dir).startswith(os.path.normcase(priv_root))
      and all(os.path.exists(os.path.join(d.private_dir, f)) for f in ("audit.key", "credentials.sqlite")))
    T("storage.projectid_param", comp.par.Projectid.eval() == d.project_id, pid=comp.par.Projectid.eval())
    shown = comp.par.Datadir.eval()
    T("storage.display_without_absolute_paths", folder not in shown and priv_root not in shown and ".tdflowmcp" in shown,
      shown=shown)
    T("storage.registry_entry_private", os.path.exists(os.path.join(priv_root, "registry", d.project_id + ".json")))
    T("storage.verified_at_open", ((ext.worker.open_info or {}).get("verify") or {}).get("ok") is True)
    T("storage.status_param", "New" in comp.par.Datastatus.eval(), status=comp.par.Datastatus.eval())
    R["storage"] = {"status": d.status, "files": files}

    g = yield checkpoint("setup_ready", {"pid": os.getpid()})
    code = (g or {}).get("match_code")
    yield until(lambda: code and code in comp.par.Pairingrequest.eval(), 10, "pairing_param")
    comp.par.Approvepairing.pulse()
    yield until(lambda: comp.par.Pairingrequest.eval() == "", 10, "pairing_approved")
    T("pairing.approved_after_durable_record", comp.par.Pairingrequest.eval() == "")
    yield checkpoint("pair_approved", {})
    yield from enable(comp, until, "gate_enable")
    T("gate.enabled", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())

    # Frame impact: idle, normal disk, simulated slow disk (50 ms before every storage operation).
    ext.FrameStats(reset=True)
    label("idle")
    yield frames(240)
    R["onframe"] = {"idle": ext.FrameStats(reset=True)}
    label("between")
    g, R["onframe"]["load"] = yield from measured(ext, "load", "load", checkpoint)
    R["latency"] = {"load": g}
    ext.worker.delay_s = 0.05
    g, R["onframe"]["load_slow"] = yield from measured(ext, "load_slow", "load_slow", checkpoint)
    ext.worker.delay_s = 0.0
    R["latency"]["load_slow"] = g
    slow = R["onframe"]["load_slow"]
    T("gate.slow_disk_main_thread_unaffected", slow and slow["n"] > 30 and slow["p99"] < 8.0 and slow["max"] < 50.0,
      onframe=slow, idle=R["onframe"]["idle"])
    T("gate.load_frame_cost_bounded", R["onframe"]["load"]["p99"] < 8.0, onframe=R["onframe"]["load"])

    # Kill switch flipped while the intent is being written (300 ms per storage operation).
    before = opacity()
    ext.worker.delay_s = 0.3
    d0 = ext.dispatch_threads["main"]
    yield checkpoint("race", {})
    yield until(lambda: ext.dispatch_threads["main"] > d0, 10, "race_plan_ran")
    comp.par.Suspendagents = True
    yield frames(90)
    ext.worker.delay_s = 0.0
    g = yield checkpoint("race_checked", {})
    T("race.not_executed", abs(opacity() - before) < 1e-9 and abs(opacity() - 0.42) > 1e-6, opacity=opacity())
    evs = yield from store_op(ext, until, "events", limit=30)
    T("race.intent_then_aborted_outcome", any(e["kind"] == "outcome" and e["outcome"] == "aborted" and
                                              e["error_code"] == "SUSPENDED" for e in evs),
      kinds=[(e["kind"], e["outcome"], e["error_code"]) for e in evs[:6]])
    comp.par.Suspendagents = False
    yield until(lambda: not ext.suspended, 10, "resume_after_race")
    T("kill.resume_only_after_durable_record", not ext.suspended and comp.par.Status.eval() == "Enabled")

    # Queue overflow: bounded queue of 2 with a slow disk; overflowing requests fail closed.
    ext.worker._q.maxsize = 2
    ext.worker.delay_s = 0.2
    g = yield checkpoint("overflow", {})
    ext.worker._q.maxsize = 1024
    ext.worker.delay_s = 0.0
    yield frames(30)
    codes = (g or {}).get("codes", [])
    T("overflow.fails_closed", any(c in ("AUDIT_UNAVAILABLE", "OUTCOME_UNRECORDED") for c in codes), codes=codes)
    T("overflow.counted", ext.worker.stats["overflow"] >= 1, stats=ext.worker.stats)
    R["overflow"] = {"codes": codes}
    if "OUTCOME_UNRECORDED" in codes:
        T("overflow.unrecorded_outcome_suspends_agents", comp.par.Suspendagents.eval() and
          ext.Diagnostics()["audit_failed"])
    ok_seqs = (g or {}).get("ok_seqs", [])
    if not ext.worker.available or comp.par.Suspendagents.eval():
        comp.par.Suspendagents = False
        yield until(lambda: not ext.suspended, 10, "resume_after_overflow")
    if comp.par.Status.eval() != "Enabled":
        yield from enable(comp, until, "reenable_after_overflow")
    evs = yield from store_op(ext, until, "events", limit=3000)
    outcomes = {e["seq"] for e in evs if e["kind"] == "outcome" and e["outcome"] == "ok"}
    T("overflow.every_reported_success_durable", all(s in outcomes for s in ok_seqs), n_ok=len(ok_seqs))
    T("overflow.recovered", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())

    # Outcome write fails after the change ran: not reported as success, agents suspended.
    st = ext.worker._store
    real = st.append

    def failing(kind, **kw):
        if kind == "outcome":
            raise sqlite3.OperationalError("disk I/O error (injected by the test)")
        return real(kind, **kw)
    st.append = failing
    g = yield checkpoint("outcome_fail", {})
    yield frames(10)
    T("outcome_fail.reported_unrecorded", (g or {}).get("code") == "OUTCOME_UNRECORDED", reply=g)
    T("outcome_fail.agents_suspended", bool(comp.par.Suspendagents.eval()) and comp.par.Status.eval() != "Enabled",
      status=comp.par.Status.eval())
    T("outcome_fail.store_failed_closed", not ext.worker.available)
    T("outcome_fail.second_call_rejected", (g or {}).get("after") in ("SUSPENDED", "MCP_DISABLED"), reply=g)
    comp.par.Suspendagents = False
    yield until(lambda: not ext.suspended, 10, "resume_after_outcome_fail")
    T("outcome_fail.resume_turns_mcp_off", comp.par.Status.eval() != "Enabled")
    yield from enable(comp, until, "reenable_after_outcome_fail")
    v = yield from store_op(ext, until, "verify")
    T("outcome_fail.recovered_chain_ok", comp.par.Status.eval() == "Enabled" and v["ok"], verify=v)
    evs = yield from store_op(ext, until, "events", limit=3000)
    um = unmatched_intents(evs)
    R["unmatched_after_gate"] = len(um)
    T("outcome_fail.unaccounted_execution_visible", len(um) >= 1, unmatched=len(um))
    g = yield checkpoint("gate_done", {})
    comp.par.Enablemcp = False
    yield frames(5)
    R["worker_stats"] = dict(ext.worker.stats)
    R["frame_stats_onframe"] = ext.FrameStats()


# ---- crash ---------------------------------------------------------------------------------------
def crash(comp, ext, T, frames, until, checkpoint, R):
    M.fixtures()
    yield frames(5)
    comp.par.Opensetup.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.ready.is_set(), 15, "open_crash")
    T("crash.identity_recovered_from_registry", ext.storage is not None and ext.storage.status == "OK",
      status=ext.storage and ext.storage.status)
    yield from enable(comp, until, "crash_enable")
    T("crash.enabled_with_persisted_agents", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())
    v = yield from store_op(ext, until, "verify")
    R["before_crash"] = v
    yield checkpoint("crash_load", {"pid": os.getpid()})      # the harness kills this process meanwhile
    yield frames(600)


# ---- restart after the crash, then relocation flows --------------------------------------------
def restart(comp, ext, T, frames, until, checkpoint, R):
    M.fixtures()                                      # a freshly generated .toe: recreate the test operators
    yield frames(5)
    comp.par.Opensetup.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.ready.is_set(), 15, "open_restart")
    v = (ext.worker.open_info or {}).get("verify") or {}
    T("crash.chain_and_checkpoints_verify_after_kill", v.get("ok") is True, verify=v)
    R["after_crash"] = v
    g = yield checkpoint("restart_ready", {})
    evs = yield from store_op(ext, until, "events", limit=5000)
    ok = {e["seq"] for e in evs if e["kind"] == "outcome" and e["outcome"] == "ok"}
    reported = (g or {}).get("ok_seqs", [])
    T("crash.every_reported_success_durable", bool(reported) and all(s in ok for s in reported),
      reported=len(reported), missing=[s for s in reported if s not in ok][:10])
    um = unmatched_intents(evs)
    base_um = int((g or {}).get("unmatched_before", 0))
    T("crash.unaccounted_only_in_flight", len(um) - base_um <= 8, new_unmatched=len(um) - base_um)
    R["crash"] = {"reported_ok": len(reported), "unmatched_new": len(um) - base_um, "events": v.get("events"),
                  "unverified_tail": v.get("unverified_tail")}
    yield from enable(comp, until, "enable_after_crash")
    T("crash.enable_after_restart", comp.par.Status.eval() == "Enabled")
    yield checkpoint("after_crash_call", {})
    comp.par.Enablemcp = False
    yield frames(5)
    ext = yield from relocation(comp, ext, T, frames, until, checkpoint, R)
    R["frame_stats_onframe"] = ext.FrameStats()


def reopen(comp, frames, until):
    """A fresh extension instance (what reopening the moved/copied .toe does), then Open Setup."""
    comp.par.reinitextensions.pulse()
    yield frames(40)
    ext = comp.ext.TDflowMCPExt
    ext.launcher = lambda url: True
    comp.par.Opensetup.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.ready.is_set(), 15, "reopen")
    return ext


def read_events(data_dir, private_dir):
    """Test-only direct read (never the component's own connection): event count of a database."""
    S = sys.modules["tdflowmcp.store"].Store
    st = S(data_dir, private_dir).open(readonly=True)
    try:
        return st.verify()
    finally:
        st.close()


def relocation(comp, ext, T, frames, until, checkpoint, R):
    d0 = ext.storage
    orig_toe = os.path.join(str(td.project.folder), str(td.project.name))
    n0 = read_events(d0.data_dir, d0.private_dir)["events"]
    folder = os.path.dirname(orig_toe)
    # 1. Rename: Save As next to the original, then the original file disappears -> MOVED.
    renamed = os.path.join(folder, "td_tests_renamed.toe")
    td.project.save(renamed)
    yield frames(5)
    # TouchDesigner may add its incremental save number (td_tests_renamed.1.toe): still the same base name.
    T("move.save_as", str(td.project.name).startswith("td_tests_renamed"), project_name=str(td.project.name))
    os.remove(orig_toe)                                      # disposable test project only
    ext = yield from reopen(comp, frames, until)
    d = ext.storage
    T("move.detected_not_adopted_silently", d is not None and d.status == "MOVED" and not ext.worker.available,
      status=d and d.status)
    comp.par.Enablemcp = True
    yield frames(20)
    # The refusal comes once the listener is verified (Setup state); that can take longer than 20 frames after a
    # reopen, so wait for it (MCP must never turn on meanwhile).
    yield until(lambda: not comp.par.Enablemcp.eval() or comp.par.Status.eval() == "Enabled", 10, "move_refused")
    T("move.mcp_refused_until_confirmed", not comp.par.Enablemcp.eval() and comp.par.Status.eval() != "Enabled",
      detail=comp.par.Statusdetail.eval(), status=comp.par.Status.eval())
    T("move.status_explains", "Confirm Move" in comp.par.Datastatus.eval(), status=comp.par.Datastatus.eval())
    comp.par.Confirmmove.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.available, 20, "confirm_move")
    yield frames(3)
    d = ext.storage
    res = ext.storage_result or {}
    T("move.verified_copy", res.get("verified") is True and res.get("events") == n0, result=res, n0=n0)
    T("move.new_location", d.data_dir.endswith(os.path.join(".tdflowmcp", "td_tests_renamed")), data_dir=d.data_dir)
    T("move.source_untouched", read_events(d0.data_dir, d0.private_dir)["events"] == n0)
    v = (ext.worker.open_info or {}).get("verify") or {}
    T("move.chain_verifies_at_new_location", v.get("ok") is True and v.get("events") == n0 + 1, verify=v)
    yield from enable(comp, until, "enable_after_move")
    yield checkpoint("after_move", {})
    comp.par.Enablemcp = False
    yield frames(5)
    # 2. Copy: Save As into another folder while the renamed original still exists -> COPIED.
    moved = ext.storage
    yield frames(30)                                          # let MCP-off records land
    n_moved = read_events(moved.data_dir, moved.private_dir)["events"]
    copy_dir = os.path.join(folder, "copy")
    os.makedirs(copy_dir, exist_ok=True)
    td.project.save(os.path.join(copy_dir, "td_tests_renamed.toe"))
    yield frames(5)
    ext = yield from reopen(comp, frames, until)
    d = ext.storage
    T("copy.detected", d is not None and d.status == "COPIED" and not ext.worker.available, status=d and d.status)
    comp.par.Claimproject.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.available, 20, "claim")
    yield frames(3)
    d = ext.storage
    T("copy.claimed_new_identity", d.project_id != moved.project_id and comp.par.Projectid.eval() == d.project_id
      and os.path.normcase(d.data_dir).startswith(os.path.normcase(copy_dir)), pid=d.project_id)
    n_after = read_events(moved.data_dir, moved.private_dir)["events"]
    T("copy.original_untouched", n_after == n_moved, before=n_moved, after=n_after)
    T("copy.no_tokens_carried_over", not ext.identity.agents)
    # 3. Data location changed to private storage -> RELOCATE, confirmed with a verified copy.
    claimed = d
    comp.par.Datalocation = "private"
    yield frames(3)
    comp.par.Opensetup.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.ready.is_set(), 15, "relocate_open")
    d = ext.storage
    T("relocate.detected", d is not None and d.status == "RELOCATE" and not ext.worker.available, status=d and d.status)
    comp.par.Confirmmove.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.available, 20, "relocate_confirm")
    d = ext.storage
    T("relocate.private_storage", os.path.normcase(d.data_dir).startswith(os.path.normcase(os.environ["TDFLOW_PRIVATE_ROOT"]))
      and "Private storage" in comp.par.Datadir.eval(), shown=comp.par.Datadir.eval())
    T("relocate.source_untouched", os.path.exists(os.path.join(claimed.data_dir, "tdflowmcp.sqlite")))
    R["relocation"] = {"moved_events": n_moved}
    return ext


# ---- bench: where does MCP latency go? (handoff costs inside TouchDesigner) ---------------------
def bench(comp, ext, T, frames, until, checkpoint, R):
    import queue
    import threading
    import time
    comp.par.Opensetup.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.available, 15, "bench_open")
    w, out, jobs = ext.worker, {}, queue.Queue()
    L["bench_jobs"] = jobs

    def pct(xs):
        s = sorted(xs)
        return {"p50": round(s[len(s) // 2], 2), "p95": round(s[int(len(s) * .95)], 2), "max": round(s[-1], 2)}

    def run_bg():
        for name, fn in (("worker_read", lambda: w.call("events", limit=1)),
                         ("worker_append", lambda: w.call("append", "bench")),
                         ("main_handoff", None)):
            xs = []
            for _ in range(60):
                t0 = time.perf_counter()
                if fn:
                    fn()
                else:
                    ev = threading.Event()
                    jobs.put(ev.set)
                    ev.wait(5)
                xs.append((time.perf_counter() - t0) * 1000)
            out[name] = pct(xs)
        out["switchinterval_ms"] = sys.getswitchinterval() * 1000
        out["done"] = True
    # Measurement only: the same durable append directly on the main thread (a separate temp database).
    import tempfile
    S = sys.modules["tdflowmcp.store"].Store
    tmpd = tempfile.mkdtemp(prefix="bench-main-", dir=os.environ.get("TDFLOW_RUN_DIR"))
    st = S(tmpd).open()
    xs = []
    for _ in range(60):
        t0 = time.perf_counter()
        st.append("bench")
        xs.append((time.perf_counter() - t0) * 1000)
    st.close()
    out["main_thread_append"] = pct(xs)
    yield frames(5)
    th = threading.Thread(target=run_bg, daemon=True)
    th.start()
    yield until(lambda: out.get("done"), 120, "bench_done")
    R["bench"] = out
    T("bench.completed", bool(out.get("done")), **{k: v for k, v in out.items() if k != "done"})


# ---- native: drop the .tox -> Check Ready -> Connect Agent -> Approve -> Start Working (owner D22, ADR-0015) --------
TOKEN_SHAPE = __import__("re").compile(r"tdf_[A-Za-z0-9]{12}\.[A-Za-z0-9_\-]{43,}")


def _dialogs(ext):
    """Record TouchDesigner dialogs instead of opening them; the test clicks a button by calling the callback."""
    rec = []
    ext.connector.dialog_opener = lambda title, text, buttons, cb: (rec.append(
        {"title": title, "text": text, "buttons": list(buttons), "cb": cb}), True)[1]
    return rec


def _stage(ext):
    return (ext.connector.state or {}).get("stage")


def _cfg(path):
    with open(path, "rb") as f:
        return f.read()


def _no_token_in_component(comp):
    hits = [p.name for p in comp.customPars if TOKEN_SHAPE.search(str(p.eval()))]
    hits += [o.path for o in comp.findChildren(type=td.DAT) if TOKEN_SHAPE.search(o.text or "")]
    return hits


def _events(ext, until, tool):
    rows = yield from store_op(ext, until, "events", "tool = ?", (tool,), 50)
    return rows


def _connect_and_pick(comp, ext, dialogs, frames, until, name):
    """Pulse Connect Agent; when several agents were found, pick Claude Code in the chooser dialog."""
    n = len(dialogs)
    comp.par.Connectagent.pulse()
    yield frames(2)
    if len(dialogs) > n and dialogs[-1]["title"] == "Connect a coding agent":
        dialogs[-1]["cb"](dialogs[-1]["buttons"].index("Claude Code"))
    yield until(lambda: _stage(ext) in ("awaiting", "failed", "waiting"), 30, name)


def native_onboard(comp, ext, T, frames, until, checkpoint, R):
    """The primary acceptance scenario, with a disposable client home (TDFLOW_CLIENT_HOME) standing in for the user's
    profile: automatic and manual diagnostics, Auto, Connect Agent, review, cancel, approve, the agent's first real
    request, operators created in the authorized network, a denied change outside it, Suspend / resume, save."""
    M.fixtures()
    p = comp.par
    home = os.environ["TDFLOW_CLIENT_HOME"]
    cfg = os.path.join(home, ".claude.json")
    original = _cfg(cfg)
    dialogs = _dialogs(ext)
    yield frames(10)
    T("first_run.AUTOMATIC_DIAGNOSTICS", p.Compatsummary.eval().startswith(("Ready", "Partly", "Not ready"))
      and bool(ext.checks) and "(automatic)" in p.Lastcheck.eval(), summary=p.Compatsummary.eval(),
      last=p.Lastcheck.eval())
    T("first_run.NOTHING_LISTENS_OR_IS_CREATED", p.Status.eval() == "Off" and ext.ctl.transport is None
      and ext.worker is None)
    T("first_run.environment_ready_next_step", p.Statusdetail.eval() == "Ready: pulse Connect Agent",
      detail=p.Statusdetail.eval())
    p.Rundiagnostics.pulse()
    yield frames(1)
    T("first_run.RUN_DIAGNOSTICS_SHOWS_RUNNING", p.Compatsummary.eval() == "Running diagnostics…",
      summary=p.Compatsummary.eval())
    yield until(lambda: "Checks done" in p.Statusdetail.eval(), 5, "diag_done")
    sc = p.Compatsummary.eval()
    rep = comp.op("diagnostics").text
    T("first_run.MANUAL_DIAGNOSTICS_COMPLETE_WITH_COUNTS", "Checks done" in p.Statusdetail.eval()
      and "(manual)" in p.Lastcheck.eval() and " ok" in sc and len(sc) <= 26
      and all(w in rep for w in ("Passed ", "warnings ", "failed ", "pending ")), detail=p.Statusdetail.eval(),
      summary=sc)
    T("first_run.PENDING_NEVER_COUNTED_AS_PASSED", [c.result for c in ext.checks if c.id == "net.bind"] == ["unknown"],
      checks={c.id: c.result for c in ext.checks})
    T("first_run.AUTO_FINDS_CLAUDE_CODE_WITHOUT_RUNNING_IT", "claude-code" in ext.native.detected
      and p.Codingagent.eval() == "auto" and "Claude Code (auto)" in p.Detectedclients.eval()
      and len(p.Detectedclients.eval()) <= 80, detected=ext.native.detected, installed=p.Detectedclients.eval())
    several = len(ext.native.selected()) > 1
    R["several_detected"] = ext.native.detected
    p.Agentaccess = "builder"
    p.Allowedscope = "/project1/fx/**"
    yield frames(2)
    T("first_run.instructions_need_no_token", "Connect Claude Code (automatic" in ext.tell_text
      and "TDFLOW_TOKEN" not in ext.tell_text.split("## Connect Claude Code", 1)[1].split("## ", 1)[0])
    # 1. Review, then cancel: nothing changes.
    n = len(dialogs)
    yield from _connect_and_pick(comp, ext, dialogs, frames, until, "connect_plan")
    d = dialogs[-1]
    text = d["text"]
    if several:
        # Usability review: one agent can be set up automatically, so Auto recommends it without asking and names
        # the others (manual setup) in the plan.
        T("connect.AUTO_RECOMMENDS_THE_AUTOMATIC_AGENT", len(dialogs) == n + 1 and d["title"] == "Connect Claude Code"
          and "Also found:" in text, titles=[x["title"] for x in dialogs[n:]])
    T("connect.CONSENT_SHOWS_THE_PLAN", all(x in text for x in (
        "~/.claude.json", "other-server", "TURNED ON", "Undo", "Builder", "/project1/fx/**",
        "protected storage", "backup", "Then start a new Claude Code chat")) and
      d["buttons"] == ["Approve", "Cancel"], text=text, buttons=d["buttons"])
    T("connect.consent_has_no_secret_or_user_path", not TOKEN_SHAPE.search(text) and home not in text)
    T("connect.REVIEW_SHOWN_IN_NEXT_STEP", p.Statusdetail.eval() == "Approve in the dialog.",
      detail=p.Statusdetail.eval())
    T("connect.NOTHING_CHANGED_BEFORE_APPROVAL", _cfg(cfg) == original and len(ext.identity.agents) == 0
      and not p.Enablemcp.eval())
    d["cb"](1)                                                   # Cancel
    yield frames(2)
    T("connect.CANCEL_CHANGES_NOTHING", ext.connector.state is None and _cfg(cfg) == original
      and len(ext.identity.agents) == 0 and "Cancelled" in p.Statusdetail.eval(), detail=p.Statusdetail.eval())
    # 2. Approve.
    yield from _connect_and_pick(comp, ext, dialogs, frames, until, "connect_plan2")
    seen = []
    lines = []
    dialogs[-1]["cb"](0)                                         # Approve (the plan turns on Enable MCP)

    def progressed():
        seen.append(_stage(ext))
        lines.append(p.Statusdetail.eval())
        return _stage(ext) in ("waiting", "failed")
    yield until(progressed, 40, "connect_configure")
    yield frames(3)
    T("connect.VISIBLE_PROGRESS", "configuring" in seen and _stage(ext) == "waiting"
      and any(x.startswith("Configuring Claude Code") for x in lines) and all(len(x) <= 26 for x in lines),
      stages=sorted(set(map(str, seen))), long=[x for x in set(lines) if len(x) > 26],
      failed=(ext.connector.state or {}).get("line"))
    T("connect.MCP_ENABLED_ONLY_BY_THE_APPROVAL", p.Enablemcp.eval() and p.Status.eval() == "Enabled",
      status=p.Status.eval())
    T("connect.CONFIGURED_IS_NOT_CONNECTED", p.Statusdetail.eval() in (
        "Start a Claude Code chat.", "Save, then start a chat.")
      and "none" in p.Connectedagents.eval(), detail=p.Statusdetail.eval(), connected=p.Connectedagents.eval())
    T("connect.connect_view_cleared_after_approval", comp.op("connect_view").text == "")
    now = __import__("json").loads(_cfg(cfg).decode("utf-8"))
    old = __import__("json").loads(original.decode("utf-8"))
    entry = now["mcpServers"]["tdflow"]
    T("connect.ENTRY_WRITTEN_EVERYTHING_ELSE_PRESERVED",
      entry.get("url") == "http://127.0.0.1:9480/mcp" and "TDflowMCP" in entry.get("headersHelper", "")
      and {k: v for k, v in now.items() if k != "mcpServers"} == {k: v for k, v in old.items() if k != "mcpServers"}
      and now["mcpServers"]["other-server"] == old["mcpServers"]["other-server"])
    T("connect.NO_TOKEN_IN_THE_CONFIG_OR_COMPONENT", not TOKEN_SHAPE.search(_cfg(cfg).decode("utf-8"))
      and not _no_token_in_component(comp), hits=_no_token_in_component(comp))
    rec = ext.connector.records.get("claude-code") or {}
    vault_file = os.path.join(ext._private_root(), "connect", "claude-code", rec.get("pid", "x") + ".dpapi")
    T("connect.CREDENTIAL_IN_THE_PROTECTED_STORE", os.path.isfile(vault_file)
      and not TOKEN_SHAPE.search(open(vault_file, encoding="ascii").read()))
    nonce = "0123456789abcdef" * 2
    T("connect.LISTENER_PROVES_THE_PROJECT_KEY", bool(ext._connect_key) and ext._route_proof(type("C", (), {
        "path": "/.well-known/tdflowmcp-proof/" + nonce})()).status == 200)
    wk = __import__("json").loads(ext._static_assets()["/.well-known/tdflowmcp"][1])
    T("connect.WELL_KNOWN_NAMES_THIS_PROJECT", wk.get("connect") == ext.storage.project_id, wk=wk)
    ev = yield from _events(ext, until, "client_connect")
    T("connect.AUDITED_INTENT_BEFORE_OUTCOME", [e["outcome"] for e in reversed(ev)] == ["cancelled", "intent", "ok"],
      outcomes=[e["outcome"] for e in reversed(ev)])
    R["connect_record"] = {k: rec.get(k) for k in ("client", "agent_id", "shown_path", "action", "created_key")}
    g = yield checkpoint("connect_live", {"project_folder": str(td.project.folder), "pid": ext.storage.project_id},
                         seconds=900)
    yield frames(5)
    yield until(lambda: _stage(ext) == "connected", 10, "connect_verified")
    T("connect.SAVE_REMINDER_AFTER_THE_FLOW", "Save the project now." in p.Statusdetail.eval()
      or p.Statusdetail.eval() == "Agent Connected — Ready", detail=p.Statusdetail.eval())
    ext._note_until = 0.0                                        # the reminder times out (15 s)
    yield frames(3)
    T("connect.AGENT_CONNECTED_READY", p.Statusdetail.eval() == "Agent Connected — Ready",
      detail=p.Statusdetail.eval())
    T("connect.ACTUAL_CLIENT_IDENTITY_SHOWN", __import__("re").search(r"Claude Code \d+\.\d+", p.Connectedagents.eval())
      is not None, connected=p.Connectedagents.eval())
    ev = yield from _events(ext, until, "client_connect")
    T("connect.VERIFIED_ONLY_AFTER_A_REAL_REQUEST", any(e["outcome"] == "verified" for e in ev))
    made = td.op("/project1/fx/probe1")
    T("work.AGENT_CREATED_AN_OPERATOR_IN_SCOPE", made is not None, ops=[o.name for o in td.op("/project1/fx").children])
    T("work.OUT_OF_SCOPE_UNCHANGED", td.op("/project1/out1") is not None and td.op("/project1/probe_outside") is None)
    denied = yield from store_op(ext, until, "events", "kind = ? AND token_id IS NOT NULL", ("deny",), 50)
    T("work.DENIAL_AUDITED", len(denied) >= 1 and bool(g.get("denied_by_server")), denied=len(denied),
      codes=sorted({str(e.get("error_code")) for e in denied}))
    p.Suspendagents = True
    yield frames(5)
    T("first_run.SUSPEND", p.Status.eval() == "Suspended", status=p.Status.eval())
    yield checkpoint("connect_suspended", {})
    p.Suspendagents = False
    yield frames(5)
    T("first_run.RESUME", p.Status.eval() == "Enabled")
    yield checkpoint("connect_resumed", {})
    R["native"] = g
    p.Enablemcp = False
    yield frames(5)
    toe = os.path.join(str(td.project.folder), str(td.project.name))
    drv = td.op("/project1/test_driver/drive")
    drv.par.active = False
    td.project.save()
    drv.par.active = True
    R["native_toe"] = toe


def probe_dialog(comp, ext, T, frames, until, checkpoint, R):
    """Diagnostic: how TouchDesigner's pop-up dialog is reached in this build (no product code involved)."""
    info = {}
    for expr in ("td.op.TDResources", "td.op.TDResources.PopDialog", "td.op('/sys/TDResources')",
                 "td.op('/sys/TDResources/popDialog')", "td.op('/sys/TDResources').op('popDialog')"):
        try:
            v = eval(expr, {"td": td})
            info[expr] = f"{type(v).__name__}: {getattr(v, 'path', '')} {[n for n in dir(v) if 'pen' in n][:12]}"
        except Exception as e:
            info[expr] = f"ERR {type(e).__name__}: {e}"[:200]
    pop = td.op("/sys/TDResources/popDialog")
    try:
        info["exts"] = [str(e) for e in pop.extensions]
        info["ext_names"] = [n for n in dir(pop.ext) if not n.startswith("_")][:20]
    except Exception as e:
        info["exts"] = f"ERR {type(e).__name__}: {e}"
    got = []
    for label, fn in (("pop.OpenDefault", lambda: pop.OpenDefault),
                      ("ext.PopDialogExt.OpenDefault", lambda: pop.ext.PopDialogExt.OpenDefault)):
        try:
            f = fn()
            info[label] = str(f)[:120]
            f(text="probe text", title="Probe", buttons=["One", "Two", "Cancel"], callback=lambda i: got.append(i),
              escButton=3, escOnClickAway=False, enterButton=3)
            info[label + " call"] = "ok"
            break
        except Exception as e:
            info[label] = f"ERR {type(e).__name__}: {e}"[:300]
    yield frames(10)
    for label, fn in (("OnButtonClicked(1)", lambda: pop.ext.PopDialogExt.OnButtonClicked(1)),
                      ("OnButtonClicked(1) via pop", lambda: pop.OnButtonClicked(1))):
        try:
            fn()
            info[label] = "ok"
            break
        except Exception as e:
            info[label] = f"ERR {type(e).__name__}: {e}"[:300]
    yield frames(5)
    info["callback_got"] = [repr(x)[:300] for x in got]
    try:
        w = pop.par.w
        info["w_before"] = {"mode": str(w.mode), "expr": w.expr, "val": w.val, "eval": w.eval(),
                            "bindExpr": getattr(w, "bindExpr", None)}
        ext.connector._open_dialog("Probe2", "text", ["OK", "Cancel"], lambda i: info.setdefault("probe2_choice", i))
        info["w_open"] = {"mode": str(w.mode), "eval": w.eval()}
        yield frames(5)
        pop.ext.PopDialogExt.OnButtonClicked(1)
        trace = []
        for _ in range(60):
            yield frames(1)
            trace.append((int(w.eval()), repr(getattr(ext.connector, "_restore_after", None))[:30]))
        info["w_trace"] = trace
        info["w_after"] = {"mode": str(w.mode), "expr": w.expr, "val": w.val, "eval": w.eval(),
                           "choice": info.get("probe2_choice"), "dialog": ext.connector.dialog,
                           "pending": repr(getattr(ext.connector, "_restore_after", None))[:80],
                           "frame_errors": ext.frame_errors, "last_frame_error": ext.last_frame_error,
                           "same_connector": ext.connector is comp.ext.TDflowMCPExt.connector}
    except Exception as e:
        info["w_probe"] = f"ERR {type(e).__name__}: {e}"[:300]
    try:
        hits = []
        for d in pop.findChildren(type=td.DAT, maxDepth=2):
            for i, line in enumerate((d.text or "").splitlines()):
                if ".par.w" in line or "par.w " in line or "'w'" in line or ".w =" in line or "width" in line.lower():
                    hits.append(f"{d.name}:{i}: {line.strip()[:140]}")
        info["width_src"] = hits[:40]
        pop.par.w = 260
        yield frames(3)
        info["w_manual"] = pop.par.w.eval()
    except Exception as e:
        info["width_src"] = f"ERR {type(e).__name__}: {e}"[:300]
    try:
        info["pars"] = {p.name: str(p.eval())[:40] for p in pop.customPars}
        info["size"] = (pop.width, pop.height)
        info["win"] = {p.name: str(p.eval())[:60] for p in pop.op("window").pars()
                       if p.name in ("winw", "winh", "winoffsetx", "winoffsety", "size", "justifyoffsetto")} \
            if pop.op("window") is not None else "no window comp"
        info["opdefault_src"] = [l for l in pop.op("PopDialogExt").text.splitlines()
                                 if "width" in l.lower() or "Width" in l][:25]
    except Exception as e:
        info["pars"] = f"ERR {type(e).__name__}: {e}"[:200]
    try:
        import inspect
        info["onbutton_sig"] = str(inspect.signature(pop.ext.PopDialogExt.OnButtonClicked))
    except Exception as e:
        info["onbutton_sig"] = f"ERR {e}"
    try:
        import inspect
        info["sig"] = str(inspect.signature(pop.ext.PopDialogExt.OpenDefault))[:400]
    except Exception as e:
        info["sig"] = f"ERR {type(e).__name__}: {e}"[:200]
    try:
        info["methods"] = [n for n in dir(pop.ext.PopDialogExt) if not n.startswith("_")][:40]
    except Exception as e:
        info["methods"] = f"ERR {e}"
    R["probe"] = info
    yield frames(2)


def native_screens(comp, ext, T, frames, until, checkpoint, R):
    """Visual review: each custom parameter page in TouchDesigner's own parameter window, in three states (fresh,
    reviewing a connection, connected and MCP enabled), plus the real Connect Agent dialog."""
    M.fixtures()
    p = comp.par
    yield frames(10)
    comp.openParameters()

    def shots(stage):
        for page in ("TDflowMCP", "Agents", "Web", "Advanced"):
            comp.currentPage = page
            yield frames(15)
            yield checkpoint(f"screen_{stage}_{page}".replace("-", "_"), {"stage": stage, "page": page})
    yield from shots("1-fresh")
    p.Codingagent = "claude-code"
    p.Connectagent.pulse()                                   # the real TouchDesigner dialog opens
    yield until(lambda: _stage(ext) == "awaiting", 30, "screens_plan")
    yield frames(20)
    T("screens.REAL_DIALOG_OPENED", ext.connector.dialog is not None, dialog=ext.connector.dialog)
    T("screens.DIALOG_WIDENED_FOR_READING", td.op("/sys/TDResources/popDialog").par.w.eval() >= 480,
      width=td.op("/sys/TDResources/popDialog").par.w.eval())
    T("screens.ONE_DIALOG_AT_A_TIME", ext.connector.dialog_opener("x", "y", ["OK", "Cancel"], lambda i: None) is False)
    yield checkpoint("screen_2-dialog", {"stage": "2-dialog", "page": "dialog"})
    before = ext.checks_at
    p.Rundiagnostics.pulse()                                 # TouchDesigner keeps working while the dialog is open
    yield until(lambda: ext.checks_at > before, 10, "pulse_with_dialog")
    T("screens.PULSES_WORK_WHILE_A_DIALOG_IS_OPEN", ext.checks_at > before)
    comp.currentPage = "TDflowMCP"
    yield from shots("2-review")
    pop = td.op("/sys/TDResources/popDialog")
    try:
        pop.ext.PopDialogExt.OnButtonClicked(1)               # the real dialog's first button: Approve
    except Exception:
        ext.connector.approve()
    yield until(lambda: _stage(ext) in ("waiting", "failed"), 40, "screens_connect")
    T("screens.REAL_DIALOG_APPROVE_CONNECTS", _stage(ext) == "waiting", stage=_stage(ext),
      line=(ext.connector.state or {}).get("line"))
    yield frames(10)
    T("screens.DIALOG_WIDTH_RESTORED", abs(pop.par.w.eval() - 260) < 1 and str(pop.par.w.mode).endswith("CONSTANT"),
      width=pop.par.w.eval())
    yield frames(10)
    yield from shots("3-enabled")
    p.Enablemcp = False
    yield frames(5)


def _reopened(comp, ext, T, frames, checkpoint, tag):
    """rc4 (UX-09, the owner's rc3 finding): what a reopened, previously connected project shows before anything is
    loaded. Storage stays closed (no listener, no files), but existing agents are never reported as absent."""
    p = comp.par
    yield frames(10)
    T(f"{tag}.MCP_OFF_AFTER_REOPEN", not p.Enablemcp.eval() and p.Status.eval() == "Off", status=p.Status.eval())
    T(f"{tag}.AUTOMATIC_DIAGNOSTICS", "(automatic)" in p.Lastcheck.eval(), last=p.Lastcheck.eval())
    T(f"{tag}.NOTHING_OPENED_BY_LOADING", ext.worker is None and ext.ctl.transport is None and ext.identity is None)
    T(f"{tag}.ONE_COMPONENT_REOPENED", comp.name == "TDflowMCP" and td.op("/project1/TDflowMCP1") is None)
    T(f"{tag}.NOT_LOADED_IS_NOT_REPORTED_AS_EMPTY", p.Agentsummary.eval() == "1 agent · not loaded"
      and "(not loaded yet)" in list(p.Agentselect.menuLabels) and "0 agents" not in p.Agentsummary.eval(),
      summary=p.Agentsummary.eval(), labels=list(p.Agentselect.menuLabels))
    T(f"{tag}.NEXT_STEP_TURN_ON_ENABLE_MCP", p.Statusdetail.eval() == "Turn on Enable MCP.", detail=p.Statusdetail.eval())
    T(f"{tag}.INSTRUCTIONS_KNOW_IT_IS_SET_UP", "already set up for this project" in ext.tell_text)
    g = yield checkpoint(f"{tag}_inspected", {"projectid": str(p.Projectid.eval())})
    T(f"{tag}.PROJECT_IDENTITY_KEPT", str(p.Projectid.eval()) == g.get("pid") and bool(g.get("pid")),
      pid=str(p.Projectid.eval()))
    return g


def _enable_and_reconnect(comp, ext, T, frames, until, checkpoint, tag, g, live):
    """Turn on Enable MCP only (no Connect Agent): the existing agent and Claude Code configuration must work."""
    p = comp.par
    p.Enablemcp = True                                       # the user's explicit decision
    yield until(lambda: p.Status.eval() == "Enabled", 30, tag + "_enable")
    yield frames(5)
    rec = ext.connector.records.get("claude-code") or {}
    aid = rec.get("agent_id")
    n = len(ext.identity.agents)
    T(f"{tag}.EXISTING_AGENT_LOADED", aid == g.get("aid") and n == g.get("agents"), aid=aid, agents=n)
    T(f"{tag}.waiting_for_the_agent", p.Statusdetail.eval() in ("Start a Claude Code chat.", "Waiting for the agent…",
                                                                "MCP on. Waiting for agent"), detail=p.Statusdetail.eval())
    v0 = yield from store_op(ext, until, "verify")
    T(f"{tag}.AUDIT_CHAIN_CONTINUOUS", v0.get("chain_ok") is True and v0.get("events", 0) >= g.get("events", 0),
      events=v0.get("events"), before=g.get("events"))
    r = yield checkpoint(f"{tag}_live", {"aid": aid, "live": live})
    yield frames(5)
    yield until(lambda: p.Statusdetail.eval() == "Agent Connected — Ready", 10, tag + "_connected")
    T(f"{tag}.AGENT_CONNECTED_READY", p.Statusdetail.eval() == "Agent Connected — Ready", detail=p.Statusdetail.eval())
    live_tokens = [t for t in ext.identity.tokens.values() if t.agent_id == aid and not t.revoked_at]
    T(f"{tag}.NO_DUPLICATE_AGENT_OR_TOKEN", len(ext.identity.agents) == n and len(live_tokens) == 1
      and len(ext.connector.records) == 1, agents=len(ext.identity.agents), tokens=len(live_tokens))
    v1 = yield from store_op(ext, until, "verify")
    # A reconnect alone (mcp list: initialize, tools/list) writes no audited tool call; the live session does.
    grew = v1.get("events", 0) > v0.get("events", 0)
    T(f"{tag}.AUDIT_VERIFIES" + ("_AND_GREW" if live else ""), v1.get("chain_ok") is True and (grew or not live)
      and v1.get("events", 0) >= v0.get("events", 0), events=v1.get("events"), before=v0.get("events"))
    R_last = {"aid": aid, "agents": n, "events": v1.get("events")}
    return r, R_last


def native_restart(comp, ext, T, frames, until, checkpoint, R):
    """Launch 2 (restart 1): inspect before loading; Enable MCP only; the same agent reconnects; a live session reads,
    creates, connects, updates and undoes; no duplicates; audit continuous; save and close cleanly."""
    _dialogs(ext)
    g = yield from _reopened(comp, ext, T, frames, checkpoint, "restart")
    r, last = yield from _enable_and_reconnect(comp, ext, T, frames, until, checkpoint, "restart", g, live=True)
    lv = td.op("/project1/fx/probe_level")
    T("work.CREATED_AND_CONNECTED", lv is not None and len(lv.inputs) == 1 and lv.inputs[0].name == "noise1",
      inputs=[i.name for i in lv.inputs] if lv is not None else None)
    T("work.UNDO_RESTORED_THE_PARAMETER", lv is not None and abs(lv.par.opacity.eval() - 1.0) < 1e-6,
      opacity=lv.par.opacity.eval() if lv is not None else None)
    ev = yield from store_op(ext, until, "events", "tool = ?", ("tdflow_undo",), 10)
    T("work.UNDO_AUDITED", any(e["kind"] == "outcome" and e["outcome"] == "ok" for e in ev))
    R["restart"] = last
    p = comp.par
    p.Enablemcp = False
    yield frames(5)
    drv = td.op("/project1/test_driver/drive")
    drv.par.active = False
    td.project.save()
    drv.par.active = True


def native_restart2(comp, ext, T, frames, until, checkpoint, R):
    """Launch 3 (restart 2, after a successful reconnection): the same checks, reconnect, then the harness ends
    TouchDesigner abruptly (no save, no clean shutdown)."""
    dialogs = _dialogs(ext)
    g = yield from _reopened(comp, ext, T, frames, checkpoint, "restart2")
    yield from _connect_and_pick(comp, ext, dialogs, frames, until, "restart2_plan")
    T("restart2.CONNECT_AGENT_KNOWS_IT_IS_SET_UP", (ext.connector.state or {}).get("intact") is True
      and dialogs[-1]["buttons"] == ["Enable MCP", "Cancel"], buttons=dialogs[-1]["buttons"])
    dialogs[-1]["cb"](1)                                     # Cancel: enable through the parameter instead
    yield frames(3)
    r, last = yield from _enable_and_reconnect(comp, ext, T, frames, until, checkpoint, "restart2", g, live=False)
    R["restart2"] = last
    yield checkpoint("restart2_kill", {"events": last["events"]}, seconds=120)   # the harness kills this process
    yield frames(600)


def native_recover(comp, ext, T, frames, until, checkpoint, R):
    """Launch 4: after the abrupt end. The project file is the one saved in launch 2; the audit log and credentials
    must have survived (durable writes, WAL), the agent reconnects. Then revocation, repair, a failed setup and
    Disconnect (rollback)."""
    p = comp.par
    home = os.environ["TDFLOW_CLIENT_HOME"]
    cfg = os.path.join(home, ".claude.json")
    dialogs = _dialogs(ext)
    g = yield from _reopened(comp, ext, T, frames, checkpoint, "recover")
    r, last = yield from _enable_and_reconnect(comp, ext, T, frames, until, checkpoint, "recover", g, live=False)
    aid = last["aid"]
    n_agents = last["agents"]
    ext.native.refresh_menu()
    p.Agentselect = aid
    yield frames(3)
    p.Revoketokens.pulse()
    yield until(lambda: not ext.native.busy and not [t for t in ext.identity.tokens.values()
                                                     if t.agent_id == aid and not t.revoked_at], 15, "revoke")
    yield checkpoint("restart_revoked", {})
    ext.connector.state = None
    p.Repairconnection.pulse()
    yield until(lambda: _stage(ext) in ("awaiting", "failed"), 30, "repair_plan")
    T("repair.PLAN_REUSES_THE_AGENT", "gets a new key" in dialogs[-1]["text"] and dialogs[-1]["buttons"][0] == "Approve",
      text=dialogs[-1]["text"][:300])
    dialogs[-1]["cb"](0)
    yield until(lambda: _stage(ext) in ("waiting", "connected", "failed"), 30, "repair_apply")
    T("repair.SAME_AGENT_NEW_CREDENTIAL", len(ext.identity.agents) == n_agents and [
        t for t in ext.identity.tokens.values() if t.agent_id == aid and not t.revoked_at])
    yield checkpoint("restart_repaired", {})
    # A failed, interrupted setup leaves nothing behind.
    good = _cfg(cfg)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("{ not valid json")
    ext.connector.state = None
    p.Repairconnection.pulse()
    yield until(lambda: _stage(ext) == "failed", 30, "failed_plan")
    T("failure.INVALID_CONFIG_FAILS_CLEANLY", p.Statusdetail.eval() == "Claude settings damaged"
      and _cfg(cfg) == b"{ not valid json" and len(ext.identity.agents) == n_agents, detail=p.Statusdetail.eval())
    with open(cfg, "wb") as f:
        f.write(good)
    # Disconnect = rollback: exactly our entry goes; whatever the client wrote meanwhile stays.
    pre = __import__("json").loads(_cfg(cfg).decode("utf-8"))
    ext.connector.state = None
    p.Disconnectagent.pulse()
    yield frames(2)
    T("disconnect.ASKS_FIRST", dialogs[-1]["buttons"] == ["Disconnect", "Cancel"])
    dialogs[-1]["cb"](0)
    yield until(lambda: "claude-code" not in ext.connector.records, 20, "disconnect")
    yield frames(3)
    rec_pid = ext.storage.project_id
    post = __import__("json").loads(_cfg(cfg).decode("utf-8"))
    pre["mcpServers"].pop("tdflow", None)
    T("disconnect.EXACTLY_THE_ENTRY_REMOVED", post == pre and "other-server" in post["mcpServers"])
    T("disconnect.CREDENTIAL_DELETED_TOKENS_REVOKED_AGENT_DISABLED",
      not os.path.exists(os.path.join(ext._private_root(), "connect", "claude-code", rec_pid + ".dpapi"))
      and not [t for t in ext.identity.tokens.values() if t.agent_id == aid and not t.revoked_at]
      and not ext.identity.agents[aid].enabled)
    ev = yield from _events(ext, until, "client_disconnect")
    T("disconnect.AUDITED", [e["outcome"] for e in reversed(ev)] == ["intent", "ok"])
    yield checkpoint("restart_disconnected", {})
    p.Enablemcp = False
    yield frames(5)


# ---- onboard: generated text -> live Claude Code session; Skills round-trip ----------------------
def onboard(comp, ext, T, frames, until, checkpoint, R):
    """Owner D22: Claude Code is set up automatically elsewhere (native suite); this suite keeps the manual fallback
    honest: Other MCP Client, configured only from the generated text plus the TDFLOW_TOKEN variable."""
    M.fixtures()
    comp.par.Codingagent = "other"
    yield frames(5)
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "onboard_enable")
    T("onboard.enabled", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())
    T("onboard.tellagent_has_connect_section", "## Connect Other MCP client (manual setup)" in ext.tell_text and
      "${TDFLOW_TOKEN}" in ext.tell_text)
    g = yield checkpoint("onboard", {"project_folder": str(td.project.folder)})
    T("onboard.claude_code_edit_applied", abs(opacity() - 0.25) < 1e-6, opacity=opacity())
    evs = yield from store_op(ext, until, "events", limit=200)
    mine = [e for e in evs if e["kind"] == "outcome" and e["tool"] == "tdflow_set_parameters" and e["outcome"] == "ok"]
    ci = mine[0].get("client_info") if mine else ""
    T("onboard.edit_audited_with_client_info", bool(mine) and "claude" in str(ci).lower(), client_info=ci)
    sk = [e for e in evs if e["tool"] in ("skills_install", "skills_uninstall")]
    T("onboard.skills_writes_audited", len(sk) >= 4, n=len(sk))
    R["onboard"] = g
    comp.par.Enablemcp = False
    yield frames(5)


# ---- dogfood: real Claude Code sessions doing realistic work (owner priority, 2026-11-22) ---------
def dogfood(comp, ext, T, frames, until, checkpoint, R):
    M.fixtures()
    comp.par.Codingagent = "other"                 # the harness configures its client from the manual text
    fx = td.op("/project1/fx")
    out = fx.create(td.nullTOP, "out")
    fx.op("level1").inputConnectors[0].connect(fx.op("noise1"))
    out.inputConnectors[0].connect(fx.op("level1"))
    fx.create(td.constantCHOP, "speed").par.value0 = 0.5
    yield frames(5)
    before = {"brightness": fx.op("level1").par.brightness1.eval(), "notes": fx.op("notes").text,
              "internals": _comp_digest(comp)}
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "dogfood_enable")
    T("dogfood.enabled", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())
    memory_pass = bool(os.environ.get("TDFLOW_DOGFOOD_MEMORY"))
    if memory_pass:                                # Phase 2A: the user creates project memory first
        td.op("/project1/mem").destroy()
        comp.par.Createprojectmemory.pulse()
        yield until(lambda: td.op("/project1/tdflowProject") is not None, 10, "dogfood_memory")
        T("dogfood.memory_created", td.op("/project1/tdflowProject") is not None)
        yield frames(10)
    g = yield checkpoint("dogfood", {"project_folder": str(td.project.folder)}, seconds=2700)
    if memory_pass:
        # Between the two agents' sessions the user edits a parameter in TouchDesigner (no audit event).
        fx.op("noise1").par.amp = 0.33
        yield frames(5)
        g = yield checkpoint("dogfood_resume", {"tell": comp.op("tellagent").text}, seconds=900)
        mc = td.op("/project1/tdflowProject")
        MEM = sys.modules["tdflowmcp.memory"]
        ents = MEM.read_records([(d.name, d.text) for d in mc.children if d.OPType == "textDAT"])
        R["dogfood_memory"] = [{"record": e["name"], "author": e["rec"]["author"] if e["rec"] else None,
                                "status": (e["rec"] or {}).get("body", {}).get("status"),
                                "claim_summary": (e["rec"] or {}).get("body", {}).get("claim_summary"),
                                "sealed": e["sealed"]} for e in ents]
    level, blur = fx.op("level1"), fx.op("blur1")
    R["dogfood_state"] = {
        "brightness_before": before["brightness"], "brightness_after": level.par.brightness1.eval(),
        "blur1": None if blur is None else {"type": blur.OPType,
                                            "inputs": [c.owner.path for i in blur.inputConnectors for c in i.connections],
                                            "out_inputs": [c.owner.path for i in out.inputConnectors for c in i.connections]},
        "notes_changed": fx.op("notes").text != before["notes"], "notes_len": len(fx.op("notes").text),
        "level_opacity_mode": str(level.par.opacity.mode), "exec_created": fx.op("exec2") is not None,
        "tdflowmcp_intact": _comp_digest(comp) == before["internals"],
    }
    evs = yield from store_op(ext, until, "events", limit=5000)
    by = {}
    for e in evs:
        if e["kind"] in ("outcome", "deny"):
            k = (e["tool"], e["kind"], e.get("outcome") or e.get("decision"), e.get("error_code"))
            by[str(k)] = by.get(str(k), 0) + 1
    R["dogfood_audit"] = {"events": len(evs), "by_tool": by,
                          "unaccounted": len(unmatched_intents(evs)),
                          "sessions": len((yield from store_op(ext, until, "sessions", limit=50)))}
    v = yield from store_op(ext, until, "verify")
    T("dogfood.audit_verifies", v["ok"], verify=v)
    T("dogfood.no_unaccounted_executions", R["dogfood_audit"]["unaccounted"] == 0)
    T("dogfood.tdflowmcp_internals_untouched", R["dogfood_state"]["tdflowmcp_intact"])
    T("dogfood.no_expression_set", "EXPRESSION" not in R["dogfood_state"]["level_opacity_mode"])
    T("dogfood.no_execute_dat_created", not R["dogfood_state"]["exec_created"])
    R["dogfood_harness"] = g
    R["frame_stats_onframe"] = ext.FrameStats()
    comp.par.Enablemcp = False
    yield frames(5)


# ---- Phase 2A: project memory, checkpoints, verified handoffs (ADR-0012/0013) ---------------------------
def _memory_comps():
    return [c for c in td.op("/project1").findChildren(tags=["tdflowProject"]) if c.isCOMP]


def _claim(comp, ext, until):
    """User action: Claim Project Memory, then wait until the claim event is durable."""
    def last_claim(evs):                         # the store returns events from the latest claim on
        return max([e["seq"] for e in evs if e["tool"] == "memory_claim"] or [0])
    s0 = last_claim((yield from store_op(ext, until, "memory_events")))
    comp.par.Claimprojectmemory.pulse()
    for _ in range(100):
        if last_claim((yield from store_op(ext, until, "memory_events"))) > s0:
            return True
    return False


def memory(comp, ext, T, frames, until, checkpoint, R):
    M.fixtures()                                   # includes /project1/mem: tagged tdflowProject, no Projectid
    yield frames(5)
    internals = _comp_digest(comp)
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "memory_enable")
    T("memory.enabled", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())
    yield checkpoint("mem_uninitialized", {})      # harness: tools refuse an uninitialised tdflowProject
    comp.par.Createprojectmemory.pulse()           # refused: a tdflowProject already exists
    yield frames(5)
    T("memory.create_refused_when_one_exists", len(_memory_comps()) == 1 and "already exists" in comp.par.Statusdetail.eval(),
      detail=comp.par.Statusdetail.eval())
    td.op("/project1/mem").destroy()               # the user removes the old one
    yield frames(3)
    yield checkpoint("mem_none", {})
    comp.par.Createprojectmemory.pulse()
    yield until(lambda: len(_memory_comps()) == 1, 10, "memory_created")
    mc = _memory_comps()[0]
    pid = str(mc.par.Projectid.eval())
    T("memory.created", mc.path == "/project1/tdflowProject" and pid.startswith("tfp_") and len(pid) == 20
      and mc.par.Projectid.readOnly and str(mc.par.Memoryauthority.eval()) == "embedded", path=mc.path, pid=pid)
    yield frames(10)
    evs = yield from store_op(ext, until, "events", "kind='admin' AND tool='memory_create'", (), 5)
    T("memory.create_audited", len(evs) == 1 and pid in (evs[0]["detail"] or ""))
    g = yield checkpoint("mem_ready", {"memory_id": pid})
    yield frames(10)
    tell = comp.op("tellagent").text
    T("memory.tell_has_resume_section", "## Resume earlier work" in tell and "1 task(s)" in tell
      and "tdflow_task_list" in tell, tell=tell[-600:])
    T("memory.tell_has_no_task_ids", "blur" not in tell)            # agent-chosen ids stay out (review 9)
    T("memory.tell_has_no_agent_text", "UNTRUSTED_MARKER" not in tell)
    T("memory.records_are_text_dats", sorted(d.name for d in mc.children if d.name != "README")
      == ["checkpoint_blur_r1", "handoff_blur_r2"], names=sorted(d.name for d in mc.children))
    # The user edits a parameter outside TDflowMCP (no audit event).
    td.op("/project1/fx/noise1").par.amp = 0.7
    yield frames(3)
    yield checkpoint("mem_out_of_band", {})
    # The user edits a record by hand: memory becomes read-only until Claim Project Memory.
    d = mc.op("checkpoint_blur_r1")
    d.text = d.text.replace("in_progress", "blocked")
    yield frames(3)
    yield checkpoint("mem_record_edited", {})
    T("memory.claim_after_edit_durable", (yield from _claim(comp, ext, until)))
    yield checkpoint("mem_claimed", {})
    # An older save / unsaved write: a record the audit log knows about disappears.
    newest = sorted(x.name for x in mc.children if x.name.startswith(("checkpoint_", "handoff_")))[-1]
    mc.op(newest).destroy()
    yield frames(3)
    yield checkpoint("mem_rewound", {"removed": newest})
    T("memory.claim_after_rewind_durable", (yield from _claim(comp, ext, until)))
    # A record copied from another project's memory: never accepted without a claim.
    MEM = sys.modules["tdflowmcp.memory"]
    rec = MEM.new_record("checkpoint", "copied", [], {"title": "x", "scopes": ["/project1/fx"]}, "agt_other", 0)
    f = mc.create(td.textDAT, rec["record_id"])
    f.text = MEM.record_text(rec)
    yield frames(3)
    yield checkpoint("mem_foreign", {})
    f.destroy()
    # Two tdflowProject components: single authority refused.
    extra = td.op("/project1").create(td.baseCOMP, "mem2")
    extra.tags.add("tdflowProject")
    yield frames(3)
    yield checkpoint("mem_ambiguous", {})
    extra.destroy()
    # Main-thread cost of a fingerprint over a larger scope (300 operators).
    big = td.op("/project1").create(td.baseCOMP, "load")
    for i in range(1999):                          # review 10, F5: the walk at its 2000-operator bound
        big.create(td.noiseTOP, f"n{i}")
    yield frames(10)
    ext.FrameStats(reset=True)
    g = yield checkpoint("mem_load", {})
    R["memory_load_onframe"] = ext.FrameStats()
    big.destroy()
    yield frames(5)
    evs = yield from store_op(ext, until, "events", limit=5000)
    v = yield from store_op(ext, until, "verify")
    T("memory.audit_verifies", v["ok"], verify=v)
    T("memory.no_unaccounted_executions", len(unmatched_intents(evs)) == 0)
    T("memory.tdflowmcp_internals_untouched", _comp_digest(comp) == internals)
    diag = ext.Diagnostics()
    T("memory.frame_no_errors", diag["frame_errors"] == 0, last=diag["last_frame_error"])
    mem_out = [e for e in evs if e["kind"] == "outcome" and e["tool"] in ("tdflow_task_checkpoint", "tdflow_handoff_create")]
    R["memory_audit"] = {"events": len(evs), "memory_write_outcomes": len(mem_out),
                         "ok": sum(1 for e in mem_out if e["outcome"] == "ok")}
    R["frame_stats_onframe"] = ext.FrameStats()
    comp.par.Enablemcp = False
    yield frames(5)


# ---- Phase 2A (D18): TouchDesigner restart persistence, identity verification, restoration ----------------
def _record_names(mc):
    return sorted(d.name for d in mc.children if d.name.startswith(("checkpoint_", "handoff_")))


def memory_persist(comp, ext, T, frames, until, checkpoint, R):
    """Launch 1: create memory, two records, SAVE the .toe, then one more record that is never saved."""
    M.fixtures()
    td.op("/project1/mem").destroy()
    yield frames(5)
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "persist_enable")
    comp.par.Createprojectmemory.pulse()
    yield until(lambda: len(_memory_comps()) == 1, 10, "persist_created")
    mc = _memory_comps()[0]
    pid = str(mc.par.Projectid.eval())
    yield frames(10)
    yield checkpoint("persist_write", {"memory_id": pid})          # harness: checkpoint r1 + handoff r2
    saved_names = _record_names(mc)
    toe = os.path.join(str(td.project.folder), str(td.project.name))
    before = os.path.getmtime(toe) if os.path.exists(toe) else 0
    drv = td.op("/project1/test_driver/drive")
    drv.par.active = False                    # the reopened project must not double-drive the runner
    td.project.save()                         # the user saves (same file)
    drv.par.active = True
    T("persist.saved", os.path.exists(toe) and os.path.getmtime(toe) > before and saved_names ==
      ["checkpoint_persist_r1", "handoff_persist_r2"], names=saved_names)
    yield frames(5)
    yield checkpoint("persist_unsaved", {})                          # harness: checkpoint r3, never saved
    R["persist"] = {"memory_id": pid, "saved": saved_names, "after_unsaved_write": _record_names(mc), "toe": toe}
    v = yield from store_op(ext, until, "verify")
    T("persist.audit_verifies", v["ok"], verify=v)
    comp.par.Enablemcp = False
    yield frames(5)


def memory_restart(comp, ext, T, frames, until, checkpoint, R):
    """Launch 2: the saved .toe reopened in a new TouchDesigner process."""
    T("restart.one_component_from_saved_toe", comp is not None and
      len(td.op("/").findChildren(tags=["TDflowMCP"])) == 1 and not comp.par.Enablemcp.eval(),
      enable=comp.par.Enablemcp.eval())
    mcs = _memory_comps()
    mc = mcs[0] if len(mcs) == 1 else None
    names = _record_names(mc) if mc else []
    pid = str(mc.par.Projectid.eval()) if mc else None
    T("restart.memory_component_restored", mc is not None and mc.par.Projectid.readOnly
      and "tdflowProject" in mc.tags, pid=pid)
    T("restart.saved_records_restored", names == ["checkpoint_persist_r1", "handoff_persist_r2"], names=names)
    comp.par.Opensetup.pulse()
    yield until(lambda: ext.worker is not None and ext.worker.ready.is_set(), 20, "restart_open")
    v = (ext.worker.open_info or {}).get("verify") or {}
    T("restart.same_project_identity", ext.storage is not None and ext.storage.status == "OK" and v.get("ok") is True,
      status=ext.storage and ext.storage.status, verify=v)
    yield from enable(comp, until, "restart_enable")
    yield frames(10)
    tell = comp.op("tellagent").text
    T("restart.tell_resume_section", "## Resume earlier work" in tell and "1 task(s)" in tell, tell=tell[-500:])
    yield checkpoint("restart_ready", {"memory_id": pid, "records": names})
    T("restart.claim_durable", (yield from _claim(comp, ext, until)))
    yield checkpoint("restart_claimed", {})
    # Identity: the tdflowProject is replaced by one with another Projectid (pasted from elsewhere / edited).
    p = mc.par.Projectid
    p.readOnly = False
    p.val = "tfp_" + "a" * 16
    p.readOnly = True
    yield frames(3)
    yield checkpoint("restart_identity", {})
    T("restart.claim_identity_durable", (yield from _claim(comp, ext, until)))
    yield checkpoint("restart_identity_claimed", {})
    # Review 10, F6: identity alone (no records at all): only the Projectid differs from the claimed one.
    for d in [x for x in mc.children if x.name.startswith(("checkpoint_", "handoff_"))]:
        d.destroy()
    T("restart.claim_empty_durable", (yield from _claim(comp, ext, until)))
    p.readOnly = False
    p.val = "tfp_" + "b" * 16
    p.readOnly = True
    yield frames(3)
    yield checkpoint("restart_identity_only", {})
    evs = yield from store_op(ext, until, "events", limit=5000)
    v = yield from store_op(ext, until, "verify")
    T("restart.audit_verifies", v["ok"], verify=v)
    T("restart.no_unaccounted_executions", len(unmatched_intents(evs)) == 0)
    R["restart"] = {"records": _record_names(mc), "events": len(evs)}
    R["frame_stats_onframe"] = ext.FrameStats()
    comp.par.Enablemcp = False
    yield frames(5)


# ---- Phase 2A increment 3: repository-mode memory (ADR-0013 gates) and the dogfood friction fixes -------------
def _files(folder):
    d = os.path.join(folder, "tdflow", "memory")
    out = {}
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                out[fn[:-5]] = f.read()
    return out


def _wait_note(comp, until, text, seconds=15):
    return until(lambda: text in comp.par.Statusdetail.eval(), seconds, "note_" + text[:12].replace(" ", "_"))


def memory_repo(comp, ext, T, frames, until, checkpoint, R):
    """Embedded records -> user-confirmed migration into a folder of the project -> agents work on files ->
    a file edited outside (Git) + claim -> a Projectid mismatch -> migration back into the component."""
    import json
    MEM = sys.modules["tdflowmcp.memory"]
    M.fixtures()
    td.op("/project1/mem").destroy()
    yield frames(5)
    internals = _comp_digest(comp)
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "repo_enable")
    comp.par.Createprojectmemory.pulse()
    yield until(lambda: len(_memory_comps()) == 1, 10, "repo_created")
    mc = _memory_comps()[0]
    pid = str(mc.par.Projectid.eval())
    rp = getattr(mc.par, "Repopath", None)
    T("repo.new_component_has_unset_readonly_repopath", rp is not None and rp.readOnly and rp.eval() == ""
      and str(mc.par.Memoryauthority.eval()) == "embedded")
    yield frames(10)
    folder = str(td.project.folder)
    repo = os.path.join(folder, "memrepo")
    os.makedirs(repo)                                   # the user's folder inside the project folder
    other = os.path.join(folder, "otherrepo")           # a folder that belongs to another project's memory
    os.makedirs(other)
    with open(os.path.join(other, "tdflow-project.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "tdflow-project/1", "project_id": "tfp_" + "9" * 16, "authority": "repo"}, f)

    # 1. Records in embedded mode, then the harness previews and confirms the migration on the admin API.
    g = yield checkpoint("repo_embedded", {"memory_id": pid, "project_folder": folder})
    embedded = {d.name: MEM.text_sha(d.text) for d in mc.children if MEM.RECORD_NAME.match(d.name)}
    T("repo.embedded_records_written", sorted(embedded) == ["checkpoint_repo_r1", "handoff_repo_r2"], names=sorted(embedded))
    g = yield checkpoint("repo_migrate", {"memory_id": pid, "project_folder": folder})
    yield frames(10)
    files = _files(repo)
    T("repo.MIGRATION_TO_REPO_PRESERVES_RECORDS_AND_HASHES",
      {n: MEM.text_sha(t) for n, t in files.items()} == embedded, files=sorted(files))
    pj = json.load(open(os.path.join(repo, "tdflow-project.json"), encoding="utf-8"))
    T("repo.project_file_links_this_memory", pj.get("project_id") == pid and pj.get("authority") == "repo", pj=pj)
    T("repo.component_is_a_pointer", str(mc.par.Memoryauthority.eval()) == "repo" and mc.par.Repopath.eval() == "memrepo"
      and mc.par.Repopath.readOnly and mc.par.Memoryauthority.readOnly
      and not [d.name for d in mc.children if MEM.RECORD_NAME.match(d.name)], children=sorted(d.name for d in mc.children))
    idx = mc.op("derived_index")
    T("repo.derived_index_counts_only", idx is not None and MEM.derived_counts(idx.text) == (1, 1)
      and "DERIVED" in idx.text and "repo" not in json.loads(idx.text).get("note", "").lower().replace("derived", ""),
      text=idx.text if idx is not None else None)
    T("repo.migration_noted_in_both_homes", mc.op("MIGRATIONS") is not None and '"to": "repo"' in mc.op("MIGRATIONS").text
      and os.path.exists(os.path.join(repo, "tdflow", "memory", "MIGRATIONS.jsonl")))
    tell = comp.op("tellagent").text
    T("repo.tell_resume_from_derived_index", "## Resume earlier work" in tell and "1 task(s), 1 not marked done" in tell
      and os.path.basename(folder.replace("\\", "/").rstrip("/")) not in tell, tell=tell[-400:])
    evs = yield from store_op(ext, until, "events", "kind='admin' AND tool='memory_migrate'", (), 10)
    T("repo.migration_audited_before_and_after", sorted(e["outcome"] for e in evs) == ["ok", "started"],
      outcomes=[e["outcome"] for e in evs])

    # 2. Agents work on files: the dedicated permission is needed to write; reading is unchanged.
    g = yield checkpoint("repo_mode", {"memory_id": pid, "project_folder": folder})
    yield frames(10)
    files = _files(repo)
    T("repo.agent_record_is_a_file", sorted(files) == ["checkpoint_repo_r1", "checkpoint_repo_r3", "handoff_repo_r2"]
      and not [d.name for d in mc.children if MEM.RECORD_NAME.match(d.name)], files=sorted(files))
    T("repo.derived_index_follows_writes", MEM.derived_counts(mc.op("derived_index").text) == (1, 1))

    # 3. A file changes outside TDflowMCP (a Git checkout or pull): read-only until the user's claim.
    p = os.path.join(repo, "tdflow", "memory", "checkpoint_repo_r1.json")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("in_progress", "blocked"))
    yield checkpoint("repo_edited", {})
    T("repo.claim_after_outside_edit_durable", (yield from _claim(comp, ext, until)))
    yield frames(5)
    yield checkpoint("repo_claimed", {})

    # 4. ADR-0013 gate: a folder whose tdflow-project.json names another Projectid blocks memory entirely.
    pj_path = os.path.join(repo, "tdflow-project.json")
    with open(pj_path, encoding="utf-8") as f:
        good = f.read()
    with open(pj_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(good.replace(pid, "tfp_" + "8" * 16))
    yield checkpoint("repo_mismatch", {})
    comp.par.Claimprojectmemory.pulse()                  # a mismatch is never claimable
    yield _wait_note(comp, until, "was not claimed")
    T("repo.MISMATCH_NOT_CLAIMABLE", "MEMORY_LINK_MISMATCH" in comp.par.Statusdetail.eval(), detail=comp.par.Statusdetail.eval())
    with open(pj_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(good)
    yield frames(5)

    # 4b. Review 11: the link is lost (tdflow-project.json deleted, e.g. by `git clean`). Memory is unavailable,
    # and the user links the same folder again from the Setup page; no record is touched.
    os.remove(pj_path)
    before_relink = {n: MEM.text_sha(t) for n, t in _files(repo).items()}
    yield checkpoint("repo_unlinked", {})
    yield frames(10)
    pj2 = json.load(open(pj_path, encoding="utf-8")) if os.path.exists(pj_path) else {}
    T("repo.RELINK_RESTORES_THE_LINK_WITHOUT_TOUCHING_RECORDS", pj2.get("project_id") == pid and pj2.get("authority") == "repo"
      and {n: MEM.text_sha(t) for n, t in _files(repo).items()} == before_relink
      and mc.par.Repopath.eval() == "memrepo" and '"relink": true' in mc.op("MIGRATIONS").text, pj=pj2)

    # 5. Back into the component. Every record, including those written as files, arrives with the same hash.
    before = {n: MEM.text_sha(t) for n, t in _files(repo).items()}
    g = yield checkpoint("repo_back", {"memory_id": pid, "project_folder": folder})
    yield frames(10)
    now = {d.name: MEM.text_sha(d.text) for d in mc.children if MEM.RECORD_NAME.match(d.name)}
    T("repo.MIGRATION_BACK_PRESERVES_RECORDS_AND_HASHES", {k: now.get(k) for k in before} == before and len(before) == 4,
      before=sorted(before), now=sorted(now))
    T("repo.embedded_again", str(mc.par.Memoryauthority.eval()) == "embedded" and mc.par.Repopath.eval() == ""
      and mc.op("derived_index") is None and '"to": "embedded"' in mc.op("MIGRATIONS").text)
    pj = json.load(open(pj_path, encoding="utf-8"))
    T("repo.folder_marked_derived", pj.get("authority") == "embedded" and "DERIVED" in pj.get("note", "")
      and sorted(_files(repo)) == sorted(before), pj=pj)
    T("repo.write_after_return_is_a_dat", "checkpoint_repo_r5" in now and "checkpoint_repo_r5" not in _files(repo), now=sorted(now))
    # Dogfood friction: the user changes one parameter outside TDflowMCP; resume names it.
    td.op("/project1/fx/noise1").par.amp = 0.61
    yield frames(3)
    yield checkpoint("repo_drift", {})
    yield frames(10)
    tell = comp.op("tellagent").text
    T("repo.tell_resume_after_return", "1 task(s), 0 not marked done" in tell, tell=tell[-300:])
    evs = yield from store_op(ext, until, "events", limit=5000)
    v = yield from store_op(ext, until, "verify")
    T("repo.audit_verifies", v["ok"], verify=v)
    T("repo.no_unaccounted_executions", len(unmatched_intents(evs)) == 0)
    T("repo.tdflowmcp_internals_untouched", _comp_digest(comp) == internals)
    diag = ext.Diagnostics()
    T("repo.frame_no_errors", diag["frame_errors"] == 0, last=diag["last_frame_error"])
    T("repo.nothing_written_outside_the_linked_folders", sorted(os.listdir(other)) == ["tdflow-project.json"]
      and not os.path.exists(os.path.join(folder, "tdflow")), other=sorted(os.listdir(other)))
    # Dogfood friction (increment 3): default placement of a created operator, checked from inside TD.
    blur = td.op("/project1/fx/auto_placed")
    sibs = [c for c in td.op("/project1/fx").children if c.name != "auto_placed"]
    T("friction.default_placement_right_of_network", blur is not None and bool(sibs)
      and all(blur.nodeX >= c.nodeX + c.nodeWidth for c in sibs), x=blur.nodeX if blur is not None else None)
    R["repo"] = {"records": sorted(now), "memory_id": pid}
    R["frame_stats_onframe"] = ext.FrameStats()
    comp.par.Enablemcp = False
    yield frames(5)
