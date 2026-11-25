"""In-TouchDesigner integration test runner (spike S3; ADR-0007 tests/td). Runs INSIDE a disposable project.

Loads the built TDflowMCP.tox into /project1 and drives it only through its public surface (custom
parameters) plus two test seams on the extension (``launcher``, ``clipboard_writer``) and one
deliberate fault (``_build_status``). The external harness (run_td_tests.py) performs every network
probe at named checkpoints: the runner writes cp_<name>.json, the harness answers go_<name>.json.

Results: $TDFLOW_RUN_DIR/td_results.json. The runner never opens or saves any other project.
"""
import hashlib
import json
import os
import threading
import time
import traceback

import td

D = os.environ.get("TDFLOW_RUN_DIR", ".")
TOX = os.environ.get("TDFLOW_TOX", "")
ROOT = os.environ.get("TDFLOW_SRC_ROOT", "")
MODE = os.environ.get("TDFLOW_TEST_MODE", "full")
MAIN = threading.main_thread()
EXPECTED_PARS = ["Version", "Compatsummary", "Rundiagnostics", "Lastcheck", "Enablemcp", "Suspendagents", "Status",
                 "Statusdetail", "Codingagent", "Agentaccess", "Allowedscope", "Connectagent", "Tellagent",
                 "Copytellagent", "Viewinstructions", "Connectedagents", "Manageagents", "Opendashboard",
                 "Detectedclients", "Agentsummary", "Agentselect", "Agentinfo", "Viewagents", "Agentscope",
                 "Addscope", "Removescope", "Enableagent", "Disableagent", "Repairconnection", "Disconnectagent",
                 "Rotatetoken", "Copytoken", "Revoketokens", "Approvalsummary", "Viewapproval", "Approverequest",
                 "Denyrequest", "Opensetup", "Webdashboard", "Websession", "Setupurl", "Pairingrequest",
                 "Approvepairing", "Rejectpairing", "Endbrowsersession", "Buildinfo", "Viewdiagnostics",
                 "Viewconnection", "Stopserver", "Port", "Portstatus", "Projectroot", "Datalocation", "Datadir",
                 "Datastatus", "Confirmmove", "Claimproject", "Createprojectmemory", "Claimprojectmemory",
                 "Projectid", "Loglevel"]
EXPECTED_PAGES = ["TDflowMCP", "Agents", "Web", "Advanced"]
READONLY_PARS = ["Version", "Compatsummary", "Lastcheck", "Status", "Statusdetail", "Tellagent", "Connectedagents",
                 "Detectedclients", "Agentsummary", "Agentinfo", "Approvalsummary", "Websession", "Setupurl",
                 "Pairingrequest", "Buildinfo", "Portstatus", "Datadir", "Datastatus", "Projectid"]

R = {"mode": MODE, "tests": [], "harness": {}, "frames": [], "errors": []}
S = {"gen": None, "wait": None, "label": "boot", "done": False}


def T(name, ok, **detail):
    R["tests"].append(dict(name=name, ok=bool(ok), **detail))


