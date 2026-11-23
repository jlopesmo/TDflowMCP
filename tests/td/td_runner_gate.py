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
    elif mode == "memory":
        yield from memory(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "memory_persist":
        yield from memory_persist(comp, ext, T, frames, until, checkpoint, R)
    elif mode == "memory_restart":
        yield from memory_restart(comp, ext, T, frames, until, checkpoint, R)
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
