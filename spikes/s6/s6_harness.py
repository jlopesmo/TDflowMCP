"""S6 spike harness (runs OUTSIDE TouchDesigner; disposable, not TDflowMCP source).

Launches TouchDesigner 2025.32440 with the disposable s6.toe only, tests the embedded Python
transport from outside, and kills only the process tree it started if it hangs.

Usage (from the run directory, e.g. .tmp/s6):  python -I s6_harness.py [main|conflict]
Results: results/harness_<mode>.json (contains host network details: keep git-ignored).
"""
import concurrent.futures as cf
import hashlib
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("TDFLOW_SRC", "")
sys.path.insert(0, SRC)
from tdflowmcp.net import oslisteners  # noqa: E402

TD_EXE = r"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\TouchDesigner.exe"
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]  # Edge headless made no request here
TOE = os.path.join(HERE, "build", "s6.toe")
PORT = 9480
RES = os.path.join(HERE, "results")
H = {"started": time.time(), "tests": [], "metrics": {}}
PROCS = []


def T(name, ok, **detail):
    H["tests"].append(dict(name=name, ok=bool(ok), **detail))
    return ok


def raw(data, host="127.0.0.1", timeout=10.0, shut=True):
    t = time.perf_counter()
    try:
        s = socket.create_connection((host, PORT), timeout=timeout)
    except OSError as e:
        return {"error": type(e).__name__, "ms": round((time.perf_counter() - t) * 1000, 2)}
    try:
        try:
            s.sendall(data)
            if shut:
                s.shutdown(socket.SHUT_WR)
        except OSError as e:                    # e.g. reset while the listener is closing
            return {"error": type(e).__name__, "ms": round((time.perf_counter() - t) * 1000, 2)}
        buf = b""
        while True:
            try:
                c = s.recv(65536)
            except (ConnectionResetError, ConnectionAbortedError) as e:
                if not buf:
                    return {"error": type(e).__name__, "ms": round((time.perf_counter() - t) * 1000, 2)}
                break
            except socket.timeout:
                return {"error": "timeout", "partial": len(buf), "ms": round((time.perf_counter() - t) * 1000, 2)}
            if not c:
                break
            buf += c
    finally:
        s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    hd = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hd.setdefault(k.strip().lower(), []).append(v.strip())
    try:
        st = int(lines[0].split()[1])
    except (IndexError, ValueError):
        st = 0
    return {"status": st, "headers": hd, "body": body, "ms": round((time.perf_counter() - t) * 1000, 2)}


def req(method="GET", path="/setup/", headers=None, body=b"", host_hdr=None, timeout=10.0):
    h = {"Host": host_hdr or f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    if body or method in ("POST", "PUT", "PATCH"):
        h["Content-Length"] = str(len(body))
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
    return raw(head.encode() + body, timeout=timeout)


def code(r):
    try:
        return json.loads(r.get("body") or b"{}").get("error")
    except (ValueError, AttributeError):
        return None


def rpc(token, method, params=None, timeout=15.0, extra=None):
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    h.update(extra or {})
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    r = req("POST", "/mcp", h, body, timeout=timeout)
    try:
        r["json"] = json.loads(r.get("body") or b"null")
    except ValueError:
        r["json"] = None
    return r


def slim(r):
    return {k: (v if k != "body" else (v[:200].decode("utf-8", "replace") if isinstance(v, bytes) else v))
            for k, v in r.items() if k != "headers"}


def lan_ipv4():
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127."):
                out.add(info[4][0])
    except OSError:
        pass
    return sorted(out)


def connect_result(addr, family=socket.AF_INET, timeout=1.0):
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((addr, PORT))
        return "connected"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


def wait_file(name, proc, seconds):
    p = os.path.join(RES, name)
    end = time.time() + seconds
    while time.time() < end:
        if os.path.exists(p):
            time.sleep(0.05)
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        if proc.poll() is not None:
            return None
        time.sleep(0.05)
    return None


def pct(xs, q):
    s = sorted(xs)
    return round(s[max(0, int(len(s) * q) - 1)], 2) if s else None


def lat(xs):
    return {"n": len(xs), "p50": round(statistics.median(xs), 2) if xs else None, "p95": pct(xs, 0.95),
            "max": round(max(xs), 2) if xs else None}


def launch(mode):
    for f in os.listdir(RES):
        if f.startswith(("ready", "results", "boot")):
            os.remove(os.path.join(RES, f))
    env = dict(os.environ, TDFLOW_S6_DIR=HERE, TDFLOW_SRC=SRC, TDFLOW_S6_MODE=mode, TOUCH_TEXT_CONSOLE="1")
    logf = open(os.path.join(RES, f"td_console_{mode}.log"), "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([TD_EXE, TOE], cwd=os.path.join(HERE, "build"), env=env, stdout=logf,
                            stderr=subprocess.STDOUT)
    H["td_pid"] = proc.pid
    PROCS.append(proc)
    return proc


def own_table(pid):
    tab = oslisteners.read_listeners(PORT, pid)
    return {"v4": tab[0], "v6": tab[1]} if tab else None


def wait_exit(proc, seconds=60):
    end = time.time() + seconds
    while time.time() < end and proc.poll() is None:
        time.sleep(0.2)
    if proc.poll() is None:
        H["killed"] = True
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=20)
    H["td_returncode"] = proc.returncode