def write(name, obj):
    p = os.path.join(D, name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(p + ".tmp", p)


# ---- wait conditions yielded by the script ----------------------------------------------------
def frames(n):
    return ("frames", n)


def until(fn, seconds, name):
    return ("until", fn, time.monotonic() + seconds, name)


def checkpoint(name, data=None, seconds=300):
    return ("cp", name, data or {}, time.monotonic() + seconds)


def own_listening(port):
    osl = __import__("sys").modules.get("tdflowmcp.net.oslisteners")
    if osl is None:
        return None
    tab = osl.read_listeners(port, os.getpid())
    return tab is not None and bool(tab[0] or tab[1])


def sha_file(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as f:
        return hashlib.sha256(f.read().replace("\r\n", "\n").encode("utf-8")).hexdigest()


def sha_text(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def load():
    if MODE in ("memory_restart", "native_restart", "native_restart2", "native_recover"):
        # The reopened saved .toe already contains the component (loading the .tox again would add a second one).
        return td.op("/project1/TDflowMCP")
    comp = td.op("/project1").loadTox(TOX)
    return comp


def recorder(calls, ext):
    def launch(url):                     # background thread: TD-free on purpose
        calls.append({"url": url, "state": ext.ctl.state.value, "off_main": threading.current_thread() is not MAIN})
        return True
    return launch


# ---- the script -----------------------------------------------------------------------------
def script():
    comp = load()
    yield frames(20)
    ext = comp.ext.TDflowMCPExt
    if MODE != "native_screens":
        ext.connector.dialog_opener = lambda title, text, buttons, cb: False   # no real dialogs in automated runs
    if MODE.startswith(("gate", "perf", "onboard", "dogfood", "memory", "native", "probe")):                 # audit-worker gate + 1B-vs-worker comparison
        ext.launcher = lambda url: True
        yield from __import__("td_runner_gate").run(MODE, comp, ext, T, frames, until, checkpoint, R)
        return
    T("load.component", comp is not None and comp.name == "TDflowMCP" and ext is not None)
    T("load.params", [p.name for p in comp.customPars] == EXPECTED_PARS, params=[p.name for p in comp.customPars])
    T("load.defaults", comp.par.Status.eval() == "Off" and comp.par.Port.eval() == 9480
      and comp.par.Setupurl.eval() == "", status=comp.par.Status.eval(), port=comp.par.Port.eval())
    # UX assignment: four pages; diagnostics run automatically on load, and safely (no listener, no store).
    T("ux.PAGES", [pg.name for pg in comp.customPages] == EXPECTED_PAGES, pages=[pg.name for pg in comp.customPages])
    T("ux.RUN_DIAGNOSTICS_ON_FIRST_PAGE", comp.par.Rundiagnostics.page.name == "TDflowMCP"
      and comp.par.Connectagent.page.name == "TDflowMCP" and comp.par.Opensetup.page.name == "Web"
      and comp.par.Lastcheck.page.name == "TDflowMCP")
    T("ux.COPY_TOKEN_IS_A_MANUAL_FALLBACK_ON_THE_AGENTS_PAGE", comp.par.Copytoken.page.name == "Agents"
      and comp.par.Disconnectagent.page.name == "Agents" and comp.par.Viewconnection.page.name == "Advanced")
    T("ux.LAST_CHECK_SHOWN", "(automatic)" in comp.par.Lastcheck.eval(), last=comp.par.Lastcheck.eval())
    T("ux.AUTO_DIAGNOSTICS_ON_LOAD", comp.par.Compatsummary.eval().startswith(("Ready", "Partly", "Not ready")) and bool(ext.checks),
      summary=comp.par.Compatsummary.eval())
    T("ux.auto_diagnostics_open_nothing", ext.ctl.transport is None and ext.worker is None
      and comp.par.Status.eval() == "Off", status=comp.par.Status.eval())
    T("ux.unverified_checks_not_passed", all(c.result == "unknown" for c in ext.checks if c.id in ("net.bind", "fs.datadir",
                                                                                          "audit.integrity")),
      checks={c.id: c.result for c in ext.checks})
    T("ux.next_step_is_context_aware", "Connect Agent" in comp.par.Statusdetail.eval()
      or "not ready" in comp.par.Statusdetail.eval(), detail=comp.par.Statusdetail.eval())
    T("load.version", comp.par.Version.eval().startswith(ext.version), version=comp.par.Version.eval())
    T("load.readonly", all(getattr(comp.par, n).readOnly for n in READONLY_PARS), readonly=READONLY_PARS)
    T("load.mcp_off_by_default", not comp.par.Enablemcp.eval() and not comp.par.Suspendagents.eval())
    man = json.loads(comp.op("manifest").text)
    mism = [n for n, m in man["modules"].items() if sha_text(comp.op("src").op(m["dat"]).text) != m["sha256"]]
    T("load.embedded_matches_manifest", not mism, mismatches=mism)
    if ROOT:
        src_mism = [n for n, m in man["modules"].items() if m["sha256"] != sha_file(m["path"])]
        src_mism += [n for n, d in man["dats"].items() if d.get("path") and d["sha256"] != sha_file(d["path"])]
        T("load.embedded_matches_repo_source", not src_mism, mismatches=src_mism)
    T("load.no_transport", ext.ctl.transport is None)
    T("load.modules_embedded", all(str(getattr(__import__("sys").modules.get(n), "__file__", "")).startswith(
        "<tdflowmcp-embedded:") for n in man["modules"]))
    if MODE == "real_browser":
        yield from real_browser(comp, ext)
        return
    if MODE in ("mcp", "mcp_restart"):
        mcp = __import__("td_runner_mcp")
        ext.launcher = lambda url: True                   # never open the user's browser in this suite
        if MODE == "mcp":
            comp.par.Opensetup.pulse()
            yield until(lambda: comp.par.Status.eval() == "Setup", 10, "open_mcp")
        yield from mcp.run(comp, ext, T, frames, until, checkpoint, R, restart=(MODE == "mcp_restart"))
        return
    g = yield checkpoint("loaded", {"pid": os.getpid()})

    comp.par.Rundiagnostics.pulse()
    yield frames(5)
    T("diag.summary", comp.par.Compatsummary.eval() not in ("", "not run"), summary=comp.par.Compatsummary.eval())
    T("diag.checks", len(ext.checks) >= 9, checks={c.id: c.result for c in ext.checks})
    T("diag.no_listener", ext.ctl.transport is None and comp.par.Status.eval() == "Off")
    T("diag.tellagent_param", comp.par.Tellagent.eval().startswith("Not ready"), tellagent=comp.par.Tellagent.eval())
    yield checkpoint("diagnosed", {"pid": os.getpid()})

    calls = []
    ext.launcher = recorder(calls, ext)
    comp.par.Opensetup.pulse()
    t0 = time.monotonic()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "open")
    T("open.serving", comp.par.Status.eval() == "Setup", seconds=round(time.monotonic() - t0, 2),
      events=list(ext.ctl.events)[-4:])
    yield frames(15)
    T("open.browser_launched_once_after_serving", len(calls) == 1 and calls[0]["url"].split("#")[0] == "http://127.0.0.1:9480/setup/"
      and calls[0]["url"].split("#")[1] == "pair"                    # ADR-0015: no secret in the launched URL
      and calls[0]["state"] == "Setup" and calls[0]["off_main"], calls=calls)
    T("open.setupurl", comp.par.Setupurl.eval() == "http://127.0.0.1:9480/setup/")
    T("open.portstatus", comp.par.Portstatus.eval() == "this component", portstatus=comp.par.Portstatus.eval())
    S["label"] = "serving_tests"
    yield checkpoint("serving", {"pid": os.getpid(), "tell": ext.tell_text})
    S["label"] = "after"

    # Clipboard: success, then a forced failure. The user's clipboard text is restored afterwards.
    saved = td.ui.clipboard
    try:
        comp.par.Copytellagent.pulse()
        yield frames(3)
        # D24: Copy Agent Prompt puts the prompt (here: not ready, no agent yet) on the real clipboard, not the guide.
        T("copy.clipboard_has_the_agent_prompt", td.ui.clipboard == ext.prompt_text and bool(ext.prompt_text)
          and "It is not ready for you yet" in td.ui.clipboard and "## " not in td.ui.clipboard,
          clip=td.ui.clipboard[:200])
        T("copy.reported", comp.par.Statusdetail.eval() == "Copied (not ready yet)", detail=comp.par.Statusdetail.eval())
        T("copy.label_renamed", comp.par.Copytellagent.label == "Copy Agent Prompt"
          and comp.par.Viewinstructions.label == "View Full Instructions")
        T("copy.tellagent_says_not_ready", comp.par.Tellagent.eval().startswith("Not ready"),
          tell=comp.par.Tellagent.eval())

        def broken(_):
            raise RuntimeError("clipboard locked")
        ext.clipboard_writer = broken
        comp.par.Copytellagent.pulse()
        yield frames(3)
        T("copy.failure_reported", "copy failed" in comp.par.Statusdetail.eval().lower(),
          detail=comp.par.Statusdetail.eval())
    finally:
        ext.clipboard_writer = ext._default_clipboard
        td.ui.clipboard = saved

    # Browser launch failure is reported with the URL.
    ext.launcher = lambda url: False
    comp.par.Opensetup.pulse()
    yield frames(20)
    d = comp.par.Statusdetail.eval()
    T("browser.failure_reported", "could not be opened" in d and "http://127.0.0.1:9480/setup/" in d, detail=d)
    ext.launcher = recorder(calls, ext)

    # Timeout: the harness has a request in flight while the main thread is blocked.
    yield checkpoint("block_ready", {})
    time.sleep(6.0)                                  # longer than the 5 s job timeout
    yield frames(10)
    yield checkpoint("block_done", {})

    # Error recovery: one dispatcher failure.
    orig = ext._build_status
    state = {"n": 0}

    def boom():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("injected status failure")
        return orig()
    ext._build_status = boom
    yield checkpoint("error_injected", {})
    ext._build_status = orig
    T("error.injected_once", state["n"] >= 1)

    # Port change while serving stops the listener; no silent re-port.
    comp.par.Port = 9482
    yield until(lambda: comp.par.Status.eval() == "Off", 10, "port_change")
    T("port.change_stops", comp.par.Status.eval() == "Off" and ext.ctl.port == 9482
      and "9482" in comp.par.Statusdetail.eval(), detail=comp.par.Statusdetail.eval())
    yield checkpoint("port_changed", {"pid": os.getpid()})

    # Port conflict.
    yield checkpoint("need_squatter", {"port": 9483})
    comp.par.Port = 9483
    yield frames(3)
    comp.par.Opensetup.pulse()
    yield frames(15)
    T("conflict.error", comp.par.Status.eval() == "Error: PORT_IN_USE", status=comp.par.Status.eval(),
      detail=comp.par.Statusdetail.eval())
    T("conflict.portstatus", "in use" in comp.par.Portstatus.eval(), portstatus=comp.par.Portstatus.eval())
    T("conflict.not_listening", not own_listening(9483))
    T("conflict.never_changed_port", comp.par.Port.eval() == 9483 and ext.ctl.port == 9483)
    yield checkpoint("conflict_checked", {"pid": os.getpid()})

    # Back to 9480; stop and restart.
    comp.par.Port = 9480
    yield frames(3)
    T("port.error_cleared_by_port_change", comp.par.Status.eval() == "Off", status=comp.par.Status.eval())
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "reopen")
    comp.par.Stopserver.pulse()
    yield until(lambda: comp.par.Status.eval() == "Off", 10, "stop")
    T("stop.off", comp.par.Status.eval() == "Off" and ext.ctl.transport is None and not own_listening(9480))
    yield checkpoint("stopped", {"pid": os.getpid()})
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "restart")
    T("restart.serving", comp.par.Status.eval() == "Setup")
    yield checkpoint("restarted", {"pid": os.getpid()})

    # Paused timeline: the pump is driven by the Execute DAT; requests must still be served.
    root_time = td.op("/").time
    was_playing = root_time.play
    td.run("op('/').time.play = True", delayMilliSeconds=15000)        # safety net if frames stopped
    root_time.play = False
    yield frames(30)
    T("paused_timeline.root_paused", root_time.play is False or root_time.play == 0)
    yield checkpoint("paused", {})
    root_time.play = was_playing

    # Injected verification failure (fault seam: the controller's evidence dependency).
    bvm = __import__("sys").modules["tdflowmcp.net.bindverify"]
    evm = __import__("sys").modules["tdflowmcp.net.evidence"]
    lan = evm.lan_ipv4()
    bad = bvm.verdict(["127.0.0.1"], [], {(lan[0] if lan else "192.0.2.1"): "connected"},
                      others=[("0.0.0.0", 9480, 4242)])

    class Now:
        def done(self):
            return True

        def result(self):
            return bad
    real_ev = ext.ctl.d.start_evidence
    comp.par.Stopserver.pulse()
    yield until(lambda: comp.par.Status.eval() == "Off", 10, "stop_before_fault")
    ext.ctl.d.start_evidence = lambda port, host: Now()
    n_before = len(calls)
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval().startswith("Error"), 10, "verify_fail")
    T("verify_fail.error", comp.par.Status.eval() == "Error: BIND_UNVERIFIED", status=comp.par.Status.eval())
    T("verify_fail.no_listener", not own_listening(9480))
    yield frames(10)
    T("verify_fail.browser_not_launched", len(calls) == n_before, launches=len(calls) - n_before)
    yield checkpoint("verify_failed", {"diagnostics": ext.Diagnostics(), "status": comp.par.Status.eval(),
                                       "detail": comp.par.Statusdetail.eval(), "tell": ext.tell_text})
    ext.ctl.d.start_evidence = real_ev
    comp.par.Stopserver.pulse()                      # acknowledge the error
    yield frames(3)
    T("verify_fail.acknowledged", comp.par.Status.eval() == "Off")

    # Stop pulsed in the same frame as Open Setup (during Starting).
    n_calls = len(calls)
    comp.par.Opensetup.pulse()
    comp.par.Stopserver.pulse()
    yield until(lambda: comp.par.Status.eval() == "Off" and not own_listening(9480), 10, "stop_while_starting")
    yield frames(30)
    T("starting.stop_wins", comp.par.Status.eval() == "Off" and not own_listening(9480) and len(calls) == n_calls,
      status=comp.par.Status.eval())

    # Idle stop, on a port no open Setup tab polls (an open tab counts as activity, by design).
    comp.par.Port = 9481
    yield frames(3)
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "idle_open")
    ext.ctl.idle_seconds = 2.0
    yield until(lambda: comp.par.Status.eval() == "Off", 15, "idle")
    T("idle.stops", comp.par.Status.eval() == "Off" and "idle" in comp.par.Statusdetail.eval().lower(),
      detail=comp.par.Statusdetail.eval())
    ext.ctl.idle_seconds = 1800.0
    yield checkpoint("idle_stopped", {"pid": os.getpid()})
    comp.par.Port = 9480
    yield frames(3)

    # A second TDflowMCP component in the same project cannot take the port.
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "before_second")
    comp2 = load()
    yield frames(20)
    ext2 = comp2.ext.TDflowMCPExt
    ext2.launcher = recorder([], ext2)
    comp2.par.Opensetup.pulse()
    yield frames(10)
    T("second.refused", comp2.par.Status.eval() == "Error: PORT_IN_USE"
      and "Another TDflowMCP component" in comp2.par.Statusdetail.eval(), status=comp2.par.Status.eval(),
      detail=comp2.par.Statusdetail.eval())
    T("second.instances_warn", any(c.id == "td.instances" and c.result == "warn" for c in ext2.checks))
    T("second.first_still_serving", comp.par.Status.eval() == "Setup" and own_listening(9480))
    comp2.destroy()
    yield frames(5)

    # Extension re-initialisation while serving closes the old listener.
    R["reverifies_before_reinit"] = ext.ctl.reverifies
    R["dispatch_before_reinit"] = dict(ext.dispatch_threads)
    comp.par.reinitextensions.pulse()
    yield until(lambda: own_listening(9480) is False, 10, "reinit")
    yield frames(5)
    ext = comp.ext.TDflowMCPExt
    T("reinit.old_listener_closed", own_listening(9480) is False and comp.par.Status.eval() == "Off",
      status=comp.par.Status.eval())
    yield checkpoint("reinitialised", {"pid": os.getpid()})
    calls = []
    ext.launcher = recorder(calls, ext)
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "after_reinit")
    T("reinit.serves_again", comp.par.Status.eval() == "Setup")

    diag = ext.Diagnostics()
    before = R.get("dispatch_before_reinit", {"main": 0, "other": 0})
    T("threads.dispatch_on_main_thread_only", diag["dispatch_threads"]["other"] == 0 and before["other"] == 0
      and before["main"] > 0, dispatch_before_reinit=before, dispatch_after=diag["dispatch_threads"])
    T("frame.no_errors", diag["frame_errors"] == 0, last=diag["last_frame_error"])
    T("reverify.ran", R.get("reverifies_before_reinit", 0) >= 1, reverifies=R.get("reverifies_before_reinit"))
    params = {p.name: str(p.eval()) for p in comp.customPars}
    yield checkpoint("scan", {"params": params, "tell": ext.tell_text + ext._refresh_prompt(),
                              "tellagent_dat": comp.op("tellagent").text,
                              "diagnostics": diag})

    # Delete the component while it is serving: the listener must go away.
    t0 = time.monotonic()
    comp.destroy()
    yield until(lambda: own_listening(9480) is False, 30, "destroy")
    T("destroy.listener_closed", own_listening(9480) is False, seconds=round(time.monotonic() - t0, 2))
    R["destroy_seconds"] = round(time.monotonic() - t0, 2)
    yield checkpoint("destroyed", {"pid": os.getpid()})

    # Reload, serve, then quit the project while serving (onExit must close the listener).
    comp = load()
    yield frames(20)
    ext = comp.ext.TDflowMCPExt
    ext.launcher = recorder([], ext)
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "reload_open")
    T("reload.serving", comp.par.Status.eval() == "Setup")
    yield checkpoint("final_serving", {"pid": os.getpid()})
    # The project-exit handler itself (frame_exec.onExit -> Shutdown) closes the listener.
    comp.op("frame_exec").module.onExit()
    yield until(lambda: own_listening(9480) is False, 10, "on_exit")
    T("exit_handler.closes_listener", own_listening(9480) is False)


