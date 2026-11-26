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


def _script_operator_probe(T, frames):
    """Owner-side probe in this disposable project, with no operator allowlist changes."""
    scratch = td.op("/project1").create(td.baseCOMP, "script_probe")
    try:
        scratch.allowCooking = False
        samples = [scratch.create(cls) for cls in (td.scriptCHOP, td.scriptSOP, td.scriptTOP, td.scriptDAT)]
        before = {op.OPType: int(op.totalCooks) for op in samples}
        yield frames(3)
        blocked = {op.OPType: int(op.totalCooks) for op in samples}
        callback_docked = {op.OPType: any(d.isDAT for d in op.docked) for op in samples}
        scratch.allowCooking = True
        dat = samples[-1]
        before_read = int(dat.totalCooks)
        dat.text
        after_read = int(dat.totalCooks)
        top = samples[2]
        before_top_read = int(top.totalCooks)
        _ = top.width, top.height
        after_top_metadata = int(top.totalCooks)
        top.saveByteArray(".png")
        after_top_image = int(top.totalCooks)
        for o in samples[:3]:
            o.cook(force=True)
        after = {op.OPType: int(op.totalCooks) for op in samples}
        T("v11.SCRIPT_OPERATOR_STAGING_PROBE", all(callback_docked.values()) and all(after[o.OPType] > blocked[o.OPType]
          for o in samples[:3]), before=before, blocked=blocked, docked=callback_docked, after=after,
          script_dat_text_read_cooks=after_read - before_read,
          script_top_metadata_read_cooks=after_top_metadata - before_top_read,
          script_top_image_read_cooks=after_top_image - after_top_metadata)
    finally:
        scratch.destroy()


def _caps_native(comp, ext, T, frames, until, checkpoint, g):
    """Owner D25: a role changed on the dashboard's Capabilities page shows on TouchDesigner's Agents page at once, and
    a scope added there shows on the page (checked by the harness at 'caps_native'). One backend, no second copy."""
    p = comp.par
    aid = (g or {}).get("cap_agent")
    T("caps.harness_changed_an_agent", bool(aid), g=g)
    if not aid:
        return
    ext.native.refresh_menu()
    p.Agentselect = aid
    yield until(lambda: "Read Only" in p.Agentinfo.eval(), 10, "caps_native_role")
    T("caps.NATIVE_SHOWS_THE_WEB_ROLE_CHANGE", ext.identity.agents[aid].role == "observer"
      and "Read Only" in p.Agentinfo.eval(), info=p.Agentinfo.eval())
    p.Agentscope = "/project1/zz_native/**"
    ext.native.scope(True)
    yield until(lambda: not ext.native.busy and any(gr.scope == "/project1/zz_native/**"
                                                    for gr in ext.identity.agents[aid].grants), 15, "caps_native_scope")
    T("caps.native_scope_added", any(gr.scope == "/project1/zz_native/**" for gr in ext.identity.agents[aid].grants))
    yield checkpoint("caps_native", {"agent": aid})
    yield until(lambda: ext.identity.agents[aid].role == "builder", 10, "caps_native_restored")
    yield until(lambda: "Builder" in p.Agentinfo.eval(), 10, "caps_native_restored_info")
    T("caps.NATIVE_SHOWS_THE_RESTORE", "Builder" in p.Agentinfo.eval(), info=p.Agentinfo.eval())


