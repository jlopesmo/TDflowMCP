"""Package TDflowMCP.tox from a clean checkout (Phase 1C item 24). Runs OUTSIDE TouchDesigner.

    python -I tools/package_release.py [--commit HEAD] [--release-tag vX.Y.Z]

1. Creates a detached git worktree of the commit under .tmp/ (the working tree is never used, so
   uncommitted changes cannot leak into a package).
2. Builds there with tools/build_tox/build.py on TouchDesigner 2025.32440 (the minimum supported build).
3. Refuses the package unless TDflowMCP.json says: built from that commit, not dirty, TD 2025.32440,
   no embedded hash mismatches.
4. Writes dist/TDflowMCP-<version>-<short commit>/ with TDflowMCP.tox, TDflowMCP.json, INSTALL.md,
   SHA256SUMS, and a .zip of that folder. dist/ is git-ignored. Removes the worktree afterwards.
Development versions (".dev") are packaged as development builds, never as releases.
``--release-tag vX.Y.Z`` packages that tag and marks TDflowMCP.json as a release. It is refused unless the tag is
annotated and the embedded version is exactly X.Y.Z (no candidate or development suffix).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_TD = "2025.32440"


def git(*args, cwd=ROOT):
    return subprocess.run(["git", "-c", f"safe.directory={ROOT.replace(os.sep, '/')}", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def sha_file(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--release-tag", default="")
    a = ap.parse_args()
    tag = a.release_tag
    if tag:
        if git("cat-file", "-t", f"refs/tags/{tag}") != "tag":
            raise SystemExit(f"refusing to package: {tag} is not an annotated tag")
        a.commit = f"refs/tags/{tag}^{{commit}}"
    commit = git("rev-parse", a.commit)
    short = commit[:10]
    wt = os.path.join(ROOT, ".tmp", f"release-{short}")
    if os.path.exists(wt):
        subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=ROOT, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
    git("worktree", "add", "--detach", wt, commit)
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join("tools", "build_tox", "build.py")], cwd=wt,
                           capture_output=True, text=True, timeout=900)
        meta_p = os.path.join(wt, "build", "TDflowMCP.json")
        if r.returncode != 0 or not os.path.exists(meta_p):
            raise SystemExit("build failed:\n" + (r.stdout + r.stderr)[-3000:])
        meta = json.load(open(meta_p, encoding="utf-8"))
        problems = []
        if meta.get("source_commit") != commit:
            problems.append(f"source_commit {meta.get('source_commit')} != {commit}")
        if meta.get("source_dirty"):
            problems.append("source tree was dirty")
        if meta.get("td_build_used") != MIN_TD:
            problems.append(f"built with TD {meta.get('td_build_used')}, expected {MIN_TD}")
        if meta.get("builder", {}).get("checks", {}).get("embedded_hash_mismatches"):
            problems.append("embedded hash mismatches")
        tox = os.path.join(wt, "build", "TDflowMCP.tox")
        if sha_file(tox) != meta.get("sha256"):
            problems.append("tox sha256 does not match TDflowMCP.json")
        version = meta["version"]
        if tag and tag != f"v{version}":
            problems.append(f"tag {tag} does not match the embedded version {version}")
        if tag and not version.replace(".", "").isdigit():
            problems.append(f"version {version} is not a final release version")
        if problems:
            raise SystemExit("refusing to package: " + "; ".join(problems))
        name = f"TDflowMCP-{version}-{short}"
        out = os.path.join(ROOT, "dist", name)
        if os.path.exists(out):
            shutil.rmtree(out)
        os.makedirs(out)
        shutil.copyfile(tox, os.path.join(out, "TDflowMCP.tox"))
        meta["package"] = {"name": name, "development_build": ".dev" in version, "from_clean_worktree": True}
        if tag:
            meta["release"] = True
            meta["note"] = (f"Release build from the annotated tag {tag}. Verified only on Windows with "
                            f"TouchDesigner {MIN_TD}.")
            meta["package"]["tag"] = tag
        with open(os.path.join(out, "TDflowMCP.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(meta, f, indent=2)
        shutil.copyfile(os.path.join(wt, "docs", "operations", "install.md"), os.path.join(out, "INSTALL.md"))
        sums = [f"{sha_file(os.path.join(out, n))}  {n}" for n in ("TDflowMCP.tox", "TDflowMCP.json", "INSTALL.md")]
        with open(os.path.join(out, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(sums) + "\n")
        zp = out + ".zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for n in sorted(os.listdir(out)):
                z.write(os.path.join(out, n), f"{name}/{n}")
        print(json.dumps({"package": os.path.relpath(out, ROOT), "zip": os.path.relpath(zp, ROOT),
                          "zip_sha256": sha_file(zp), "tox_sha256": meta["sha256"], "commit": commit,
                          "td_build": meta["td_build_used"], "development_build": ".dev" in version}, indent=1))
    finally:
        subprocess.run(["git", "-c", f"safe.directory={ROOT.replace(os.sep, '/')}", "worktree", "remove", "--force", wt],
                       cwd=ROOT, capture_output=True)


if __name__ == "__main__":
    main()
