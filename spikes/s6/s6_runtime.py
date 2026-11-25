"""S6 spike runtime, executed inside a disposable TouchDesigner project (not TDflowMCP source).

Drives the real ``tdflowmcp.net.pyhttp`` transport + ``bindverify.BindGate`` from TouchDesigner's
frame loop (``on_frame`` is called from the boot Execute DAT's onFrameStart, i.e. the main thread):

  baseline (no server) -> start -> VERIFYING (evidence collected off-thread, pure sockets only)
  -> SERVING (pump every frame) -> [restart: CLOSING -> CLOSED -> user re-activation -> SERVING]
  -> finish: CLOSING -> CLOSED (OS table confirmed) -> results.json -> project.quit

Everything that touches ``td`` (DATs, ops, app, absTime, project) happens in on_frame / dispatch,
both on the main thread. Spike-only JSON-RPC methods (``s6/*``) require the bearer token.
Mode ``TDFLOW_S6_MODE=conflict``: the harness occupies 127.0.0.1:9480 first; we must not serve.
"""
import json
import os
import secrets
import socket
import sys
import threading
import time
import traceback

import td  # noqa: F401  (TouchDesigner module; only used on the main thread)

from tdflowmcp.net import bindverify as bv
from tdflowmcp.net import oslisteners
from tdflowmcp.net.pyhttp import EmbeddedHTTPTransport, Response, TokenAuthenticator, TransportError, error

D = os.environ.get("TDFLOW_S6_DIR") or os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(D, "results")
MODE = os.environ.get("TDFLOW_S6_MODE", "main")
PORT = 9480
BASELINE_FRAMES = 600
MAIN = threading.main_thread()

S = {"phase": "init", "label": "baseline", "frames": [], "pump": [], "dispatch": {"main": 0, "other": 0},
     "events": [], "errors": [], "restarts": 0, "t0": time.perf_counter(), "action": None, "action_at": 0,
     "suspend_until": None, "touch": []}
G = {"gate": None, "t": None, "token": None, "auth": None, "ev_thread": None, "ev_result": None, "me": None}


def log(kind, **kw):
    S["events"].append(dict(t=round(time.perf_counter() - S["t0"], 3), kind=kind, **kw))


def write(name, obj):
    os.makedirs(RES, exist_ok=True)
    tmp = os.path.join(RES, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, os.path.join(RES, name))


# ---- evidence (pure sockets / OS table; safe off the main thread) -----------------------------
def lan_ipv4():
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = info[4][0]
            if not a.startswith("127."):
                out.add(a)
    except OSError:
        pass
    return sorted(out)


def probe(addr, port, timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((addr, port))
        return "connected"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


def collect_evidence(port, pid, sock_host, out):
    t = time.perf_counter()
    tab = oslisteners.read_listeners(port, pid)
    others = oslisteners.other_listeners(port, pid)
    probes = {a: probe(a, port) for a in lan_ipv4()}
    v4, v6 = tab if tab is not None else (None, None)
    out["ev"] = bv.verdict(v4, v6, probes, sockname=sock_host, others=others)
    out["ms"] = round((time.perf_counter() - t) * 1000, 1)
    out["thread"] = threading.current_thread().name


# ---- dispatch: main thread only ---------------------------------------------------------------
def rpc_result(mid, result):
    return Response.json(200, {"jsonrpc": "2.0", "id": mid, "result": result})


def rpc_error(mid, code, msg):
    return Response.json(200, {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}})


def status():
    t = G["t"]
    return {"gate": G["gate"].state.value, "error_code": G["gate"].error_code, "build": td.app.build,
            "frame": td.absTime.frame, "cook_rate": td.project.cookRate, "label": S["label"],
            "is_main": threading.current_thread() is MAIN, "thread": threading.current_thread().name,
            "suspended": t.suspended if t else None, "stats": t.stats() if t else {}, "restarts": S["restarts"],
            "dispatch": dict(S["dispatch"])}


def dispatch(job):
    is_main = threading.current_thread() is MAIN
    S["dispatch"]["main" if is_main else "other"] += 1
    if not is_main:                       # must never happen; refuse rather than touch TD
        return error(500, "OFF_MAIN_THREAD")
    if job.route == "well_known":
        return Response.json(200, {"name": "TDflowMCP", "spike": "s6", "transport": "python-http"})
    if job.route == "api" and job.method == "GET" and job.path == "/setup/api/status":
        return Response.json(200, status())
    if job.route != "mcp":
        return error(404, "NOT_FOUND")
    try:
        msg = json.loads(job.body.decode("utf-8"))
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    except Exception:
        return Response.json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    if method == "ping":
        return rpc_result(mid, {})
    if method == "tools/list":
        return rpc_result(mid, {"tools": []})          # S6 exposes no tools; nothing can execute code
    if not isinstance(method, str) or not method.startswith("s6/"):
        return rpc_error(mid, -32601, "Method not found")
    return spike_control(mid, method, params)