def _native_flow(comp, ext, T, frames, until, checkpoint, calls):
    """UX assignment: the TouchDesigner-only workflow (no browser). Connect Agent creates the agent; Copy Token is the
    only way the token leaves TDflowMCP (the harness reads it from the clipboard, as a user would paste it); the
    Agents page rotates, disables and re-enables it; an approval is decided natively; Open Dashboard opens a
    page that pairs through a TouchDesigner dialog (owner D22: no secret in the launched URL).
    Cursor is selected: it has no automatic setup, so this is the manual fallback (Copy Token). (Codex was used
    here until it got automatic setup on Windows, ADR-0017.)"""
    p = comp.par
    p.Codingagent = "cursor"
    p.Agentaccess = "observer"
    p.Allowedscope = "/project1/fx/**"
    yield frames(3)
    T("native.instructions_for_the_selected_client", "Connect Cursor (manual setup)" in ext.tell_text
      and "Codex" not in ext.tell_text and "Copy Token" in ext.tell_text)
    p.Connectagent.pulse()
    yield until(lambda: ext.native.handoff is not None, 20, "native_handoff")
    yield frames(2)
    T("native.AGENT_CREATED_IN_TOUCHDESIGNER", ext.native.handoff is not None and "Copy Token" in p.Statusdetail.eval(),
      detail=p.Statusdetail.eval())
    p.Copytoken.pulse()
    yield frames(3)
    tok = td.ui.clipboard
    leak = [x.name for x in comp.customPars if tok and tok in str(x.eval())] + \
        [o.path for o in comp.findChildren(type=td.DAT) if tok and tok in o.text]
    T("native.TOKEN_ONLY_ON_THE_CLIPBOARD", tok.startswith("tdf_") and not leak, leak=leak)
    g = yield checkpoint("native_token", {})
    yield frames(5)
    T("native.CONNECTED_AGENTS_SHOWS_A_REAL_CONNECTION", "none" not in p.Connectedagents.eval(),
      connected=p.Connectedagents.eval(), detail=p.Statusdetail.eval())
    ext.native._clip = (ext.native._clip[0], 0.0)                # the clipboard is cleared after a minute
    yield frames(3)
    T("native.clipboard_cleared_after_a_minute", td.ui.clipboard != tok)
    aid = g.get("agent_id")
    ext.native.refresh_menu()
    p.Agentselect = aid
    yield frames(3)
    T("native.AGENTS_PAGE_SHOWS_THE_AGENT", "Read Only" in p.Agentinfo.eval() and p.Agentscope.eval() == "/project1/fx/**",
      info=p.Agentinfo.eval(), scope=p.Agentscope.eval())
    p.Agentscope = "/project1/out1"
    p.Addscope.pulse()
    yield until(lambda: not ext.native.busy and any(gr.scope == "/project1/out1" for gr in ext.identity.agents[aid].grants),
                15, "native_scope")
    T("native.SCOPE_ADDED", any(gr.scope == "/project1/out1" for gr in ext.identity.agents[aid].grants))
    p.Removescope.pulse()
    yield until(lambda: not ext.native.busy and not any(gr.scope == "/project1/out1"
                                                        for gr in ext.identity.agents[aid].grants), 15, "native_unscope")
    T("native.SCOPE_REMOVED", not any(gr.scope == "/project1/out1" for gr in ext.identity.agents[aid].grants))
    p.Rotatetoken.pulse()
    yield until(lambda: ext.native.handoff is not None and not ext.native.busy and ext.native.handoff["token"] != tok,
                15, "native_rotate")
    p.Copytoken.pulse()
    yield frames(3)
    yield checkpoint("native_rotated", {})
    p.Disableagent.pulse()
    yield until(lambda: not ext.identity.agents[aid].enabled, 15, "native_disable")
    yield checkpoint("native_disabled", {})
    p.Enableagent.pulse()
    yield until(lambda: ext.identity.agents[aid].enabled, 15, "native_enable")
    T("native.ENABLE_DISABLE", ext.identity.agents[aid].enabled)
    g = yield checkpoint("native_approval_requested", {})
    yield until(lambda: bool(ext.native.approvals), 15, "native_approvals")
    yield frames(2)
    T("native.PENDING_APPROVAL_SHOWN", "waiting" in p.Approvalsummary.eval(), summary=p.Approvalsummary.eval())
    p.Viewapproval.pulse()
    yield frames(2)
    T("native.APPROVAL_SHOWS_COMPLETE_ARGUMENTS", g.get("expr", "?") in comp.op("approval_view").text)
    p.Approverequest.pulse()
    yield until(lambda: not ext.native.busy and comp.op("approval_view").text == "", 15, "native_decided")
    g = yield checkpoint("native_approved", {})
    n0 = len(calls)
    dialogs = []
    real_opener = ext.connector.dialog_opener
    ext.connector.dialog_opener = lambda title, text, buttons, cb: (dialogs.append((title, text, buttons, cb)), True)[1]
    p.Opendashboard.pulse()
    yield until(lambda: len(calls) > n0, 15, "dashboard_launch")
    url = calls[-1]
    T("web.DASHBOARD_URL_CARRIES_NO_SECRET", url == "http://127.0.0.1:9480/ui/#pair", url=url)
    g = yield checkpoint("web_pair_request", {})          # the page asks to pair, as app.js does on #pair
    yield frames(3)
    code = g.get("match_code") or "?"
    shown = dialogs[-1] if dialogs else ("", "", [], None)
    # Review 19: number matching: the dialog offers the real code among two decoys; the user picks the page's code.
    # Owner D25: the codes are numbered lines of the text (TouchDesigner's 260 px dialog clipped them as buttons); the
    # buttons carry the numbers.
    import re as _re
    listed = [(int(mm.group(1)), mm.group(2)) for mm in
              (_re.match(r"^\s*Button (\d):\s+([A-Z0-9]{3}-[A-Z0-9]{3})$", ln) for ln in str(shown[1]).splitlines()) if mm]
    pick = next((n for n, c in listed if c == code), None)
    T("web.SIGN_IN_DIALOG_MATCHES_THE_CODE", shown[2] == ["1", "2", "3", "Deny"] and len(listed) == 3
      and [n for n, _ in listed] == [1, 2, 3] and len({c for _, c in listed}) == 3 and pick is not None,
      buttons=shown[2], listed=listed)
    T("web.SIGN_IN_BUTTONS_ARE_SHORT", all(len(b) <= 4 for b in shown[2]), buttons=shown[2])
    if shown[3] and pick is not None:
        shown[3](pick - 1)                                    # the user picks the number of the page's code
    yield until(lambda: ext.admin.active_sessions() > 0, 10, "web_pair_approved")
    yield frames(3)
    yield checkpoint("web_paired", {})
    T("web.browser_session_shown", "signed in" in p.Websession.eval(), session=p.Websession.eval())
    n = len(dialogs)
    yield checkpoint("web_pair_untriggered", {})          # a request nobody launched from TouchDesigner
    yield frames(3)
    T("web.NO_DIALOG_WITHOUT_A_LAUNCH", len(dialogs) == n and "Pending" in p.Pairingrequest.eval(),
      pending=p.Pairingrequest.eval())
    p.Rejectpairing.pulse()
    yield frames(3)
    ext.connector.dialog_opener = real_opener


