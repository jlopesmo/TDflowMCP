"""Audit-worker gate (ADR-0006 revision) + project-local storage, in TouchDesigner. Runs OUTSIDE TouchDesigner.

    python -I tests/td/run_td_gate_tests.py [--tox build/TDflowMCP.tox] [--baseline <Phase 1B .tox>]

Launches (disposable projects under .tmp/td-gate-tests/ only; private storage there too, via
TDFLOW_PRIVATE_ROOT, so nothing is written to the user's real profile):
1. ``gate``       storage decision, pairing, frame impact idle/load/slow disk, kill-switch race, overflow,
                  injected outcome failure.
2. ``gate_crash`` same .toe path, load from 8 clients, then a hard kill of that TouchDesigner process.
3. ``gate_restart`` crash recovery checks, then move/rename, copy and data-location flows.
4. ``perf``       the same load for the baseline (Phase 1B) build and for this build.
Results: build/td-gate-test-results.json. Only processes this harness started are ever killed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_td_mcp_tests as mt  # noqa: E402

base, T, H, MCP, TOKENS = mt.base, mt.T, mt.H, mt.MCP, mt.TOKENS
ROOT = base.ROOT
from tdtoe import boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402
from tdflowmcp.net import oslisteners  # noqa: E402

STATE = {}


def stats(xs):
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    q = lambda f: round(s[min(len(s) - 1, int(f * len(s)))], 2)
    return {"n": len(s), "p50_ms": q(.5), "p95_ms": q(.95), "p99_ms": q(.99), "max_ms": round(s[-1], 2),
            "mean_ms": round(sum(s) / len(s), 2)}


def timed(cli, name, args):
    t0 = time.perf_counter()
    r = cli.call(name, args)
    return (time.perf_counter() - t0) * 1000, r


def load(threads=8, per=30, seq=30, tag="load"):
    """Sequential calls (one client), then ``threads`` concurrent clients (writers + readers)."""
    B = MCP(TOKENS["B"])
    seq_lat, errors, ping_lat, list_lat = [], [], [], []
    for i in range(seq):                       # transport-only requests: no audit, no main thread (B6)
        t0 = time.perf_counter()
        B.rpc("ping")
        ping_lat.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        B.rpc("tools/list")
        list_lat.append((time.perf_counter() - t0) * 1000)
    for i in range(seq):
        ms, r = timed(B, "tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": (i % 50) / 100}})
        seq_lat.append(ms)
        if not mt.ok(r):
            errors.append(r.get("code") or r.get("_code"))
    conc, lock = [], threading.Lock()

    def worker(k):
        cli = MCP(TOKENS["B"] if k % 2 == 0 else TOKENS["C"])
        for i in range(per):
            if k % 2 == 0:
                ms, r = timed(cli, "tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                             "values": {"opacity": ((k * per + i) % 90) / 100}})
            else:
                ms, r = timed(cli, "tdflow_get_operator", {"path": "/project1/fx/level1"})
            with lock:
                conc.append(ms)
                if not mt.ok(r):
                    errors.append(r.get("code") or r.get("_code"))
    t0 = time.perf_counter()
    ths = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(300)
    wall = time.perf_counter() - t0
    out = {"sequential": stats(seq_lat), "concurrent": stats(conc), "clients": threads, "calls": len(conc),
           "ping": stats(ping_lat), "tools_list": stats(list_lat),
           "throughput_calls_s": round(len(conc) / wall, 1) if wall else None, "errors": len(errors),
           "error_codes": sorted(set(map(str, errors)))[:5]}
    T(f"{tag}.all_calls_succeeded", not errors, **{k: out[k] for k in ("errors", "error_codes")})
    return out


# ---- checkpoint handlers --------------------------------------------------------------------------
def cp_load(data, pid):
    return load()


def cp_load_slow(data, pid):
    return load(threads=8, per=10, seq=10, tag="load_slow")


def cp_race(data, pid):
    out = {}

    def go():
        out["r"] = MCP(TOKENS["B"]).call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                                   "values": {"opacity": 0.42}})
    th = threading.Thread(target=go)
    th.start()
    STATE["race"] = (th, out)
    return {}


def cp_race_checked(data, pid):
    th, out = STATE.pop("race")
    th.join(30)
    r = out.get("r") or {}
    T("race.call_reported_suspended", r.get("code") == "SUSPENDED" and r.get("_isError") is True, result=r)
    return {}


def cp_overflow(data, pid):
    res, lock = [], threading.Lock()

    def go(i):
        r = MCP(TOKENS["B"]).call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                            "values": {"opacity": 0.5 + i / 100}})
        with lock:
            res.append(r)
    ths = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(60)
    codes = ["OK" if mt.ok(r) else (r.get("code") or r.get("_code")) for r in res]
    T("overflow.no_success_without_durable_outcome", all(r.get("audit_seq") for r in res if mt.ok(r)))
    return {"codes": codes, "ok_seqs": [r["audit_seq"] for r in res if mt.ok(r)]}


def cp_outcome_fail(data, pid):
    B = MCP(TOKENS["B"])
    r = B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.77}})
    time.sleep(0.6)
    st, j, raw = B.rpc("tools/call", {"name": "tdflow_whoami", "arguments": {}})
    after = base.code(raw) if st != 200 else ((j or {}).get("result", {}).get("structuredContent") or {}).get("code")
    T("outcome_fail.not_reported_as_success", r.get("_isError") is True and r.get("code") == "OUTCOME_UNRECORDED", result=r)
    return {"code": r.get("code"), "after": after}


def cp_crash_load(data, pid):
    """8 clients hammer the component; after 2 s this harness hard-kills ITS OWN TouchDesigner process."""
    stop, ok_seqs, lock = threading.Event(), [], threading.Lock()

    def worker(k):
        cli = MCP(TOKENS["B"])
        i = 0
        while not stop.is_set():
            i += 1
            try:
                r = cli.call("tdflow_set_parameters", {"path": "/project1/fx/level1",
                                                       "values": {"opacity": ((k * 997 + i) % 90) / 100}})
            except Exception:
                continue
            if mt.ok(r) and r.get("audit_seq"):
                with lock:
                    ok_seqs.append(r["audit_seq"])
    ths = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(8)]
    for t in ths:
        t.start()
    time.sleep(2.0)
    with lock:
        before_kill = len(ok_seqs)
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)   # own child only
    STATE["killed_pid"] = pid
    time.sleep(1.0)
    stop.set()
    for t in ths:
        t.join(15)
    STATE["crash_ok_seqs"] = sorted(set(ok_seqs))
    T("crash.load_reached_component", before_kill > 0, ok_before_kill=before_kill)
    return {}


def cp_restart_ready(data, pid):
    return {"ok_seqs": STATE.get("crash_ok_seqs", []), "unmatched_before": STATE.get("unmatched_before", 0)}


def cp_after_crash_call(data, pid):
    B = MCP(TOKENS["B"])
    T("crash.calls_work_after_restart", mt.ok(B.call("tdflow_whoami")) and
      mt.ok(B.call("tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"opacity": 0.3}})))
    return {}


def cp_after_move(data, pid):
    B = MCP(TOKENS["B"])
    w = B.call("tdflow_whoami")
    T("move.agents_and_tokens_kept", mt.ok(w) and w.get("display_name", "").startswith("Agent B"), result=w)
    T("move.calls_audited_at_new_location", mt.ok(B.call("tdflow_set_parameters", {
        "path": "/project1/fx/level1", "values": {"opacity": 0.2}})))
    return {}


GATE = {"setup_ready": mt.cp_setup_ready, "pair_approved": mt.cp_pair_approved, "load": cp_load,
        "load_slow": cp_load_slow, "race": cp_race, "race_checked": cp_race_checked, "overflow": cp_overflow,
        "outcome_fail": cp_outcome_fail, "gate_done": lambda d, p: {}, "crash_load": cp_crash_load,
        "restart_ready": cp_restart_ready, "after_crash_call": cp_after_crash_call, "after_move": cp_after_move}
PERF = {"setup_ready": mt.cp_setup_ready, "pair_approved": mt.cp_pair_approved, "load": cp_load}


def launch(mode, tox, run_dir, proj_dir, env, handlers, expect_kill=False, deadline_s=900):
    for f in ("td_runner.py", "td_runner_mcp.py", "td_runner_gate.py"):
        shutil.copyfile(os.path.join(HERE, f), os.path.join(run_dir, f))
    H["run_dir"] = run_dir
    toe = make_toe(proj_dir, "td_tests", boot_script("td_runner", start_frame=20))
    td = TDProcess(toe, run_dir, env=dict({"TDFLOW_TOX": tox, "TDFLOW_SRC_ROOT": ROOT, "TDFLOW_TEST_MODE": mode}, **env))
    done, deadline = set(), time.time() + deadline_s
    try:
        while td.alive() and time.time() < deadline:
            if os.path.exists(os.path.join(run_dir, "td_results.json")):
                break
            for fn in sorted(os.listdir(run_dir)):
                if fn.startswith("cp_") and fn.endswith(".json") and fn not in done:
                    try:
                        with open(os.path.join(run_dir, fn), encoding="utf-8") as f:
                            cp = json.load(f)
                    except (PermissionError, ValueError):
                        continue
                    done.add(fn)
                    extra = {}
                    try:
                        extra = handlers.get(cp["name"], lambda d, p: None)(cp["data"], td.pid) or {}
                    except Exception as e:
                        T(f"harness.{mode}.{cp['name']}", False, error=f"{type(e).__name__}: {e}")
                    if not td.alive():
                        break
                    tmp = os.path.join(run_dir, f"go_{cp['name']}.json.tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(dict({"ok": True}, **extra), f)
                    os.replace(tmp, os.path.join(run_dir, f"go_{cp['name']}.json"))
            time.sleep(0.05)
    finally:
        rc = td.finish(90)
    if expect_kill:
        T(f"{mode}.killed_by_harness", STATE.get("killed_pid") == td.pid and rc != 0, rc=rc)
    else:
        T(f"{mode}.exit_code_0", rc == 0 and not td.killed, rc=rc, killed=td.killed)
    T(f"{mode}.no_listener_after_exit", all(not oslisteners.other_listeners(p, 0) for p in base.PORTS))
    T(f"{mode}.other_td_untouched", td.others_untouched())
    err = os.path.join(run_dir, "boot_error.txt")
    T(f"{mode}.no_boot_errors", not os.path.exists(err), error=open(err).read()[-1500:] if os.path.exists(err) else "")
    res_p = os.path.join(run_dir, "td_results.json")
    return json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tox", default=os.path.join(ROOT, "build", "TDflowMCP.tox"))
    ap.add_argument("--baseline", default=os.path.join(ROOT, ".tmp", "wt-e15ec55", "build", "TDflowMCP.tox"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "td-gate-test-results.json"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    top = base.safe_dir(os.path.join(ROOT, ".tmp", "td-gate-tests"), ROOT)
    if os.path.exists(top):
        shutil.rmtree(top)
    os.makedirs(top)
    if any(oslisteners.other_listeners(p, 0) for p in base.PORTS):
        print(json.dumps({"abort": "a test port is in use"}))
        return
    tox, bl = os.path.abspath(a.tox), os.path.abspath(a.baseline)
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
    results = {}
    proj = os.path.join(top, "gate-project")                 # the same .toe path for launches 1-3
    priv = os.path.join(top, "private")
    os.makedirs(proj)
    os.makedirs(os.path.join(top, "bench-project"))
    gate_env = {"TDFLOW_PRIVATE_ROOT": priv}
    modes = [m for m in ("gate", "gate_crash", "gate_restart", "perf_baseline", "perf_worker", "gate_bench")
             if not a.only or m in a.only.split(",")]
    for mode in modes:
        run_dir = os.path.join(top, mode)
        os.makedirs(run_dir)
        if mode == "gate_bench":
            results[mode] = launch(mode, tox, run_dir, os.path.join(top, "bench-project"),
                                   {"TDFLOW_DATADIR": os.path.join(top, "bench-datadir")}, {})
            continue
        if mode.startswith("perf"):
            pdir = os.path.join(top, mode + "-project")
            os.makedirs(pdir)
            dd = os.path.join(top, mode + "-datadir")
            os.makedirs(dd)
            mt.STATE["cookies"], mt.STATE["csrf"] = {}, None
            results[mode] = launch("perf", bl if mode == "perf_baseline" else tox, run_dir, pdir,
                                   {"TDFLOW_DATADIR": dd, "TDFLOW_PRIVATE_ROOT": os.path.join(top, mode + "-private")},
                                   PERF)
        else:
            mt.STATE["cookies"] = {} if mode != "gate" else mt.STATE["cookies"]
            results[mode] = launch(mode, tox, run_dir, proj, gate_env, GATE, expect_kill=(mode == "gate_crash"))
            if mode == "gate":
                STATE["unmatched_before"] = results[mode].get("unmatched_after_gate", 0)
                STATE["gate_tokens"] = dict(TOKENS)
    # Nothing sensitive in the project folder: no key, no credentials, no token secret.
    secrets_ = [t.split(".", 1)[1].encode() for t in STATE.get("gate_tokens", TOKENS).values() if t]
    proj_files, blob = [], b""
    for d, _, fs in os.walk(proj):
        for f in fs:
            if ".tdflowmcp" in d:
                proj_files.append(os.path.relpath(os.path.join(d, f), proj))
                with open(os.path.join(d, f), "rb") as fh:
                    blob += fh.read()
    T("storage.project_folder_never_holds_keys_or_credentials", proj_files and not any(
        os.path.basename(f) in ("audit.key", "credentials.sqlite", "checkpoints.jsonl") for f in proj_files),
      files=sorted(proj_files))
    T("storage.no_token_secret_in_project_folder", bool(blob) and not any(s in blob for s in secrets_))
    T("storage.no_token_digest_tables_in_project_db", b"CREATE TABLE tokens" not in blob)
    in_td = [dict(t, mode=m) for m, r in results.items() for t in (r.get("tests") or [])]
    errs = [f"{m}: {e}" for m, r in results.items() for e in (r.get("errors") or [])]
    extra = {m: {k: v for k, v in r.items() if k in ("onframe", "latency", "perf", "overflow", "crash", "storage",
                                                     "worker_stats", "frame_stats", "frame_stats_onframe",
                                                     "after_crash", "before_crash", "relocation", "unmatched_after_gate", "bench")}
             for m, r in results.items()}
    out = {"tox_sha256": sha(tox), "baseline_tox_sha256": sha(bl), "td_build": results.get("gate", {}).get("td_build"),
           "in_td": {"passed": sum(t["ok"] for t in in_td), "total": len(in_td), "tests": in_td, "errors": errs},
           "harness": {"passed": sum(t["ok"] for t in H["tests"]), "total": len(H["tests"]), "tests": H["tests"]},
           "measurements": extra}
    out["result"] = "pass" if (in_td and out["in_td"]["passed"] == out["in_td"]["total"] and not errs and
                               out["harness"]["passed"] == out["harness"]["total"]) else "fail"
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, default=str)
    failed = [f"{t.get('mode', 'harness')}:{t['name']}" for t in in_td + H["tests"] if not t["ok"]]
    print(json.dumps({"result": out["result"], "in_td": f"{out['in_td']['passed']}/{out['in_td']['total']}",
                      "harness": f"{out['harness']['passed']}/{out['harness']['total']}", "failed": failed[:40],
                      "errors": errs[:3]}, indent=1))


if __name__ == "__main__":
    main()
