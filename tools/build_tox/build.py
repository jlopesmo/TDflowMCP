"""Build TDflowMCP.tox from src/ (ADR-0007, spike S2). Runs OUTSIDE TouchDesigner (CPython 3.11).

    python -I tools/build_tox/build.py [--out build] [--check-reproducible]

1. Reads src/component/manifest.json and every source file it lists (LF-normalised).
2. Writes a build plan (texts + SHA-256 + parameters) and a disposable builder .toe (tools/tdtoe.py).
3. Launches TouchDesigner 2025.32440 with that file only; builder_runtime.py constructs the COMP,
   verifies the embedded texts, saves <out>/TDflowMCP.tox and quits.
4. Writes <out>/TDflowMCP.json: version, source digest, TD build used, tox SHA-256, build time, commit.
``--check-reproducible`` builds a second time and compares tox bytes and expanded contents.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from tdtoe import EXPAND, boot_script, make_toe  # noqa: E402
from tdrun import TDProcess  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def safe_dir(path, root):
    """Only directories under <repo>/.tmp or <repo>/build may be wiped (review 5)."""
    p = os.path.abspath(path)
    allowed = [os.path.join(root, ".tmp"), os.path.join(root, "build")]
    ok = (p == allowed[1]) or any(os.path.commonpath([p, a]) == a and p != a for a in allowed)
    if not ok:
        raise SystemExit(f"refusing to use {p}: only paths under .tmp/ or build/ are allowed")
    return p


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan(out_tox: str) -> dict:
    m = json.loads(read("src/component/manifest.json"))
    version = re.search(r'__version__\s*=\s*"([^"]+)"', read(m["version_from"])).group(1)
    tool_surface = re.search(r'TOOL_SURFACE_VERSION\s*=\s*"([^"]+)"', read(m["version_from"])).group(1)
    modules = {}
    for name, info in m["modules"].items():
        text = read(info["path"])
        modules[name] = {"dat": "m_" + name.replace(".", "_"), "path": info["path"], "sha256": sha(text),
                         "package": bool(info.get("package")), "text": text}
    dats = {}
    for name, info in m["dats"].items():
        text = read(info["path"]) if "path" in info else info.get("text", "")
        dats[name] = {k: v for k, v in info.items() if k not in ("text",)}
        dats[name].update(text=text, sha256=sha(text))
    callbacks = {}
    for name, info in m["callbacks"].items():
        text = read(info["path"])
        callbacks[name] = dict(info, text=text, sha256=sha(text))
    params = json.loads(json.dumps(m["parameters"]).replace("{version}", f"{version} (tools {tool_surface})"))
    parts = sorted([("module", n, v["sha256"]) for n, v in modules.items()] +
                   [("dat", n, v["sha256"]) for n, v in dats.items() if n != "tellagent"] +
                   [("callback", n, v["sha256"]) for n, v in callbacks.items()] +
                   [("parameters", "", sha(json.dumps(params, sort_keys=True)))])
    source_digest = sha(json.dumps(parts))
    embedded = {
        "schema": "tdflowmcp.embedded/1", "name": m["name"], "version": version, "tool_surface": tool_surface,
        "min_td_build": m["min_td_build"], "source_digest": source_digest,
        "modules": {n: {"dat": v["dat"], "sha256": v["sha256"], "package": v["package"]} for n, v in modules.items()},
        "dats": {n: {k: v for k, v in d.items() if k in ("serve", "content_type", "sha256", "path")}
                 for n, d in dats.items()},
        "callbacks": {n: {"type": c["type"], "sha256": c["sha256"]} for n, c in callbacks.items()},
    }
    return {"name": m["name"], "version": version, "source_digest": source_digest, "out": out_tox,
            "tags": m["tags"], "modules": modules, "dats": dats, "callbacks": callbacks,
            "extension": m["extension"], "parameters": params, "embedded_manifest": embedded}


def build_once(out_dir: str, work: str) -> dict:
    out_dir, work = safe_dir(out_dir, ROOT), safe_dir(work, ROOT)
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    out_tox = os.path.join(out_dir, "TDflowMCP.tox")
    if os.path.exists(out_tox):
        os.remove(out_tox)
    p = plan(out_tox)
    with open(os.path.join(work, "build_plan.json"), "w", encoding="utf-8") as f:
        json.dump(p, f)
    shutil.copyfile(os.path.join(HERE, "builder_runtime.py"), os.path.join(work, "builder_runtime.py"))
    toe = make_toe(work, "builder", boot_script("builder_runtime", start_frame=10))
    td = TDProcess(toe, work, env={"TDFLOW_BUILD_PLAN": os.path.join(work, "build_plan.json")})
    res = td.wait_file("build_result.json", 180)
    rc = td.finish(60)
    if not res or not res.get("ok") or not os.path.exists(out_tox):
        err = open(os.path.join(work, "boot_error.txt")).read()[-2000:] if os.path.exists(
            os.path.join(work, "boot_error.txt")) else ""
        raise SystemExit(f"build failed rc={rc} killed={td.killed} result={json.dumps(res)[:1500]} {err}")
    with open(out_tox, "rb") as f:
        tox_sha = hashlib.sha256(f.read()).hexdigest()
    try:
        commit = subprocess.run(["git", "-c", f"safe.directory={ROOT.replace(os.sep, '/')}", "rev-parse", "HEAD"],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-c", f"safe.directory={ROOT.replace(os.sep, '/')}", "status",
                                     "--porcelain", "--", "src", "tools"], cwd=ROOT, capture_output=True,
                                    text=True).stdout.strip())
    except OSError:
        commit, dirty = "", None
    meta = {"name": "TDflowMCP", "version": p["version"], "source_digest": p["source_digest"],
            "td_build_used": res.get("td_build"), "min_td_build": p["embedded_manifest"]["min_td_build"],
            "tox": "TDflowMCP.tox", "sha256": tox_sha, "size": os.path.getsize(out_tox),
            "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "source_commit": commit, "source_dirty": dirty, "builder": {"rc": rc, "killed": td.killed,
            "others_untouched": td.others_untouched(), "checks": res.get("checks")},
            "release": False, "note": "Development build. Not a release artifact (ADR-0007 §6)."}
    with open(os.path.join(out_dir, "TDflowMCP.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, indent=2)
    return meta


def expand_listing(tox: str, work: str) -> dict:
    """Expand a .tox with toeexpand and return {relative path: sha256} of its text parts."""
    d = safe_dir(os.path.join(work, "expand"), ROOT)
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    shutil.copyfile(tox, os.path.join(d, "TDflowMCP.tox"))
    subprocess.run([EXPAND, "TDflowMCP.tox"], cwd=d, capture_output=True, text=True)
    out = {}
    base = os.path.join(d, "TDflowMCP.tox.dir")
    for dp, _, fns in os.walk(base):
        for fn in fns:
            p = os.path.join(dp, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, base).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    toc = os.path.join(d, "TDflowMCP.tox.toc")
    if os.path.exists(toc):
        with open(toc, "rb") as f:
            out["<toc>"] = hashlib.sha256(f.read()).hexdigest()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "build"))
    ap.add_argument("--work", default=os.path.join(ROOT, ".tmp", "build_tox"))
    ap.add_argument("--check-reproducible", action="store_true")
    a = ap.parse_args()
    meta = build_once(a.out, os.path.join(a.work, "a"))
    report = {"first": meta}
    if a.check_reproducible:
        out_b = os.path.join(a.work, "b_out")
        meta_b = build_once(out_b, os.path.join(a.work, "b"))
        la = expand_listing(os.path.join(a.out, "TDflowMCP.tox"), os.path.join(a.work, "xa"))
        lb = expand_listing(os.path.join(out_b, "TDflowMCP.tox"), os.path.join(a.work, "xb"))
        diff = sorted(k for k in set(la) | set(lb) if la.get(k) != lb.get(k))
        report["reproducible"] = {
            "source_digest_equal": meta["source_digest"] == meta_b["source_digest"],
            "tox_sha_equal": meta["sha256"] == meta_b["sha256"],
            "expanded_files": len(la), "expanded_differences": diff,
        }
        with open(os.path.join(a.out, "reproducibility.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(report["reproducible"], f, indent=2)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