def run_conflict():
    squat = socket.socket()
    squat.bind(("127.0.0.1", PORT))          # loopback only; never a wildcard address
    squat.listen(1)
    H["squatter"] = "127.0.0.1"
    proc = launch("conflict")
    seen_td_listener = []
    ready = None
    end = time.time() + 90
    while time.time() < end:
        t = own_table(proc.pid)
        if t and (t["v4"] or t["v6"]):
            seen_td_listener.append(t)
        if os.path.exists(os.path.join(RES, "ready.json")):
            ready = wait_file("ready.json", proc, 2)
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    T("conflict.reported", ready and ready.get("state") == "CLOSED" and ready.get("error_code") == "PORT_IN_USE",
      ready={k: ready.get(k) for k in ("state", "error_code")} if ready else None)
    T("conflict.td_never_listened", not seen_td_listener, samples=len(seen_td_listener))
    wait_exit(proc)
    squat.close()
    res = wait_file("results.json", proc, 1) or {}
    H["td_events"] = res.get("events")
    direct = [e for e in (res.get("events") or []) if e.get("kind") == "direct_bind"]
    T("conflict.direct_bind_refused", direct and direct[0].get("started") is False
      and direct[0].get("code") == "PORT_IN_USE", direct=direct)
    T("conflict.no_listener_after_exit", not (own_table(proc.pid) or {}).get("v4"))


def headless_dom():
    exe = next((b for b in BROWSERS if os.path.exists(b)), None)
    if not exe:
        return "ERROR no browser"
    H["browser"] = os.path.basename(exe)
    prof = os.path.join(HERE, "browser-profile")        # throwaway profile; the user's profile is untouched
    args = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking",
            "--disable-component-update", f"--user-data-dir={prof}", "--virtual-time-budget=5000",
            "--dump-dom", f"http://127.0.0.1:{PORT}/setup/"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return r.stdout
    except Exception as e:
        return f"ERROR {type(e).__name__}"