def run(comp, ext, T, frames, until, checkpoint, R, restart=False):
    calls = []
    ext.launcher = lambda url: (calls.append(url), True)[1]
    if restart:
        yield from _restart(comp, ext, T, frames, until, checkpoint, R)
        return
    fx = fixtures()
    network = td.op("/project1/fx")
    snapshot = network.create(td.constantTOP, "capture_snapshot")
    snapshot.cook(force=True)
    snapshot.lock = True
    scripted = network.create(td.scriptDAT, "script_output_probe")
    wired = network.create(td.nullDAT, "wired_source_probe")
    wired.inputConnectors[0].connect(scripted.outputConnectors[0])
    network.create(td.textDAT, "file_source_probe").par.file.expr = "''"  # no file opened by this fixture
    yield frames(5)
    # Enable MCP before any agent exists: refused with a reason, toggle reverts.
    comp.par.Enablemcp = True
    yield until(lambda: comp.par.Status.eval() == "Setup" and not comp.par.Enablemcp.eval(), 15, "enable_refused")
    yield frames(10)
    T("enable.refused_without_agent", not comp.par.Enablemcp.eval()
      and comp.par.Statusdetail.eval() == "No agent: Connect Agent" and ext.mcp_on is False,
      detail=comp.par.Statusdetail.eval(), status=comp.par.Status.eval())
    # Owner D25: Run Diagnostics and Enable MCP are the first two actionable controls of the main page; Suspend Agents
    # follows at once, and the connection controls come after them.
    page = next(pg for pg in comp.customPages if pg.name == "TDflowMCP")
    acts = [q.name for q in page.pars if not q.readOnly]
    T("order.run_diagnostics_then_enable_mcp", acts[:3] == ["Rundiagnostics", "Enablemcp", "Suspendagents"]
      and acts.index("Enablemcp") < min(acts.index(n) for n in ("Codingagent", "Agentaccess", "Connectagent")),
      actionable=acts)
    T("order.refusal_fits_the_window", len(comp.par.Statusdetail.eval()) <= 26)
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
    # The status lines refresh up to 4 times a second (UX throttle), so the count may lag a quarter second.
    yield until(lambda: comp.par.Agentsummary.eval().startswith(("5 agent", "6 agent")), 5, "agents_summary")
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
    g = yield checkpoint("resumed", {})
    yield from _caps_native(comp, ext, T, frames, until, checkpoint, g)
    yield from _native_flow(comp, ext, T, frames, until, checkpoint, calls)
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
    yield checkpoint("scan", {"params": params, "tell": ext.tell_text + ext._refresh_prompt(),
                              "tellagent_dat": comp.op("tellagent").text,
                              "events": events, "token_rows": [list(r) for r in tokens_rows], "diagnostics": diag})
    # Revocation is immediate (harness revoked one token); disabling MCP closes /mcp.
    yield checkpoint("revoked", {})
    probe = network.create(td.baseCOMP, "top_read_probe")
    probe.allowCooking = False
    source = probe.create(td.scriptTOP, "uncooked_script")
    output = probe.create(td.nullTOP, "unlocked_output")
    source.viewer = output.viewer = False
    output.inputConnectors[0].connect(source.outputConnectors[0])
    callback = next(o for o in source.docked if o.isDAT)
    callback.text = ('def onCook(scriptOp):\n'
                     '    scriptOp.store("top_read_callback_runs", scriptOp.fetch("top_read_callback_runs", 0) + 1)\n')

    def read_state():
        return [source.totalCooks, output.totalCooks, source.fetch("top_read_callback_runs", 0)]

    def setup_cooks_reported():
        source_cooks, _, runs = read_state()
        return source_cooks == runs and runs > 0

    tdops = ext._m.tdops

    def check_reads(label, expected, *, eligible=False):
        resolutions = []
        hints = tdops.INS.hints

        def observe_hints(rows):
            resolutions.extend((row["path"], row["resolution"]) for row in rows if "resolution" in row)
            return hints(rows)

        try:
            tdops.INS.hints = observe_hints
            yield checkpoint(label + "_top_reads", {"path": probe.path})
        finally:
            tdops.INS.hints = hints
        after = read_state()
        T(f"v11.{label}_top_reads_passive", after == expected, before=expected, after=after)
        T(f"v11.{label}_top_resolution", [path for path, _ in resolutions] == ([output.path] if eligible else []),
          resolutions=resolutions)

    probe.allowCooking = True
    yield from check_reads("unlocked", [0, 0, 0])
    output.cook(force=True)
    output.lock = True
    T("v11.TOP_CALLBACK_COUNTER_POSITIVE_CONTROL", read_state()[2] > 0, counts=read_state())
    callback.text += "\n# Update the upstream source after caching the output.\n"
    # The fixture runs one callback per cook. Wait for TD to report all known setup cooks.
    yield frames(3)
    yield until(setup_cooks_reported, 10, "top_read_setup_cooks_reported")
    before = read_state()
    yield from check_reads("locked", before, eligible=True)
    verified_build = tdops.VERIFIED_TOP_READ_BUILD
    try:
        tdops.VERIFIED_TOP_READ_BUILD = "0.0.0"
        yield from check_reads("unverified", read_state())
    finally:
        tdops.VERIFIED_TOP_READ_BUILD = verified_build
    output.bypass = True
    yield from check_reads("bypassed", read_state())
    output.bypass = False
    output.lock = True
    probe.bypass = True
    T("v11.INHERITED_BYPASS_WITH_LOCK_HELD", output.lock and probe.bypass and not output.bypass)
    yield from check_reads("ancestor_bypassed", read_state())
    probe.destroy()
    comp.par.Enablemcp = False
    yield frames(5)
    T("disable.status", comp.par.Status.eval() == "Setup", status=comp.par.Status.eval())
    yield checkpoint("disabled", {})
    R["datadir_files"] = sorted(os.listdir(ext.worker.datadir))
    R["frame_stats_onframe"] = ext.FrameStats()
    # Owner-side output and Script operator checks run after the minimized-window regression.
    source = td.op("/project1/fx/dev_color")
    level = td.op("/project1/fx/dev_level")
    output = td.op("/project1/fx/dev_out")
    expected = float(source.par.colorr.val) * 0.25 if source is not None else None
    if output is not None:
        output.cook(force=True)
    T("v11.PROCEDURAL_TOP_OUTPUT_AND_EXPRESSION", expected is not None and level is not None and output is not None
      and output.totalCooks > 0 and not output.errors() and abs(float(level.par.opacity.eval()) - expected) < 1e-6)
    yield from _script_operator_probe(T, frames)


