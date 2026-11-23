"""In-TouchDesigner integration tests for the packaged TDflowMCP.tox (S3). Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_tests.py [--tox build/TDflowMCP.tox] [--mode full|real_browser]

Launches TouchDesigner 2025.32440 with a generated disposable project that loads the .tox
(tests/td/td_runner.py), answers the runner's checkpoints with external network probes, and writes
build/td-test-results.json. Safety: aborts if any test port is in use; never touches TouchDesigner
processes it did not start; on timeout kills only its own process tree; uses 127.0.0.1 only (the LAN
address is only *connected to*, never bound).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import getpass
import hashlib
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402
from tdflowmcp.net import oslisteners  # noqa: E402

PORTS = (9480, 9481, 9482, 9483)

def safe_dir(path, root):
    """Only directories under <repo>/.tmp or <repo>/build may be wiped (review 5)."""
    p = os.path.abspath(path)
    allowed = [os.path.join(root, ".tmp"), os.path.join(root, "build")]
    ok = (p == allowed[1]) or any(os.path.commonpath([p, a]) == a and p != a for a in allowed)
    if not ok:
        raise SystemExit(f"refusing to use {p}: only paths under .tmp/ or build/ are allowed")
    return p

CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
H = {"tests": [], "metrics": {}, "bodies": []}


def T(name, ok, **detail):
    H["tests"].append(dict(name=name, ok=bool(ok), **detail))


# ---- HTTP helpers (raw sockets, loopback) -----------------------------------------------------
def raw(data: bytes, host="127.0.0.1", port=9480, timeout=10.0):
    t = time.perf_counter()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return {"error": type(e).__name__, "ms": round((time.perf_counter() - t) * 1000, 2)}
    try:
        try:
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
        except OSError as e:
            return {"error": type(e).__name__}
        buf = b""
        while True:
            try:
                c = s.recv(65536)
            except (ConnectionResetError, ConnectionAbortedError) as e:
                if not buf:
                    return {"error": type(e).__name__}
                break
            except socket.timeout:
                return {"error": "timeout"}
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
    H["bodies"].append(body[:20000])
    return {"status": st, "headers": hd, "body": body, "ms": round((time.perf_counter() - t) * 1000, 2)}


def req(method="GET", path="/setup/", headers=None, body=b"", host_hdr=None, port=9480, timeout=10.0):
    h = {"Host": host_hdr or f"127.0.0.1:{port}"}
    h.update(headers or {})
    if body or method in ("POST", "PUT", "PATCH"):
        h["Content-Length"] = str(len(body))
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
    return raw(head.encode() + body, port=port, timeout=timeout)


def code(r):
    try:
        return json.loads(r.get("body") or b"{}").get("error")
    except (ValueError, AttributeError):
        return None


def jbody(r):
    try:
        return json.loads(r.get("body") or b"null")
    except ValueError:
        return None


def lan_ipv4():
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127."):
                out.add(info[4][0])
    except OSError:
        pass
    return sorted(out)


def connect_result(addr, port=9480, family=socket.AF_INET, timeout=1.0):
    s = socket.socket(family, socket.SOCK_STREAM)
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


def td_listening(pid, ports=PORTS):
    out = {}
    for p in ports:
        tab = oslisteners.read_listeners(p, pid)
        out[p] = None if tab is None else {"v4": tab[0], "v6": tab[1]}
    return out


def no_listener(pid, prefix, ports=PORTS):
    tab = td_listening(pid, ports)
    T(f"{prefix}.no_td_listener", all(v is not None and not v["v4"] and not v["v6"] for v in tab.values()), table=tab)
    r = req(timeout=1.0)
    T(f"{prefix}.connect_refused_9480", "error" in r, outcome=r.get("error") or r.get("status"))


def read_src(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def headless_dom():
    exe = next((b for b in CHROME if os.path.exists(b)), None)
    if not exe:
        return "ERROR no browser"
    prof = os.path.join(H["run_dir"], "browser-profile")      # throwaway; the user's profile is untouched
    args = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking", "--disable-component-update",
            f"--user-data-dir={prof}", "--virtual-time-budget=6000", "--dump-dom", "http://127.0.0.1:9480/setup/"]
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=90).stdout
    except Exception as e:
        return f"ERROR {type(e).__name__}"


# ---- checkpoint handlers --------------------------------------------------------------------
def cp_serving(data, pid):
    tab = td_listening(pid, (9480,))[9480]
    T("bind.os_table_loopback_only", tab == {"v4": ["127.0.0.1"], "v6": []}, table=tab)
    T("bind.no_other_owner", oslisteners.other_listeners(9480, pid) == [])
    lan = lan_ipv4()
    res = [connect_result(a) for a in lan]
    T("bind.lan_not_connected", bool(lan) and all(x != "connected" for x in res), results=res, count=len(lan))
    T("bind.lan_http_no_response", all("status" not in raw(f"GET /setup/ HTTP/1.1\r\nHost: {a}:9480\r\n\r\n".encode(),
                                                            host=a, timeout=2.0) for a in lan))
    T("bind.ipv6_not_bound", connect_result("::1", family=socket.AF_INET6) != "connected")
    # Static assets embedded in the .tox equal the repository sources.
    for path, rel, ctype in (("/setup/", "src/setup/web/index.html", "text/html"),
                             ("/setup/app.css", "src/setup/web/app.css", "text/css"),
                             ("/setup/app.js", "src/setup/web/app.js", "text/javascript")):
        r = req("GET", path, {"Sec-Fetch-Site": "none"})
        T(f"static.{os.path.basename(rel)}", r.get("status") == 200 and r.get("body") == read_src(rel)
          and (r["headers"].get("content-type") or [""])[0].startswith(ctype), status=r.get("status"))
    r = req("GET", "/setup/")
    hd = r.get("headers", {})
    T("static.security_headers", "content-security-policy" in hd and hd.get("x-frame-options") == ["DENY"]
      and not [k for k in hd if k.startswith("access-control")] and hd.get("cache-control") == ["no-store"])
    r = req("GET", "/.well-known/tdflowmcp")
    wk = jbody(r) or {}
    T("well_known.minimal", r.get("status") == 200 and set(wk) == {"product", "version"} and wk["product"] == "TDflowMCP",
      body=wk)
    r = req("GET", "/setup/api/status", {"X-TDflow-Request": "1"})
    st = jbody(r) or {}
    T("api.status", r.get("status") == 200 and st.get("state") == "Setup" and st.get("mcp") == "disabled"
      and st.get("td_build") == "2025.32440" and len(st.get("checks", [])) >= 9 and "Tell Your Coding Agent" in st.get("tell_agent", ""),
      state=st.get("state"), summary=st.get("summary"), checks={c["id"]: c["result"] for c in st.get("checks", [])})
    T("api.checks_have_remediation_text", all(c["title"] and c["steps"] for c in st.get("checks", [])
                                               if c["result"] in ("warn", "fail")))
    r = req("GET", "/setup/api/tellagent", {"X-TDflow-Request": "1"})
    T("api.tellagent", r.get("status") == 200 and r.get("body", b"").decode("utf-8") == data["tell"])
    o = "http://127.0.0.1:9480"
    mcp = {"Content-Type": "application/json"}
    cases = [
        ("gate.bad_host", req(host_hdr="evil.example:9480"), 403, "BAD_HOST"),
        ("gate.localhost_redirect", req(host_hdr="localhost:9480"), 308, "REDIRECT_CANONICAL"),
        ("gate.api_missing_header", req("GET", "/setup/api/status"), 403, "MISSING_REQUEST_HEADER"),
        ("gate.api_evil_origin", req("GET", "/setup/api/status", {"X-TDflow-Request": "1", "Origin": "http://evil.example"}), 403, "BAD_ORIGIN"),
        ("gate.api_other_port_origin", req("GET", "/setup/api/status", {"X-TDflow-Request": "1", "Origin": "http://127.0.0.1:5173"}), 403, "BAD_ORIGIN"),
        ("gate.api_cross_site", req("GET", "/setup/api/status", {"X-TDflow-Request": "1", "Sec-Fetch-Site": "cross-site"}), 403, "CROSS_SITE_FETCH"),
        ("gate.preflight", req("OPTIONS", "/setup/api/status", {"Origin": "http://evil.example"}), 403, "PREFLIGHT_DENIED"),
        ("gate.traversal", req("GET", "/setup/../api/x"), 400, "BAD_PATH"),
        ("gate.mutation_no_csrf", req("POST", "/setup/api/x", {"Origin": o}, b"{}"), 403, "MISSING_CSRF"),
        ("gate.no_write_endpoints", req("POST", "/setup/api/x", {"Origin": o, "X-TDflow-CSRF": "x"}, b"{}"), 401, "UNAUTHENTICATED"),
        ("gate.admin_api_denied", req("GET", "/api/agents", {"X-TDflow-Request": "1"}), 401, "UNAUTHENTICATED"),
        ("gate.dashboard_absent", req("GET", "/ui/"), 404, "NOT_FOUND"),
        ("gate.unknown", req("GET", "/etc/passwd"), 404, "NOT_FOUND"),
        ("gate.body_too_large", raw(b"POST /setup/api/x HTTP/1.1\r\nHost: 127.0.0.1:9480\r\nOrigin: http://127.0.0.1:9480\r\nContent-Length: 9000000\r\n\r\n"), 413, "BODY_TOO_LARGE"),
        ("mcp.disabled", req("POST", "/mcp", mcp, b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'), 503, "MCP_DISABLED"),
        ("mcp.disabled_with_token", req("POST", "/mcp", dict(mcp, Authorization="Bearer " + "x" * 43),
                                        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"execute_python","arguments":{"code":"import os"}}}'), 503, "MCP_DISABLED"),
        ("mcp.disabled_evil_origin", req("POST", "/mcp", dict(mcp, Origin="http://evil.example"), b"{}"), 503, "MCP_DISABLED"),
        ("mcp.disabled_get", req("GET", "/mcp"), 503, "MCP_DISABLED"),
        ("mcp.disabled_preflight", req("OPTIONS", "/mcp", {"Origin": "http://evil.example"}), 503, "MCP_DISABLED"),
        ("mcp.bad_host_first", req("POST", "/mcp", mcp, b"{}", host_hdr="evil.example"), 403, "BAD_HOST"),
    ]
    for name, r, s, c in cases:
        T(name, r.get("status") == s and code(r) == c, status=r.get("status"), code=code(r))
    dom = headless_dom()
    T("browser.headless_renders_setup", 'data-loaded="true"' in dom and 'data-state="Setup"' in dom
      and dom.count("data-check=") >= 9 and "Tell Your Coding Agent" in dom and 'id="copy"' in dom,
      excerpt=dom[dom.find("<dl"):dom.find("</dl>") + 5][:800] if "<dl" in dom else dom[:300])
    lat = [req("GET", "/setup/api/status", {"X-TDflow-Request": "1"})["ms"] for _ in range(50)]
    with cf.ThreadPoolExecutor(8) as ex:
        par = list(ex.map(lambda _: req("GET", "/setup/app.css"), range(200)))
    sts = {}
    for r in par:
        sts[str(r.get("status") or r.get("error"))] = sts.get(str(r.get("status") or r.get("error")), 0) + 1
    H["metrics"]["status_api_ms"] = {"p50": round(statistics.median(lat), 2), "max": round(max(lat), 2)}
    H["metrics"]["static_8_clients"] = sts
    T("load.static_8_clients_ok", sts == {"200": 200}, statuses=sts)


STATE = {}


def cp_block_ready(data, pid):
    # The runner blocks its main thread for 3 s right after reading our answer; send the request
    # 0.5 s after answering so it is queued while the main thread is blocked.
    out = {}

    def later():
        time.sleep(0.5)
        out["r"] = req("GET", "/setup/api/status", {"X-TDflow-Request": "1"}, timeout=20)
    th = threading.Thread(target=later)
    th.start()
    STATE["block"] = (th, out)


def cp_block_done(data, pid):
    th, out = STATE.pop("block")
    th.join(20)
    r = out.get("r", {})
    T("timeout.queued_request_504", r.get("status") == 504 and code(r) == "TDFLOW_TIMEOUT", status=r.get("status"),
      code=code(r), ms=r.get("ms"))
    T("timeout.recovers", req("GET", "/setup/api/status", {"X-TDflow-Request": "1"}).get("status") == 200)


def cp_error_injected(data, pid):
    r1 = req("GET", "/setup/api/status", {"X-TDflow-Request": "1"})
    r2 = req("GET", "/setup/api/status", {"X-TDflow-Request": "1"})
    T("error.500_without_details", r1.get("status") == 500 and code(r1) == "INTERNAL_ERROR"
      and b"injected" not in r1.get("body", b""), status=r1.get("status"))
    T("error.recovers", r2.get("status") == 200)


def cp_need_squatter(data, pid):
    s = socket.socket()
    s.bind(("127.0.0.1", data["port"]))        # loopback only
    s.listen(1)
    STATE["squatter"] = s


def cp_conflict_checked(data, pid):
    tab = td_listening(pid, (9483,))[9483]
    T("conflict.td_never_listened", tab is not None and not tab["v4"] and not tab["v6"], table=tab)
    STATE.pop("squatter").close()


def cp_restarted(data, pid):
    tab = td_listening(pid, (9480,))[9480]
    T("restart.loopback_only", tab == {"v4": ["127.0.0.1"], "v6": []}, table=tab)
    T("restart.serves", req("GET", "/setup/api/status", {"X-TDflow-Request": "1"}).get("status") == 200)
    T("restart.lan_not_connected", all(connect_result(a) != "connected" for a in lan_ipv4()))


def secret_markers():
    m = {}
    for k in ("USERNAME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "COMPUTERNAME", "USERDOMAIN"):
        v = os.environ.get(k)
        if v and len(v) >= 3:
            m[k] = v
    try:
        m["user"] = getpass.getuser()
    except Exception:
        pass
    for i, a in enumerate(lan_ipv4()):
        m[f"lan{i}"] = a
    m["repo_path"] = ROOT
    return m


def scan(texts, label):
    marks = secret_markers()
    hits = []
    for name, text in texts.items():
        low = str(text).lower()
        for k, v in marks.items():
            if v.lower() in low:
                hits.append(f"{name}:{k}")
        low = low.replace("contains no secrets", "")
        import re as _re
        for pat in (r"bearer\s+[a-z0-9._\-]{16,}", r"token=\S", r"password\s*[=:]", r"secret=\S",
                    r"tdf_[a-z0-9]{12}\.[a-z0-9_\-]{20,}"):
            if _re.search(pat, low):
                hits.append(f"{name}:{pat[:12]}")
    T(f"secrets.{label}", not hits, hits=hits)


def cp_scan(data, pid):
    texts = {f"param:{k}": v for k, v in data["params"].items()}
    texts["tell_text"] = data["tell"]
    texts["tellagent_dat"] = data["tellagent_dat"]
    texts["diagnostics"] = json.dumps(data["diagnostics"])
    scan(texts, "params_instructions_diagnostics")
    scan({f"http{i}": b.decode("utf-8", "replace") for i, b in enumerate(H["bodies"])}, "http_responses")


def cp_final_serving(data, pid):
    tab = td_listening(pid, (9480,))[9480]
    T("reload.loopback_only", tab == {"v4": ["127.0.0.1"], "v6": []}, table=tab)


def cp_paused(data, pid):
    r = req("GET", "/setup/api/status", {"X-TDflow-Request": "1"}, timeout=5)
    T("paused_timeline.still_served", r.get("status") == 200, status=r.get("status"), code=code(r), ms=r.get("ms"))


def cp_verify_failed(data, pid):
    no_listener(pid, "verify_failed", (9480,))
    scan({"diag": json.dumps(data["diagnostics"]), "status": data["status"], "detail": data["detail"],
          "tell": data["tell"]}, "failure_path")


HANDLERS = {"serving": cp_serving, "paused": cp_paused, "verify_failed": cp_verify_failed, "block_ready": cp_block_ready, "block_done": cp_block_done,
            "error_injected": cp_error_injected, "need_squatter": cp_need_squatter,
            "conflict_checked": cp_conflict_checked, "restarted": cp_restarted, "scan": cp_scan,
            "final_serving": cp_final_serving}
for _n in ("loaded", "diagnosed", "port_changed", "stopped", "destroyed", "reinitialised", "idle_stopped"):
    HANDLERS[_n] = (lambda n: (lambda data, pid: no_listener(pid, n)))(_n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--run-dir", default=os.path.join(ROOT, ".tmp", "td-tests"))
    ap.add_argument("--mode", default="full", choices=("full", "real_browser"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-test-results.json"))
    a = ap.parse_args()
    run_dir = safe_dir(os.path.join(a.run_dir, a.mode), ROOT)
    H["run_dir"] = run_dir
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)
    tox = os.path.abspath(a.tox)
    with open(tox, "rb") as f:
        H["tox_sha256"] = hashlib.sha256(f.read()).hexdigest()
    meta_p = os.path.join(os.path.dirname(tox), "TDflowMCP.json")
    if os.path.exists(meta_p):
        H["tox_meta"] = {k: v for k, v in json.load(open(meta_p)).items() if k in ("version", "source_digest", "sha256")}
    busy = {p: oslisteners.other_listeners(p, 0) for p in PORTS}
    if any(busy.values()):
        H["abort"] = "a test port is in use before launch"
        return finish(a, None)
    shutil.copyfile(os.path.join(HERE, "td_runner.py"), os.path.join(run_dir, "td_runner.py"))
    toe = make_toe(run_dir, "td_tests", boot_script("td_runner", start_frame=20))
    # Open Setup opens the audit store: keep its private storage inside the disposable run folder too.
    td = TDProcess(toe, run_dir, env={"TDFLOW_TOX": tox, "TDFLOW_SRC_ROOT": ROOT, "TDFLOW_TEST_MODE": a.mode,
                                      "TDFLOW_PRIVATE_ROOT": os.path.join(run_dir, "private")})
    H["preexisting_td"] = len(td.preexisting)
    done = set()
    deadline = time.time() + 600
    try:
        while td.alive() and time.time() < deadline:
            if os.path.exists(os.path.join(run_dir, "td_results.json")):
                break
            for fn in sorted(os.listdir(run_dir)):
                if fn.startswith("cp_") and fn.endswith(".json") and fn not in done:
                    try:
                        with open(os.path.join(run_dir, fn), encoding="utf-8") as f:
                            cp = json.load(f)
                    except (PermissionError, ValueError):     # being written right now (Windows): retry
                        continue
                    name = cp["name"]
                    done.add(fn)
                    try:
                        HANDLERS.get(name, lambda d, p: None)(cp["data"], td.pid)
                    except Exception as e:
                        T(f"harness.{name}", False, error=f"{type(e).__name__}: {e}")
                    with open(os.path.join(run_dir, f"go_{name}.json.tmp"), "w", encoding="utf-8") as f:
                        json.dump({"ok": True}, f)
                    os.replace(os.path.join(run_dir, f"go_{name}.json.tmp"), os.path.join(run_dir, f"go_{name}.json"))
            time.sleep(0.05)
    finally:
        rc = td.finish(60)
        sq = STATE.pop("squatter", None)
        if sq:
            sq.close()
    H["td_returncode"], H["killed"] = rc, td.killed
    T("exit.code_0", rc == 0 and not td.killed, rc=rc, killed=td.killed)
    T("exit.no_listener_after_exit", all(not oslisteners.other_listeners(p, 0) for p in PORTS))
    T("exit.other_td_untouched", td.others_untouched(), preexisting=len(td.preexisting))
    log = os.path.join(run_dir, "td_console.log")
    if os.path.exists(log):
        scan({"td_console": open(log, encoding="utf-8", errors="replace").read()}, "td_console_log")
    err = os.path.join(run_dir, "boot_error.txt")
    T("td.no_boot_errors", not os.path.exists(err), error=open(err).read()[-1500:] if os.path.exists(err) else "")
    finish(a, td)


def finish(a, td):
    res_p = os.path.join(H["run_dir"], "td_results.json")
    tdres = json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {}
    H.pop("bodies", None)
    out = {"mode": a.mode, "tox_sha256": H.get("tox_sha256"), "tox_meta": H.get("tox_meta"),
           "td_build": tdres.get("td_build"), "abort": H.get("abort"),
           "in_td": {"passed": tdres.get("passed"), "total": tdres.get("total"), "tests": tdres.get("tests"),
                     "errors": tdres.get("errors"), "frame_stats": tdres.get("frame_stats"),
                     "destroy_seconds": tdres.get("destroy_seconds")},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"],
                       "metrics": H["metrics"]},
           "td_returncode": H.get("td_returncode")}
    total_ok = (not out["abort"] and tdres.get("passed") == tdres.get("total") and not tdres.get("errors")
                and out["harness"]["passed"] == out["harness"]["total"])
    out["result"] = "pass" if total_ok else "fail"
    out_p = a.out if a.mode == "full" else a.out.replace(".json", f"-{a.mode}.json")
    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    with open(out_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    failed = [t["name"] for t in (tdres.get("tests") or []) if not t["ok"]] + [t["name"] for t in H["tests"] if not t["ok"]]
    print(json.dumps({"result": out["result"], "in_td": f"{tdres.get('passed')}/{tdres.get('total')}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}", "failed": failed,
                      "errors": (tdres.get("errors") or [])[:2], "abort": out["abort"], "rc": H.get("td_returncode")},
                     indent=1))


if __name__ == "__main__":
    main()
