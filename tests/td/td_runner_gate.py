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
        if o.family == "DAT" and o.name not in ("tellagent",):
            h.update(o.text.encode("utf-8", "replace"))
    for p in comp.customPars:
        if p.readOnly or p.name in ("Enablemcp", "Suspendagents"):   # status values and user toggles
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
    if mode == "perf":
        yield from perf(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate":
        yield from gate(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate_crash":
        yield from crash(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "gate_restart":
        yield from restart(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "onboard":
        yield from onboard(comp, ext, T, frames, until, checkpoint, R)
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
    T("move.mcp_refused_until_confirmed", not comp.par.Enablemcp.eval() and comp.par.Status.eval() != "Enabled",
      detail=comp.par.Statusdetail.eval())
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


# ---- onboard: generated text -> live Claude Code session; Skills round-trip ----------------------
def onboard(comp, ext, T, frames, until, checkpoint, R):
    M.fixtures()
    yield frames(5)
    yield from open_and_pair(comp, ext, T, frames, until, checkpoint)
    yield from enable(comp, until, "onboard_enable")
    T("onboard.enabled", comp.par.Status.eval() == "Enabled", detail=comp.par.Statusdetail.eval())
    T("onboard.tellagent_has_connect_section", "## Connect your coding agent" in ext.tell_text and
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
    g = yield checkpoint("dogfood", {"project_folder": str(td.project.folder)}, seconds=2700)
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
