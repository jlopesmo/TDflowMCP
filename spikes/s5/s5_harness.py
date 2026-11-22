"""S5 spike harness (runs OUTSIDE TouchDesigner; disposable, not TDflowMCP source).

Launches TouchDesigner 2025.32440 with the disposable s5.toe only, probes the Web Server DAT
from outside, and kills only the process it started if it hangs.
Usage: python -I s5_harness.py
"""
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

S5 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S5)
import s5_bindcheck  # noqa: E402

TD_EXE = r"C:\Program Files\Derivative\TouchDesigner.2025.32440\bin\TouchDesigner.exe"
TOE = os.path.join(S5, "build", "s5.toe")
PORT = 9480
BASE = f"http://127.0.0.1:{PORT}"
H = {"started": time.time(), "probes": {}}


def http(url, method="GET", headers=None, body=None, timeout=2.0):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "headers": dict(r.headers), "body": r.read()[:4000].decode("utf-8", "replace"),
                    "ms": round((time.perf_counter() - t) * 1000, 2)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "headers": dict(e.headers), "body": e.read()[:2000].decode("utf-8", "replace"),
                "ms": round((time.perf_counter() - t) * 1000, 2)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "ms": round((time.perf_counter() - t) * 1000, 2)}


def netstat_9480():
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    v6 = subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True).stdout
    return [l.strip() for l in (out + v6).splitlines() if f":{PORT} " in l and "LISTEN" in l]


def main():
    os.makedirs(os.path.join(S5, "results"), exist_ok=True)
    for f in ("ready.json", "results.json", "phase_a.json", "phase_b.json", "boot_started.txt", "boot_error.txt"):
        p = os.path.join(S5, "results", f)
        if os.path.exists(p):
            os.remove(p)
    H["pre_listeners"] = s5_bindcheck.listeners(PORT)
    if H["pre_listeners"]["v4"] or H["pre_listeners"]["v6"]:
        H["abort"] = "port 9480 in use before launch"
        return
    env = dict(os.environ, TDFLOW_S5_DIR=S5, TOUCH_TEXT_CONSOLE="1")
    if os.environ.get("TDFLOW_S5_NO_WS"):
        env["TDFLOW_S5_NO_WS"] = "1"
    logf = open(os.path.join(S5, "results", "td_console.log"), "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([TD_EXE, TOE], cwd=os.path.join(S5, "build"), env=env, stdout=logf, stderr=subprocess.STDOUT)
    H["td_pid"] = proc.pid
    t0 = time.time()
    early = []
    ready = None
    while time.time() - t0 < 90:
        r = http(BASE + "/s5/ping", timeout=0.3)
        early.append({"t": round(time.time() - t0, 3), "status": r.get("status"), "error": r.get("error", "")[:40]})
        rp = os.path.join(S5, "results", "ready.json")
        if os.path.exists(rp) or os.path.exists(os.path.join(S5, "results", "results.json")):
            time.sleep(0.2)
            ready = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {"gate": "n/a (params-only)"}
            break
        if proc.poll() is not None:
            H["td_exited_early"] = proc.returncode
            break
        time.sleep(0.1)
    H["pre_ready_polls"] = {
        "count": len(early),
        "statuses": sorted({str(e["status"]) for e in early}),
        "any_200_before_ready": any(e["status"] == 200 for e in early),
        "first": early[:3], "last": early[-3:],
    }
    H["ready"] = ready
    H["t_ready"] = round(time.time() - t0, 2)
    if ready and ready.get("gate") == "SERVING":
        P = H["probes"]
        P["netstat_listening"] = netstat_9480()
        P["ping"] = http(BASE + "/s5/ping")
        lan = s5_bindcheck.local_non_loopback_ipv4()
        P["lan_count"] = len(lan)
        P["lan"] = [http(f"http://{a}:{PORT}/s5/ping", timeout=1.5) for a in lan]
        try:
            s = socket.create_connection(("::1", PORT), timeout=1.0)
            s.close()
            P["ipv6_loopback_connect"] = "connected"
        except Exception as e:
            P["ipv6_loopback_connect"] = type(e).__name__
        P["echo_no_origin"] = http(BASE + "/s5/echo?case=none", headers={"X-TDflow-Request": "1"})
        P["echo_evil_origin"] = http(BASE + "/s5/echo?case=evil", headers={"Origin": "http://evil.example", "X-Custom-Probe": "abc"})
        P["echo_other_port_origin"] = http(BASE + "/s5/echo?case=otherport", headers={"Origin": "http://127.0.0.1:5173"})
        P["echo_evil_host"] = http(BASE + "/s5/echo?case=host", headers={"Host": "evil.example:9480"})
        P["status403"] = http(BASE + "/s5/status403")
        for size in (1024, 1024 * 1024, 8 * 1024 * 1024):
            P[f"post_{size}"] = http(BASE + "/s5/echo?case=size", method="POST", body=b"x" * size,
                                     headers={"Content-Type": "application/octet-stream"}, timeout=15)
            if "body" in P[f"post_{size}"]:
                P[f"post_{size}"]["body"] = P[f"post_{size}"]["body"][-120:]
        lat = []
        for _ in range(50):
            r = http(BASE + "/s5/ping")
            if r.get("status") == 200:
                lat.append(r["ms"])
        P["latency_ms"] = {"n": len(lat), "p50": statistics.median(lat) if lat else None,
                           "p95": sorted(lat)[int(len(lat) * 0.95) - 1] if lat else None, "max": max(lat) if lat else None}
        deadline = time.time() + 25
        st = None
        while time.time() < deadline:
            st = http(BASE + "/s5/state")
            try:
                b = json.loads(st.get("body", "{}"))
                if len(b.get("browser", [])) >= 3:
                    break
            except Exception:
                pass
            time.sleep(1)
        P["state_before_done"] = st
        P["done"] = http(BASE + "/s5/done", method="POST", body=b"{}")
    # wait for TD to finish and exit
    deadline = time.time() + 90
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.5)
    if proc.poll() is None:
        H["window_before_kill"] = subprocess.run(["tasklist", "/v", "/fo", "csv", "/fi", f"PID eq {proc.pid}"], capture_output=True, text=True).stdout[-600:]
        H["killed"] = True
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=20)
    H["td_returncode"] = proc.returncode
    H["post_exit_listeners"] = s5_bindcheck.listeners(PORT)
    H["post_exit_netstat"] = netstat_9480()
    H["finished"] = time.time()


if __name__ == "__main__":
    try:
        main()
    finally:
        with open(os.path.join(S5, "results", "harness.json"), "w", encoding="utf-8") as f:
            json.dump(H, f, indent=2, default=str)
        print(json.dumps({k: H.get(k) for k in ("td_pid", "t_ready", "ready", "killed", "td_returncode", "abort",
                                                  "td_exited_early")}, default=str))