def run_main():
    proc = launch("main")
    early, t0 = [], time.time()
    while time.time() - t0 < 120 and not os.path.exists(os.path.join(RES, "ready.json")):
        r = req(timeout=0.5)
        early.append((r.get("status") or r.get("error"), time.time()))   # response arrival (wall clock)
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    ready = wait_file("ready.json", proc, 5)
    H["t_ready_s"] = round(time.time() - t0, 2)
    H["pre_ready"] = {"polls": len(early), "outcomes": sorted({str(x) for x, _ in early})}
    sw = (ready or {}).get("serving_wall") or 0
    early_200 = [round(w - sw, 4) for x, w in early if x == 200 and w < sw]
    T("startup.no_200_before_serving", ready is not None and not early_200, outcomes=H["pre_ready"]["outcomes"],
      ok_200_after_serving=sum(1 for x, w in early if x == 200 and w >= sw), early_200=early_200)
    if not ready or ready.get("state") != "SERVING":
        T("startup.serving", False, ready=ready)
        wait_exit(proc, 30)
        return
    T("startup.serving", True)
    tok, pid = ready["token"], ready["pid"]

    # --- binding ---------------------------------------------------------------------------
    tab = own_table(pid)
    T("bind.os_table_loopback_only", tab and tab["v4"] == ["127.0.0.1"] and tab["v6"] == [], table=tab)
    others = oslisteners.other_listeners(PORT, pid)
    T("bind.no_other_owner", others == [], others=len(others or []))
    lan = lan_ipv4()
    H["metrics"]["lan_addr_count"] = len(lan)
    lan_res = [connect_result(a) for a in lan]
    T("bind.lan_tcp_not_connected", lan and all(x != "connected" for x in lan_res), results=lan_res)
    lan_http = [raw(f"GET /setup/ HTTP/1.1\r\nHost: {a}:{PORT}\r\n\r\n".encode(), host=a, timeout=2.0) for a in lan]
    T("bind.lan_http_no_response", all("status" not in r for r in lan_http), results=[r.get("error") for r in lan_http])
    T("bind.ipv6_loopback_not_bound", connect_result("::1", socket.AF_INET6) != "connected")

    # --- static Setup Assistant from DATs ----------------------------------------------------
    for path, fname, ctype in (("/setup/", "index.html", "text/html"), ("/setup/app.css", "app.css", "text/css"),
                               ("/setup/app.js", "app.js", "text/javascript")):
        r = req("GET", path, {"Sec-Fetch-Site": "none"})
        with open(os.path.join(HERE, "assets", fname), "rb") as f:
            want = f.read()
        got = r.get("body") or b""
        T(f"static.{fname}", r.get("status") == 200 and (r["headers"].get("content-type") or [""])[0].startswith(ctype),
          status=r.get("status"), same_bytes=got == want,
          same_after_newline_normalisation=got.replace(b"\r\n", b"\n") == want.replace(b"\r\n", b"\n"),
          sha_served=hashlib.sha256(got).hexdigest()[:16], sha_asset=hashlib.sha256(want).hexdigest()[:16],
          ms=r.get("ms"))
    r = req("GET", "/setup/")
    hd = r.get("headers", {})
    T("static.security_headers", "content-security-policy" in hd and hd.get("x-frame-options") == ["DENY"]
      and not [k for k in hd if k.startswith("access-control")], headers=sorted(hd))
    dom = headless_dom()
    T("static.headless_browser_runs_js", 'data-state="SERVING"' in dom and 'data-main="true"' in dom,
      dom_excerpt=dom[dom.find("<dl"):dom.find("</dl>") + 5][:600] if "<dl" in dom else dom[:300])

    # --- gate matrix -------------------------------------------------------------------------
    o = f"http://127.0.0.1:{PORT}"
    cases = [
        ("gate.bad_host", req("GET", "/setup/", host_hdr=f"evil.example:{PORT}"), 403, "BAD_HOST"),
        ("gate.localhost_redirect", req("GET", "/setup/", host_hdr=f"localhost:{PORT}"), 308, "REDIRECT_CANONICAL"),
        ("gate.api_evil_origin", req("GET", "/setup/api/status", {"X-TDflow-Request": "1", "Origin": "http://evil.example"}), 403, "BAD_ORIGIN"),
        ("gate.api_other_port_origin", req("GET", "/setup/api/status", {"X-TDflow-Request": "1", "Origin": "http://127.0.0.1:5173"}), 403, "BAD_ORIGIN"),
        ("gate.api_missing_header", req("GET", "/setup/api/status"), 403, "MISSING_REQUEST_HEADER"),
        ("gate.api_cross_site", req("GET", "/setup/api/status", {"X-TDflow-Request": "1", "Sec-Fetch-Site": "cross-site"}), 403, "CROSS_SITE_FETCH"),
        ("gate.preflight", req("OPTIONS", "/mcp", {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}), 403, "PREFLIGHT_DENIED"),
        ("gate.traversal", req("GET", "/setup/../api/x"), 400, "BAD_PATH"),
        ("gate.encoded_traversal", req("GET", "/setup/%2e%2e/api/x"), 400, "BAD_PATH"),
        ("gate.mutation_no_origin", req("POST", "/setup/api/x", {"X-TDflow-CSRF": "x"}, b"{}"), 403, "BAD_ORIGIN"),
        ("gate.mutation_no_csrf", req("POST", "/setup/api/x", {"Origin": o}, b"{}"), 403, "MISSING_CSRF"),
        ("gate.mutation_no_admin_session", req("POST", "/setup/api/x", {"Origin": o, "X-TDflow-CSRF": "x"}, b"{}"), 401, "UNAUTHENTICATED"),
        ("gate.admin_read_denied", req("GET", "/api/agents", {"X-TDflow-Request": "1"}), 401, "UNAUTHENTICATED"),
        ("gate.unknown_route", req("GET", "/etc/passwd"), 404, "NOT_FOUND"),
        ("gate.trace", req("TRACE", "/setup/"), 501, "HTTP_501"),
        ("gate.malformed", raw(b"NONSENSE\r\n\r\n"), 400, "HTTP_400"),
        ("gate.body_too_large", raw(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (PORT, 8 << 20)), 413, "BODY_TOO_LARGE"),
        ("gate.chunked", raw(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n" % PORT), 400, "TRANSFER_ENCODING_UNSUPPORTED"),
        ("gate.dup_host", raw(b"GET /setup/ HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nHost: evil.example\r\n\r\n" % PORT), 400, "DUPLICATE_HEADER:host"),
        ("auth.no_token", req("POST", "/mcp", {"Content-Type": "application/json"}, b"{}"), 401, "UNAUTHENTICATED"),
        ("auth.wrong_token", rpc("x" * 43, "ping"), 401, "UNAUTHENTICATED"),
        ("auth.evil_origin", rpc(tok, "ping", extra={"Origin": "http://evil.example"}), 403, "BAD_ORIGIN"),
        ("auth.bad_content_type", req("POST", "/mcp", {"Content-Type": "text/plain", "Authorization": f"Bearer {tok}"}, b"{}"), 415, "UNSUPPORTED_MEDIA_TYPE"),
    ]
    for name, r, st, c in cases:
        T(name, r.get("status") == st and code(r) == c, status=r.get("status"), code=code(r))

    # --- authenticated MCP surface (no code execution) ----------------------------------------
    r = rpc(tok, "ping")
    T("mcp.ping", r.get("status") == 200 and r["json"].get("result") == {}, ms=r.get("ms"))
    r = rpc(tok, "tools/list")
    T("mcp.tools_list_empty", r.get("json", {}).get("result") == {"tools": []})
    for m in ("tools/call", "execute_python", "td/exec"):
        r = rpc(tok, m, {"name": "execute_python", "arguments": {"code": "import os"}})
        T(f"mcp.no_exec.{m}", (r.get("json") or {}).get("error", {}).get("code") == -32601)
    r = req("GET", "/.well-known/tdflowmcp")
    T("well_known", r.get("status") == 200 and b"python-http" in r.get("body", b""))
    T("well_known.no_project_details", set(json.loads(r.get("body") or b"{}")) <= {"product", "version", "transport"})
    r = rpc(tok, "s6/touch")
    T("td.operator_access_on_main_thread", (r.get("json") or {}).get("result", {}).get("is_main") is True
      and r["json"]["result"].get("exists_after") is False, result=(r.get("json") or {}).get("result"))
    r = req("GET", "/setup/api/status", {"X-TDflow-Request": "1"})
    st = json.loads(r.get("body") or b"{}")
    T("api.status_main_thread", r.get("status") == 200 and st.get("is_main") is True and st.get("gate") == "SERVING",
      build=st.get("build"), thread=st.get("thread"))

    # --- latency and load --------------------------------------------------------------------
    rpc(tok, "s6/mark", {"label": "idle"})
    time.sleep(10.0)                                     # server on, no requests: compare with baseline
    rpc(tok, "s6/mark", {"label": "seq"})
    seq_mcp = [rpc(tok, "ping") for _ in range(200)]
    seq_static = [req("GET", "/setup/app.css") for _ in range(200)]
    H["metrics"]["seq_mcp_ms"] = lat([r["ms"] for r in seq_mcp if r.get("status") == 200])
    H["metrics"]["seq_static_ms"] = lat([r["ms"] for r in seq_static if r.get("status") == 200])
    T("load.sequential_all_ok", all(r.get("status") == 200 for r in seq_mcp + seq_static),
      mcp=H["metrics"]["seq_mcp_ms"], static=H["metrics"]["seq_static_ms"])
    rpc(tok, "s6/mark", {"label": "load8"})
    t = time.perf_counter()
    with cf.ThreadPoolExecutor(8) as ex:
        par = list(ex.map(lambda _: rpc(tok, "ping"), range(400)))
    dur = time.perf_counter() - t
    sts = {}
    for r in par:
        sts[str(r.get("status") or r.get("error"))] = sts.get(str(r.get("status") or r.get("error")), 0) + 1
    H["metrics"]["load8"] = {"n": 400, "seconds": round(dur, 2), "rps": round(400 / dur, 1), "statuses": sts,
                             "lat": lat([r["ms"] for r in par if r.get("status") == 200])}
    T("load.concurrent8_all_ok", sts == {"200": 400}, **H["metrics"]["load8"])
    rpc(tok, "s6/mark", {"label": "burst48"})
    with cf.ThreadPoolExecutor(48) as ex:
        burst = list(ex.map(lambda _: rpc(tok, "ping"), range(480)))
    bst = {}
    for r in burst:
        k = f"{r.get('status')} {code(r) or ''}".strip() if r.get("status") else str(r.get("error"))
        bst[k] = bst.get(k, 0) + 1
    H["metrics"]["burst48"] = bst
    T("load.burst48_only_200_or_busy", set(bst) <= {"200", "503 TDFLOW_BUSY"}, statuses=bst)
    rpc(tok, "s6/mark", {"label": "after_load"})
    time.sleep(2.0)

    # --- timeouts ------------------------------------------------------------------------------
    rpc(tok, "s6/mark", {"label": "slow"})
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("slow", rpc(tok, "s6/slow", {"ms": 5000}, timeout=20)))
    th.start()
    time.sleep(0.4)
    out["ping_during_slow"] = rpc(tok, "ping", timeout=20)
    th.join(25)
    T("timeout.queued_job_cancelled", code(out["ping_during_slow"]) == "TDFLOW_TIMEOUT",
      **slim(out["ping_during_slow"]))
    T("timeout.after_dispatch", code(out["slow"]) == "TDFLOW_TIMEOUT_AFTER_DISPATCH", **slim(out["slow"]))
    time.sleep(1.5)
    rpc(tok, "s6/mark", {"label": "after_slow"})
    s = socket.create_connection(("127.0.0.1", PORT), timeout=20)
    s.sendall(b"GET /setup/ HTTP/1.1\r\n")
    t = time.perf_counter()
    try:
        data = s.recv(100)
    except (ConnectionResetError, ConnectionAbortedError, socket.timeout) as e:
        data = type(e).__name__.encode()
    s.close()
    T("timeout.slow_client_cut", time.perf_counter() - t < 8.0, seconds=round(time.perf_counter() - t, 2),
      got=data[:40].decode("latin-1"))

    # --- recovery ----------------------------------------------------------------------------
    r = rpc(tok, "s6/raise")
    T("recovery.dispatch_error_500", r.get("status") == 500 and code(r) == "INTERNAL_ERROR" and b"deliberate" not in r.get("body", b""))
    s = socket.create_connection(("127.0.0.1", PORT))
    s.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nAuthorization: Bearer %s\r\n"
              b"Content-Length: 1000\r\n\r\n{\"jsonrpc\"" % (PORT, tok.encode()))
    s.close()
    s = socket.create_connection(("127.0.0.1", PORT))
    s.close()
    time.sleep(0.3)
    T("recovery.after_errors_ping_ok", rpc(tok, "ping").get("status") == 200)

    # --- kill switch -------------------------------------------------------------------------
    rpc(tok, "s6/suspend", {"frames": 120})
    r1, r2 = rpc(tok, "ping"), req("GET", "/setup/")
    T("killswitch.mcp_suspended", r1.get("status") == 503 and code(r1) == "SUSPENDED")
    T("killswitch.setup_still_served", r2.get("status") == 200)
    end = time.time() + 10
    while time.time() < end and code(rpc(tok, "ping")) == "SUSPENDED":
        time.sleep(0.2)
    T("killswitch.cleared", rpc(tok, "ping").get("status") == 200)

    # --- port conflict inside TD ---------------------------------------------------------------
    r = rpc(tok, "s6/conflict")
    T("conflict.second_transport_refused", (r.get("json") or {}).get("result") == {"started": False, "code": "PORT_IN_USE"},
      result=(r.get("json") or {}).get("result"))
    T("conflict.first_still_serving", rpc(tok, "ping").get("status") == 200)

    # --- restart -----------------------------------------------------------------------------
    rpc(tok, "s6/restart")
    t = time.time()
    gap = []
    ready2 = None
    while time.time() - t < 30:
        p = os.path.join(RES, "ready_2.json")
        if os.path.exists(p):
            ready2 = wait_file("ready_2.json", proc, 2)
            break
        r = req("GET", "/setup/", timeout=0.5)
        gap.append(r.get("status") or r.get("error"))
        time.sleep(0.02)
    H["metrics"]["restart_s"] = round(time.time() - t, 2)
    H["metrics"]["restart_gap"] = sorted({str(g) for g in gap})
    T("restart.serving_again", ready2 is not None and rpc(tok, "ping").get("status") == 200,
      seconds=H["metrics"]["restart_s"], outcomes=H["metrics"]["restart_gap"])
    tab = own_table(pid)
    T("restart.loopback_only", tab and tab["v4"] == ["127.0.0.1"] and tab["v6"] == [], table=tab)
    T("restart.lan_not_connected", all(connect_result(a) != "connected" for a in lan))

    # --- finish ------------------------------------------------------------------------------
    rpc(tok, "s6/mark", {"label": "final"})
    time.sleep(1.0)
    rpc(tok, "s6/finish")
    wait_exit(proc, 60)
    T("shutdown.td_exit_code_0", proc.returncode == 0, code=proc.returncode)
    T("shutdown.no_listener_after_exit", oslisteners.other_listeners(PORT, 0) == [])
    res = wait_file("results.json", proc, 1) or {}
    H["td_results"] = res
    T("threads.no_off_main_dispatch", res.get("dispatch_threads", {}).get("other") == 0,
      dispatch=res.get("dispatch_threads"))
    T("audit.no_token_in_audit", res.get("audit_has_token") is False, audit_count=res.get("audit_count"))
    T("shutdown.closed_confirmed", res.get("gate_final") == "CLOSED", close_ms=res.get("close_ms"))
    T("td.no_runtime_errors", not res.get("errors"), errors=(res.get("errors") or [])[:2])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "main"
    os.makedirs(RES, exist_ok=True)
    pre = oslisteners.other_listeners(PORT, 0)
    try:
        if pre:
            H["abort"] = "port 9480 in use before launch"
        elif mode == "conflict":
            run_conflict()
        else:
            run_main()
    except Exception as e:
        H["harness_error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        for pr in PROCS:                        # never leave the TD we started running
            if pr.poll() is None:
                H["killed_in_finally"] = True
                subprocess.run(["taskkill", "/PID", str(pr.pid), "/T", "/F"], capture_output=True)
        for f in os.listdir(RES):               # the ready files carry the run's bearer token: never keep them
            if f.startswith("ready"):
                os.remove(os.path.join(RES, f))
        H["finished"] = time.time()
        with open(os.path.join(RES, f"harness_{mode}.json"), "w", encoding="utf-8") as f:
            json.dump(H, f, indent=2, default=str)
        ok = sum(t["ok"] for t in H["tests"])
        print(json.dumps({"mode": mode, "passed": ok, "total": len(H["tests"]), "abort": H.get("abort"),
                          "failed": [t["name"] for t in H["tests"] if not t["ok"]],
                          "td_returncode": H.get("td_returncode"), "killed": H.get("killed")}, indent=1))