def spike_control(mid, method, params):
    if method == "s6/mark":
        S["label"] = str(params.get("label", "?"))[:32]
        log("mark", label=S["label"])
        return rpc_result(mid, {"label": S["label"]})
    if method == "s6/slow":
        ms = max(0, min(int(params.get("ms", 0)), 5000))
        time.sleep(ms / 1000.0)                       # deliberately blocks the main thread (timeout test)
        return rpc_result(mid, {"slept_ms": ms})
    if method == "s6/raise":
        raise RuntimeError("s6 deliberate dispatch error")
    if method == "s6/touch":
        # Operator access through the queue: create, edit, read and destroy a scratch DAT.
        parent = G["me"].parent()
        n = parent.create(td.textDAT, "s6_scratch")
        n.text = "touched at frame %d" % td.absTime.frame
        got = n.text
        n.destroy()
        rec = {"is_main": threading.current_thread() is MAIN, "text": got, "exists_after": parent.op("s6_scratch") is not None}
        S["touch"].append(rec)
        return rpc_result(mid, rec)
    if method == "s6/suspend":
        G["t"].suspended = True
        S["suspend_until"] = S["n"] + max(1, min(int(params.get("frames", 60)), 600))
        log("suspend")
        return rpc_result(mid, {"suspended": True})
    if method == "s6/conflict":
        other = EmbeddedHTTPTransport("127.0.0.1", PORT, state_fn=lambda: bv.ListenerState.OFF)
        try:
            other.start()
            other.begin_close()
            res = {"started": True}
        except TransportError as e:
            res = {"started": False, "code": e.code}
        log("conflict", **res)
        return rpc_result(mid, res)
    if method in ("s6/restart", "s6/finish"):
        S["action"], S["action_at"] = method[3:], S["n"] + 10   # after this response is written
        return rpc_result(mid, {"scheduled": S["action"]})
    return rpc_error(mid, -32601, "Method not found")


# ---- lifecycle -------------------------------------------------------------------------------
def static_snapshot(me):
    p = me.parent()
    html = p.op("setup_index_html").text.encode("utf-8")
    return {"/setup": ("text/html; charset=utf-8", html), "/setup/": ("text/html; charset=utf-8", html),
            "/setup/app.css": ("text/css; charset=utf-8", p.op("setup_app_css").text.encode("utf-8")),
            "/setup/app.js": ("text/javascript; charset=utf-8", p.op("setup_app_js").text.encode("utf-8")),
            "/.well-known/tdflowmcp": ("application/json",
                                       b'{"product":"TDflowMCP","version":"spike-s6","transport":"python-http"}')}