def real_browser(comp, ext):
    """Production launcher: the user's default browser opens the page; evidence = the page's own API call."""
    comp.par.Opensetup.pulse()
    yield until(lambda: comp.par.Status.eval() == "Setup", 10, "open")
    T("real.serving", comp.par.Status.eval() == "Setup")
    seen = {}

    def browser_hit():
        t = ext.ctl.transport
        for rec in list(t._audit_ring) if t else []:
            if rec.get("path") in ("/setup/", "/setup/app.js", "/setup/api/status") and rec.get("status") == 200:
                seen[rec["path"]] = rec["code"]
        return all(k in seen for k in ("/setup/", "/setup/app.js", "/setup/api/status"))   # an old tab may poll too
    yield until(browser_hit, 45, "browser")
    T("real.browser_loaded_page_and_ran_js", "/setup/" in seen and "/setup/app.js" in seen
      and "/setup/api/status" in seen, seen=seen, launch=ext.ctl.launch_status)
    T("real.launch_status", ext.ctl.launch_status == "launched", launch=ext.ctl.launch_status)
    yield frames(30)


# ---- frame driver ---------------------------------------------------------------------------
DRIVER = "def onFrameStart(frame):\n\t__import__('td_runner').drive()\n\treturn\n"


def on_frame(me, frame):
    """Boot DAT (root timeline): create a driver COMP with its own independent timeline once, so the
    runner keeps going while a test pauses the project timeline (spike S7)."""
    if S.get("driver"):
        return
    S["driver"] = True
    old = td.op("/project1/test_driver/drive")
    if old is not None:                          # a saved test project reopened: reuse its driver
        old.par.active = True
        return
    d = td.op("/project1").create(td.baseCOMP, "test_driver")
    tm = d.create(td.baseCOMP, "local").create(td.timeCOMP, "time")
    tm.par.independent = True
    tm.par.play = True
    e = d.create(td.executeDAT, "drive")
    e.text = DRIVER
    e.par.framestart = True
    e.par.active = True


