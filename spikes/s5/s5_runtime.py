"""S5 spike runtime, executed inside a disposable TouchDesigner project (not TDflowMCP source).

NOTE: corrected after run 1 (conflict probe now only after SERVING; clipboard restore in finally;
no hard-coded paths). Run 1 used the earlier version; the full mode was not re-run afterwards.

phase_a: runtime/library/parameter/clipboard probes, create Web Server DAT, activate in VERIFYING.
phase_b: bind-verification gate (security-model §8.2): pass -> SERVING; fail/ambiguous -> close now.
phase_c/d: port-conflict probe, shutdown evidence, write results, quit.
"""
import importlib
import json
import os
import sys
import threading
import time
import traceback

import td

import s5_bindcheck
import s5_server

S5 = os.environ.get("TDFLOW_S5_DIR") or os.path.dirname(os.path.abspath(__file__))
PORT = 9480
R = {"started": time.time(), "steps": {}}
CTX = {}


def _rec(key, fn):
    try:
        R["steps"][key] = {"ok": True, "value": fn()}
    except Exception as e:  # record, never raise
        R["steps"][key] = {"ok": False, "error": repr(e), "trace": traceback.format_exc()[-800:]}


def _write(name, obj):
    with open(os.path.join(S5, "results", name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)


def _runtime():
    a = td.app
    return {
        "app.build": getattr(a, "build", None), "app.version": getattr(a, "version", None),
        "app.product": getattr(a, "product", None), "app.osName": getattr(a, "osName", None),
        "app.osVersion": getattr(a, "osVersion", None),
        "sys.version": sys.version, "version_info": list(sys.version_info[:3]),
        "pid": os.getpid(), "main_thread": threading.current_thread().name,
    }


def _libs():
    out = {}
    for name in ["json", "sqlite3", "ssl", "hmac", "hashlib", "secrets", "asyncio", "unittest",
                 "ctypes", "zipfile", "urllib.request", "threading", "socket", "webbrowser",
                 "jsonschema", "yaml", "requests", "numpy", "pydantic", "mcp", "starlette", "uvicorn"]:
        try:
            m = importlib.import_module(name)
            out[name] = {"ok": True, "version": str(getattr(m, "__version__", ""))}
        except Exception as e:
            out[name] = {"ok": False, "error": type(e).__name__}
    import sqlite3, ssl
    out["sqlite3"]["sqlite_version"] = sqlite3.sqlite_version
    out["ssl"]["openssl"] = ssl.OPENSSL_VERSION
    try:
        import jsonschema
        jsonschema.validate({"a": 1}, {"type": "object", "properties": {"a": {"type": "integer"}}})
        out["jsonschema"]["validate_smoke"] = "ok"
    except Exception as e:
        out["jsonschema"]["validate_smoke"] = repr(e)
    return out


def _custom_pars():
    root = td.op("/project1")
    comp = root.create(td.baseCOMP, "s5_comp")
    CTX["comp"] = comp
    page = comp.appendCustomPage("TDflowMCP")
    res = {}
    ver = page.appendStr("Version", label="Version")[0]
    ver.val = "0.0.0-s5"
    res["str_readOnly_attr"] = hasattr(ver, "readOnly")
    ver.readOnly = True
    try:
        ver.val = "0.0.1-s5"
        res["script_set_after_readOnly"] = ver.eval()
    except Exception as e:
        res["script_set_after_readOnly"] = "error:" + repr(e)
    page.appendPulse("Opensetup", label="Open Setup")
    tog = page.appendToggle("Enablemcp", label="Enable MCP")[0]
    tog.default = False
    port = page.appendInt("Port", label="Port")[0]
    port.default = PORT
    port.val = PORT
    port.normMin, port.normMax = 1024, 65535
    port.clampMin = port.clampMax = True
    port.min, port.max = 1024, 65535
    menu = page.appendMenu("Loglevel", label="Log level")[0]
    menu.menuNames = ["error", "warning", "info", "debug"]
    menu.menuLabels = ["Error", "Warning", "Info", "Debug"]
    menu.default = "info"
    tell = page.appendStr("Tellagent", label="Tell Your Coding Agent")[0]
    tell.readOnly = True
    res["attrs"] = {n: hasattr(port, n) for n in ["readOnly", "enable", "help", "startSection", "default",
                                                   "clampMin", "normMin", "pulse", "enableExpr"]}
    res["par_values"] = {p.name: str(p.eval()) for p in comp.customPars}
    res["custom_pages"] = [pg.name for pg in comp.customPages]
    # parameter-execute DAT for pulse callback
    pe = comp.create(td.parameterexecuteDAT, "pe")
    pe.text = ("import s5_server\n"
               "def onPulse(par):\n\ts5_server.STATE['pulses'].append(par.name)\n\treturn\n"
               "def onValueChange(par, prev):\n\ts5_server.STATE.setdefault('valuechanges', []).append(par.name)\n\treturn\n")
    res["parexec_par_names"] = [p.name for p in pe.pars()]
    for name, val in (("op", comp.path), ("pars", "*"), ("onpulse", True), ("custom", True), ("active", True)):
        if hasattr(pe.par, name):
            setattr(pe.par, name, val)
    comp.par.Opensetup.pulse()
    # Execute DAT: which project-level callbacks exist (pre-save hook?)
    ex = comp.create(td.executeDAT, "ex_probe")
    res["executeDAT_par_names"] = [p.name for p in ex.pars()]
    ex.destroy()
    return res


def _clipboard():
    ui = td.ui
    res = {"has_ui_clipboard": hasattr(ui, "clipboard")}
    if res["has_ui_clipboard"]:
        old = ui.clipboard               # text content only; never recorded
        try:
            ui.clipboard = "TDflowMCP-S5-clipboard-test"
            res["roundtrip"] = (ui.clipboard == "TDflowMCP-S5-clipboard-test")
        finally:
            ui.clipboard = old           # restores TEXT content; non-text clipboard formats are not preserved
            res["text_restored"] = (ui.clipboard == old)
    return res


def _webserver_setup():
    comp = CTX["comp"]
    pre = s5_bindcheck.listeners(PORT)
    CTX["pre_listeners"] = pre
    ws = comp.create(td.webserverDAT, "ws")
    cb = comp.create(td.textDAT, "ws_callbacks")
    cb.text = ("import s5_server\n"
               "def onHTTPRequest(webServerDAT, request, response):\n\treturn s5_server.onHTTPRequest(webServerDAT, request, response)\n"
               "def onServerStart(webServerDAT):\n\treturn s5_server.onServerStart(webServerDAT)\n"
               "def onServerStop(webServerDAT):\n\treturn s5_server.onServerStop(webServerDAT)\n"
               "def onWebSocketOpen(webServerDAT, client, uri):\n\treturn\n"
               "def onWebSocketClose(webServerDAT, client):\n\treturn\n"
               "def onWebSocketReceiveText(webServerDAT, client, data):\n\treturn\n"
               "def onWebSocketReceiveBinary(webServerDAT, client, data):\n\treturn\n"
               "def onWebSocketReceivePing(webServerDAT, client, data):\n\treturn\n"
               "def onWebSocketReceivePong(webServerDAT, client, data):\n\treturn\n")
    CTX["ws"] = ws
    res = {"par_names": [p.name for p in ws.pars()],
           "methods": sorted(n for n in dir(ws) if not n.startswith("_")),
           "pre_listeners_9480": pre}
    if pre["v4"] or pre["v6"]:
        res["port_in_use_before_start"] = True
        return res
    ws.par.active = False
    ws.par.callbacks = cb.path
    ws.par.port = PORT
    res["has_localaddress"] = hasattr(ws.par, "localaddress")
    if res["has_localaddress"]:
        ws.par.localaddress = "127.0.0.1"
        res["localaddress_readback"] = ws.par.localaddress.eval()
    s5_server.STATE["mode"] = "VERIFYING"
    t = time.time()
    ws.par.active = True
    CTX["t_activate"] = t
    res["verdict_same_frame"] = list(s5_bindcheck.verdict(PORT, os.getpid()))
    res["errors_after_activate"] = ws.errors()
    res["warnings_after_activate"] = ws.warnings()
    return res


def phase_a(boot_op=None):
    s5_server.STATE["main_ident"] = threading.get_ident()
    os.makedirs(os.path.join(S5, "results"), exist_ok=True)
    _rec("runtime", _runtime)
    _rec("libs", _libs)
    _rec("custom_pars", _custom_pars)
    _rec("clipboard", _clipboard)
    _rec("browser_api", lambda: {"ui.viewFile": hasattr(td.ui, "viewFile")})
    if os.environ.get("TDFLOW_S5_NO_WS"):
        R["steps"]["webserver_setup"] = {"ok": True, "value": {"skipped": "params-only run"}}
        td.run("import s5_runtime; s5_runtime.phase_params()", delayFrames=20)
        _write("phase_a.json", R)
        return
    _rec("webserver_setup", _webserver_setup)
    _write("phase_a.json", R)
    CTX["b_attempts"] = 0
    if "ws" in CTX and not R["steps"]["webserver_setup"].get("value", {}).get("port_in_use_before_start"):
        td.run("import s5_runtime; s5_runtime.phase_b()", delayFrames=10)
    else:
        _finish("no_webserver")


def phase_b():
    ws = CTX["ws"]
    CTX["b_attempts"] += 1
    v, ev = s5_bindcheck.verdict(PORT, os.getpid())
    att = R.setdefault("bind_attempts", [])
    att.append({"attempt": CTX["b_attempts"], "verdict": v, "evidence": ev,
                "dt_since_activate": round(time.time() - CTX["t_activate"], 3)})
    if v == "ambiguous" and CTX["b_attempts"] < 5 and not ev.get("table", {}).get("v4") and not ev.get("table", {}).get("v6"):
        td.run("import s5_runtime; s5_runtime.phase_b()", delayFrames=15)   # socket not up yet
        return
    if v == "pass":
        s5_server.STATE["mode"] = "SERVING"
        R["gate"] = {"result": "SERVING", "t": time.time()}
        try:
            td.ui.viewFile("http://127.0.0.1:%d/s5/browser" % PORT)
            R["steps"]["browser_launch_call"] = {"ok": True, "method": "ui.viewFile"}
        except Exception as e:
            R["steps"]["browser_launch_call"] = {"ok": False, "error": repr(e)}
    else:
        t = time.time()
        ws.par.active = False                       # close immediately, same callback
        R["gate"] = {"result": "CLOSED", "verdict": v, "t": t,
                     "listeners_after_close": s5_bindcheck.listeners(PORT, os.getpid())}
    R["ws_errors"] = ws.errors()
    R["ws_warnings"] = ws.warnings()
    _write("phase_b.json", R)
    with open(os.path.join(S5, "results", "ready.json"), "w", encoding="utf-8") as f:
        json.dump({"gate": R["gate"]["result"]}, f)
    if R["gate"]["result"] != "SERVING":
        R["conflict"] = {"skipped": "gate CLOSED; never re-listen on a build that failed bind verification"}
        td.run("import s5_runtime; s5_runtime._finish('gate_closed')", delayFrames=30)


def phase_c():
    """Port-conflict probe: a second Web Server DAT on the same loopback port (only after SERVING)."""
    comp = CTX["comp"]
    if R.get("gate", {}).get("result") != "SERVING":
        _finish("conflict_skipped")
        return
    try:
        ws2 = comp.create(td.webserverDAT, "ws2")
        ws2.par.port = PORT
        if hasattr(ws2.par, "localaddress"):
            ws2.par.localaddress = "127.0.0.1"
        ws2.par.active = True
        CTX["ws2"] = ws2
    except Exception as e:
        R["conflict_setup_error"] = repr(e)
    td.run("import s5_runtime; s5_runtime.phase_d()", delayFrames=30)


def phase_d():
    ws, ws2 = CTX.get("ws"), CTX.get("ws2")
    if ws2 is not None:
        R["conflict"] = {"ws2_errors": ws2.errors(), "ws2_warnings": ws2.warnings(),
                         "listeners_with_two_dats": s5_bindcheck.listeners(PORT, os.getpid()),
                         "ws1_errors": ws.errors() if ws else None}
        ws2.par.active = False
    _finish("normal")


def _finish(reason):
    comp = CTX.get("comp")
    ws = CTX.get("ws")
    if ws is not None:
        ws.par.active = False
    R["pulses_observed"] = list(s5_server.STATE["pulses"])
    R["server_events"] = list(s5_server.STATE["server_events"])
    R["request_log_count"] = len(s5_server.STATE["log"])
    R["request_threads"] = sorted({(r["on_main_thread"], r["thread_name"]) for r in s5_server.STATE["log"]})
    R["requests_by_mode"] = {}
    for r in s5_server.STATE["log"]:
        R["requests_by_mode"][r["mode"]] = R["requests_by_mode"].get(r["mode"], 0) + 1
    R["handler_ms"] = [r.get("handler_ms") for r in s5_server.STATE["log"] if r.get("handler_ms") is not None]
    R["browser_records"] = list(s5_server.STATE["browser"])
    R["finish_reason"] = reason
    R["finished"] = time.time()
    _write("results.json", R)
    td.run("import s5_runtime; s5_runtime._quit()", delayFrames=20)


def _quit():
    R["listeners_at_quit"] = s5_bindcheck.listeners(PORT, os.getpid())
    _write("results.json", R)
    td.project.quit(force=True)


def phase_params():
    comp = CTX["comp"]
    pe = comp.op("pe")
    R["pe_errors"] = pe.errors() if pe else "no pe"
    R["pulses_before_late_pulse"] = list(s5_server.STATE["pulses"])
    comp.par.Opensetup.pulse()
    comp.par.Port.val = 9481
    td.run("import s5_runtime; s5_runtime.phase_params2()", delayFrames=20)


def phase_params2():
    comp = CTX["comp"]
    R["pulses_after_late_pulse"] = list(s5_server.STATE["pulses"])
    R["valuechanges"] = list(s5_server.STATE.get("valuechanges", []))
    R["menu_default_vs_val"] = {"default": str(comp.par.Loglevel.default), "val": str(comp.par.Loglevel.eval())}
    _finish("params_only")