def activate(me, user_initiated):
    gate = G["gate"]
    t_start = time.perf_counter()
    others = oslisteners.other_listeners(PORT, os.getpid())
    act = gate.request_activation(bv.embedded_capability("127.0.0.1"), user_initiated=user_initiated)
    log("activate", action=act, others=len(others or []))
    if act != "activate_verifying":
        return False
    if others:                                       # pre-check: never share the port (spike finding)
        gate.on_bind_error("PORT_IN_USE")
        log("precheck_port_in_use", owners=sorted({r[2] for r in others}))
        if MODE == "conflict":                       # also confirm the bind itself is refused
            probe_t = EmbeddedHTTPTransport("127.0.0.1", PORT, state_fn=lambda: bv.ListenerState.OFF)
            try:
                probe_t.start()
                log("direct_bind", started=True)     # would still answer 503 only (state OFF)
                probe_t.begin_close()
            except TransportError as e:
                log("direct_bind", started=False, code=e.code)
        return False
    t = EmbeddedHTTPTransport("127.0.0.1", PORT, state_fn=lambda: G["gate"].state, authenticator=G["auth"],
                              queue_max=32, max_connections=16, io_timeout=5.0, job_timeout=2.0,
                              dispatch_grace=2.0)
    t.set_static(static_snapshot(me))
    try:
        bound = t.start()
    except TransportError as e:
        gate.on_bind_error(e.code)
        log("bind_error", code=e.code, detail=str(e))
        return False
    G["t"] = t
    out = {}
    th = threading.Thread(target=collect_evidence, args=(PORT, os.getpid(), bound[0], out),
                          name="s6-evidence", daemon=True)
    G["ev_thread"], G["ev_result"] = th, out
    th.start()
    S["start_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
    S["phase"] = "verifying"
    S["verify_started"] = time.perf_counter()
    log("started", bound=bound[0], start_ms=S["start_ms"])
    return True


def begin_close(reason):
    G["gate"].user_close()
    if G["t"]:
        G["t"].begin_close()
    S["phase"], S["close_reason"], S["close_started"] = "closing", reason, time.perf_counter()
    log("begin_close", reason=reason)


def on_frame(me, _timeline_frame):
    # The timeline frame passed to onFrameStart loops (S6 finding); use our own monotonic counter.
    S["n"] = frame = S.get("n", 0) + 1
    G["me"] = me
    now = time.perf_counter()
    S["frames"].append((S["label"], now))
    if len(S["frames"]) > 20000:
        S["frames"] = S["frames"][-20000:]
    try:
        step(me, frame, now)
    except Exception:
        S["errors"].append(traceback.format_exc()[-2000:])
        if len(S["errors"]) > 20:
            finish_now(me, "too_many_errors")
    if frame % 60 == 1:
        write("debug.json", {"phase": S["phase"], "frame": frame, "errors": S["errors"][-3:],
                             "events": S["events"][-5:]})


def step(me, frame, now):
    ph = S["phase"]
    if ph == "init":
        G["gate"] = bv.BindGate()
        G["token"] = secrets.token_urlsafe(32)
        G["auth"] = TokenAuthenticator()
        G["auth"].add(G["token"], "s6-harness")
        S["build"] = td.app.build
        S["switchinterval_s"] = sys.getswitchinterval()   # recorded only; never changed (process-global)
        S["phase"], S["baseline_from"] = "baseline", frame
        log("init", mode=MODE, build=td.app.build)
        return
    if ph == "baseline":
        if frame - S["baseline_from"] >= BASELINE_FRAMES:
            S["label"] = "idle"
            if not activate(me, user_initiated=True):
                write("ready.json", {"state": G["gate"].state.value, "error_code": G["gate"].error_code,
                                     "pid": os.getpid(), "events": S["events"]})
                S["phase"], S["quit_at"] = "done", frame + 30
        return
    if ph == "verifying":
        th = G["ev_thread"]
        if th.is_alive():
            return
        out = G["ev_result"]
        act = G["gate"].on_evidence(out["ev"], now=now)
        serving_wall = time.time()                     # the gate state flipped (or not) at this instant
        log("evidence", verdict=out["ev"].verdict.value, reasons=list(out["ev"].reasons), action=act,
            ms=out["ms"], thread=out["thread"])
        if act == "serve":
            S["phase"] = "serving"
            S["last_verify"] = frame
            S.setdefault("serve_ms", []).append(round((now - S["verify_started"]) * 1000, 1))
            name = "ready.json" if S["restarts"] == 0 else f"ready_{S['restarts'] + 1}.json"
            write(name, {"state": "SERVING", "serving_wall": serving_wall, "token": G["token"], "pid": os.getpid(), "port": PORT,
                         "build": td.app.build, "restarts": S["restarts"], "events": S["events"][-10:]})
        elif act == "recheck_later":
            out.clear()
            th2 = threading.Thread(target=collect_evidence, args=(PORT, os.getpid(), G["t"].sockname()[0], out),
                                   name="s6-evidence", daemon=True)
            G["ev_thread"] = th2
            th2.start()
        else:                                          # close_now: same frame
            G["t"].begin_close()
            S["phase"], S["close_reason"], S["close_started"] = "closing", "verify_failed", now
        return
    if ph == "serving":
        t = G["t"]
        p0 = time.perf_counter()
        n = t.pump(dispatch, max_jobs=8, budget_s=0.004)
        S["pump"].append((S["label"], n, time.perf_counter() - p0))
        if len(S["pump"]) > 20000:
            S["pump"] = S["pump"][-20000:]
        rv = G.get("reverify")
        if rv is None and frame - S.get("last_verify", frame) >= 300:   # periodic re-check (review 4, L)
            out = {}
            th = threading.Thread(target=collect_evidence, args=(PORT, os.getpid(), t.sockname()[0], out),
                                  name="s6-reverify", daemon=True)
            G["reverify"] = (th, out)
            th.start()
        elif rv is not None and not rv[0].is_alive():
            G["reverify"] = None
            S["last_verify"] = frame
            S["reverifies"] = S.get("reverifies", 0) + 1
            if rv[1]["ev"].verdict is not bv.Verdict.PASS:
                log("reverify_failed", reasons=list(rv[1]["ev"].reasons))
                G["gate"].on_transport_failure()
                t.begin_close()
                S["phase"], S["close_reason"], S["close_started"] = "closing", "reverify_failed", now
                return
        if frame % 30 == 0:                          # main thread drains the in-memory audit ring
            S.setdefault("audit", []).extend(t.drain_audit())
        if S["suspend_until"] is not None and frame >= S["suspend_until"]:
            t.suspended, S["suspend_until"] = False, None    # simulates the user clearing Suspendagents
            log("resume")
        if not t.healthy():
            G["gate"].on_transport_failure()
            t.begin_close()
            S["phase"], S["close_reason"], S["close_started"] = "closing", "transport_failed", now
            return
        if S["action"] and frame >= S["action_at"]:
            act, S["action"] = S["action"], None
            begin_close(act)
        return
    if ph == "closing":
        t = G["t"]
        if t and not t.is_closed():
            return
        tab = oslisteners.read_listeners(PORT, os.getpid())
        v4, v6 = tab if tab is not None else (None, None)
        res = G["gate"].confirm_closed(v4, v6)
        if res == "recheck_close":
            return
        close_ms = round((now - S["close_started"]) * 1000, 1)
        S.setdefault("close_ms", []).append(close_ms)
        log("closed", result=res, close_ms=close_ms, state=G["gate"].state.value, stats=t.stats() if t else {})
        S.setdefault("audit", []).extend(t.drain_audit() if t else [])
        if S.get("close_reason") == "restart" and res == "closed":
            S["restarts"] += 1
            S["label"] = "after_restart"
            if not activate(me, user_initiated=True):       # the user's explicit re-activation
                finish_now(me, "restart_failed")
            return
        finish_now(me, S.get("close_reason", "?"))
        return
    if ph == "done":
        if frame >= S.get("quit_at", frame):
            S["phase"] = "quitting"
            write("results.json", results())
            td.project.quit(force=True)


def finish_now(me, reason):
    S["finish_reason"] = reason
    S["phase"], S["quit_at"] = "done", S["n"] + 5


def frame_stats():
    out = {}
    fr = S["frames"]
    for i in range(1, len(fr)):
        lab = fr[i][0]
        out.setdefault(lab, []).append((fr[i][1] - fr[i - 1][1]) * 1000)
    res = {}
    for lab, dts in out.items():
        s = sorted(dts)
        mean = sum(s) / len(s)
        res[lab] = {"n": len(s), "mean_ms": round(mean, 2), "p50_ms": round(s[len(s) // 2], 2),
                    "p95_ms": round(s[max(0, int(len(s) * 0.95) - 1)], 2), "max_ms": round(s[-1], 2),
                    "fps": round(1000 / mean, 2) if mean else None,
                    "over_25ms": sum(1 for x in s if x > 25), "over_100ms": sum(1 for x in s if x > 100),
                    "spikes_ms": [round(x) for x in s if x > 100][-8:]}
    pumps = {}
    for lab, n, dur in S["pump"]:
        p = pumps.setdefault(lab, {"frames": 0, "jobs": 0, "max_ms": 0.0, "total_ms": 0.0})
        p["frames"] += 1
        p["jobs"] += n
        p["total_ms"] += dur * 1000
        p["max_ms"] = max(p["max_ms"], dur * 1000)
    for p in pumps.values():
        p["mean_ms"] = round(p["total_ms"] / p["frames"], 4) if p["frames"] else None
        p["max_ms"] = round(p["max_ms"], 3)
        p["total_ms"] = round(p["total_ms"], 1)
    return res, pumps


def results():
    fs, pumps = frame_stats()
    audit = S.get("audit", [])
    codes = {}
    for a in audit:
        codes[f"{a['status']} {a['code']}"] = codes.get(f"{a['status']} {a['code']}", 0) + 1
    return {"mode": MODE, "build": S.get("build"), "switchinterval_s": S.get("switchinterval_s"), "finish_reason": S.get("finish_reason"),
            "gate_final": G["gate"].state.value if G["gate"] else None,
            "error_code": G["gate"].error_code if G["gate"] else None,
            "start_ms": S.get("start_ms"), "serve_ms": S.get("serve_ms"), "close_ms": S.get("close_ms"),
            "restarts": S["restarts"], "reverifies": S.get("reverifies", 0), "dispatch_threads": S["dispatch"], "touch": S["touch"],
            "frame_stats": fs, "pump_stats": pumps, "audit_count": len(audit), "audit_codes": codes,
            "audit_has_token": any(G["token"] and G["token"] in json.dumps(a) for a in audit),
            "audit_sample": audit[:5], "events": S["events"], "errors": S["errors"],
            "threads_alive_at_end": sorted(t.name for t in threading.enumerate())}