HANG = {"fh": None, "n": 0}


def _watchdog():
    """Test-only stall diagnostics (D18, unexplained hang): faulthandler's C-level watchdog needs no GIL, so if
    TouchDesigner's main thread stops ticking for 10 s, every Python thread's stack lands in hang_traceback.txt."""
    now = time.monotonic()
    if now - HANG.get("armed", 0.0) < 1.0:       # review 10, F7: re-arm on wall time, not frame count
        return
    HANG["armed"] = now
    try:
        import faulthandler
        if HANG["fh"] is None:
            HANG["fh"] = open(os.path.join(D, "hang_traceback.txt"), "w")
        faulthandler.dump_traceback_later(10, repeat=False, file=HANG["fh"], exit=False)
    except Exception:
        pass


def drive():
    if S["done"]:
        return
    _watchdog()
    bj = S.get("bench_jobs")                  # gate_bench: run queued main-thread handoffs once per frame
    while bj is not None and not bj.empty():
        bj.get_nowait()()
    R["frames"].append((S["label"], time.perf_counter()))
    if S["gen"] is None:
        S["gen"] = script()
        S["wait"] = ("frames", 0)
    w = S["wait"]
    send = None
    try:
        if w[0] == "frames":
            if w[1] > 0:
                S["wait"] = ("frames", w[1] - 1)
                return
        elif w[0] == "until":
            ok = False
            try:
                ok = bool(w[1]())
            except Exception:
                ok = False
            if not ok and time.monotonic() < w[2]:
                return
            if not ok:
                R["errors"].append(f"timeout waiting for {w[3]}")
        elif w[0] == "cp":
            name = w[1]
            if not S.get("cp_written") == name:
                write(f"cp_{name}.json", {"name": name, "data": w[2]})
                S["cp_written"] = name
            gp = os.path.join(D, f"go_{name}.json")
            if not os.path.exists(gp):
                if time.monotonic() > w[3]:
                    R["errors"].append(f"harness did not answer {name}")
                    finish()
                return
            try:
                with open(gp, encoding="utf-8") as f:
                    send = json.load(f)
            except (PermissionError, ValueError):          # being replaced right now (Windows): retry
                return
            R["harness"][name] = send
        S["wait"] = S["gen"].send(send) if S.get("started") else next(S["gen"])
        S["started"] = True
    except StopIteration:
        finish()
    except Exception:
        R["errors"].append(traceback.format_exc()[-2500:])
        finish()


def frame_stats():
    by = {}
    fr = R.pop("frames")
    for i in range(1, len(fr)):
        by.setdefault(fr[i][0], []).append((fr[i][1] - fr[i - 1][1]) * 1000)
    out = {}
    for lab, xs in by.items():
        s = sorted(xs)
        out[lab] = {"n": len(s), "p50_ms": round(s[len(s) // 2], 2), "p95_ms": round(s[max(0, int(len(s) * .95) - 1)], 2),
                    "max_ms": round(s[-1], 2), "over_100ms": sum(1 for x in s if x > 100)}
    return out


def finish():
    if S["done"]:
        return
    S["done"] = True
    R["frame_stats"] = frame_stats()
    R["passed"] = sum(t["ok"] for t in R["tests"])
    R["total"] = len(R["tests"])
    R["td_build"] = td.app.build
    write("td_results.json", R)
    try:
        __import__("faulthandler").cancel_dump_traceback_later()
    except Exception:
        pass
    td.run("project.quit(force=True)", delayFrames=10)