def _intent_pairs(events):
    """Every executed call has an intent before its outcome (same request id)."""
    by = {}
    for e in sorted(events, key=lambda x: x["seq"]):
        if e["kind"] in ("intent", "outcome") and e.get("request_id"):
            by.setdefault((e["request_id"], e["tool"]), []).append(e["kind"])
    return bool(by) and all(v[0] == "intent" and "outcome" in v for v in by.values())


def _restart(comp, ext, T, frames, until, checkpoint, R):
    # UX assignment: state recovery after TouchDesigner restarts from the saved project.
    p = comp.par
    T("restart.UX_MCP_STAYS_OFF_AND_CHECKS_RAN", not p.Enablemcp.eval() and p.Status.eval() == "Off"
      and p.Compatsummary.eval().startswith(("Ready", "Partly", "Not ready")), summary=p.Compatsummary.eval())
    T("restart.UX_NOT_CONNECTED_YET", "none" in p.Connectedagents.eval(), connected=p.Connectedagents.eval())
    comp.par.Enablemcp = True
    yield until(lambda: comp.par.Status.eval() == "Enabled", 15, "enable_after_restart")
    T("restart.enabled_with_persisted_agents", comp.par.Status.eval() == "Enabled",
      status=comp.par.Status.eval(), detail=comp.par.Statusdetail.eval())
    v = yield from store_op(ext, until, "verify")
    T("restart.audit_chain_ok", v["ok"] and v["checkpoints"] >= 1, verify=v)
    yield frames(3)
    labels = list(p.Agentselect.menuLabels)
    T("restart.UX_AGENT_CREATED_IN_TD_PERSISTS", any("Cursor (Read Only)" in x for x in labels), labels=labels)
    yield checkpoint("restart_enabled", {"pid": os.getpid()})
    comp.par.Enablemcp = False
    yield frames(5)
