"""Consent-based Skills installation (ADR-0011 §3, agent-integration §2–§3). Pure Python, no `td`.

Flow: ``preview`` (what would happen, plus a digest) → the user confirms that exact preview → ``install``
or ``uninstall`` re-plans and refuses if anything changed since (``PREVIEW_CHANGED``).

Hard rules enforced here:
- targets are only the registry-listed skills directories of a client (user or project scope);
- ``..`` and symlinks/junctions anywhere below the scope base are rejected;
- bundled files must match the hashes recorded in the build manifest (Skills steer agents);
- a file that differs from both the bundle and the install manifest (user-modified or foreign) is never
  overwritten or removed (``CONFLICT``);
- writes are atomic (temp file, fsync, rename); a manifest ``tdflow-skills.manifest.json`` records
  version and per-file SHA-256; uninstall removes only manifest-listed, unmodified files;
- nothing here ever writes tokens or client configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
from typing import Dict, List, Mapping, Optional

MANIFEST = "tdflow-skills.manifest.json"
PREFIX = "tdflow-"


class SkillsError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code, self.message = code, message or code


def sha(data: str) -> str:
    return hashlib.sha256(data.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _file_sha(path: str) -> str:
    with open(path, "rb") as f:
        return sha(f.read().decode("utf-8", errors="replace"))


def verify_bundle(files: Mapping[str, str], expected: Mapping[str, str]) -> Dict[str, str]:
    """``files``: rel path -> text; ``expected``: rel path -> sha256 from the build manifest."""
    out = {}
    for rel, text in files.items():
        _check_rel(rel)
        if expected.get(rel) != sha(text):
            raise SkillsError("BUNDLE_HASH_MISMATCH", f"bundled file {rel} does not match the build manifest")
        out[rel] = text
    if not out:
        raise SkillsError("BUNDLE_EMPTY")
    return out


def _check_rel(rel: str):
    parts = rel.replace("\\", "/").split("/")
    if not parts or not parts[0].startswith(PREFIX) or any(p in ("", ".", "..") for p in parts) or os.path.isabs(rel):
        raise SkillsError("BAD_PATH", f"bad bundle path {rel!r}")


def _is_link(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))     # junctions (Windows)


def resolve_target(client: Mapping, scope: str, home: str, project_folder: str) -> (str, str):
    """(base, target): the scope base (home or project folder) and the registry-listed skills dir."""
    sk = client.get("skills") or {}
    rel = sk.get(f"{scope}_dir") if scope in ("user", "project") else None
    if not rel:
        raise SkillsError("TARGET_NOT_ALLOWED", "this client has no registry-listed skills directory for that scope")
    if scope == "user":
        if not rel.startswith("~/"):
            raise SkillsError("TARGET_NOT_ALLOWED")
        base, sub = home, rel[2:]
    else:
        if not project_folder:
            raise SkillsError("PROJECT_UNSAVED", "save the TouchDesigner project first")
        base, sub = project_folder, rel
    if ".." in sub.replace("\\", "/").split("/") or os.path.isabs(sub):
        raise SkillsError("BAD_PATH")
    return os.path.abspath(base), os.path.abspath(os.path.join(base, sub))


def _check_tree(base: str, target: str, rels):
    """No symlink/junction anywhere from below ``base`` down to every file we may touch."""
    t = os.path.normcase(target)
    if not t.startswith(os.path.normcase(base) + os.sep):
        raise SkillsError("TARGET_NOT_ALLOWED")
    paths = []
    cur = target
    while os.path.normcase(cur) != os.path.normcase(base):
        paths.append(cur)
        cur = os.path.dirname(cur)
    for rel in list(rels) + [MANIFEST]:
        p = target
        for part in rel.replace("\\", "/").split("/"):
            p = os.path.join(p, part)
            paths.append(p)
    for p in paths:
        if _is_link(p):
            raise SkillsError("SYMLINK_REJECTED", "a symbolic link or junction is in the target path")


def _in_git_repo(path: str) -> bool:
    cur = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        nxt = os.path.dirname(cur)
        if nxt == cur:
            return False
        cur = nxt


def read_manifest(target: str) -> Optional[dict]:
    p = os.path.join(target, MANIFEST)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) and isinstance(m.get("files"), dict) else {"files": {}, "invalid": True}
    except (OSError, ValueError):
        return {"files": {}, "invalid": True}


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def preview(base: str, target: str, bundle: Mapping[str, str], version: str, op: str = "install") -> dict:
    """What install (or uninstall) would do. Writes nothing."""
    _check_tree(base, target, bundle.keys())
    man = read_manifest(target) or {"files": {}}
    known = man.get("files", {})
    for rel in known:
        _check_rel(rel)
    _check_tree(base, target, known.keys())          # review 7 F5: uninstall paths are checked for links too
    actions: List[dict] = []
    if op == "install":
        for rel, text in sorted(bundle.items()):
            dst = os.path.join(target, *rel.split("/"))
            want = sha(text)
            if not os.path.exists(dst):
                a = "create"
            else:
                cur = _file_sha(dst)
                a = "unchanged" if cur == want else ("update" if known.get(rel) == cur else "conflict")
            actions.append({"path": rel, "action": a, "sha256": want})
    else:
        for rel, h in sorted(known.items()):
            _check_rel(rel)
            dst = os.path.join(target, *rel.split("/"))
            if not os.path.exists(dst):
                a = "missing"
            else:
                a = "remove" if _file_sha(dst) == h else "keep_modified"
            actions.append({"path": rel, "action": a, "sha256": h})
    res = {"op": op, "target": target, "actions": actions, "bundled_version": version,
           "installed_version": man.get("version"), "in_git_repo": _in_git_repo(target),
           "conflicts": [a["path"] for a in actions if a["action"] in ("conflict",)]}
    res["digest"] = _digest({k: res[k] for k in ("op", "target", "actions", "bundled_version", "installed_version")})
    return res


def _atomic_write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Review 7 F6: a random name created exclusively (never follows a planted link), then renamed.
    tmp = f"{path}.{secrets.token_hex(8)}.tdflow-tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.replace("\r\n", "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        if _is_link(path):
            raise SkillsError("SYMLINK_REJECTED")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def install(base: str, target: str, bundle: Mapping[str, str], version: str, confirm: str, installed_by: str) -> dict:
    p = preview(base, target, bundle, version, "install")
    if p["digest"] != confirm:
        raise SkillsError("PREVIEW_CHANGED", "the target changed since the preview; review it again")
    if p["conflicts"]:
        raise SkillsError("CONFLICT", "modified files would be overwritten: " + ", ".join(p["conflicts"]))
    os.makedirs(target, exist_ok=True)
    _check_tree(base, target, bundle.keys())                      # again, after creating directories
    written = []
    for a in p["actions"]:
        if a["action"] in ("create", "update"):
            _atomic_write(os.path.join(target, *a["path"].split("/")), bundle[a["path"]])
            written.append(a["path"])
    man = {"schema": "tdflowmcp.skills-manifest/1", "version": version, "installed_by": installed_by,
           "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "files": {rel: sha(text) for rel, text in bundle.items()}}
    _atomic_write(os.path.join(target, MANIFEST), json.dumps(man, indent=1, sort_keys=True) + "\n")
    return {"written": written, "version": version, "unchanged": [a["path"] for a in p["actions"] if a["action"] == "unchanged"]}


def uninstall(base: str, target: str, confirm: str, version: str = "") -> dict:
    p = preview(base, target, {}, version, "uninstall")
    if p["digest"] != confirm:
        raise SkillsError("PREVIEW_CHANGED", "the target changed since the preview; review it again")
    removed, kept = [], []
    for a in p["actions"]:
        dst = os.path.join(target, *a["path"].split("/"))
        if a["action"] in ("remove", "missing"):
            if a["action"] == "remove":
                os.remove(dst)
                removed.append(a["path"])
            d = os.path.dirname(dst)
            while os.path.normcase(d) != os.path.normcase(target):
                try:
                    os.rmdir(d)                                       # only if empty
                except OSError:
                    break
                d = os.path.dirname(d)
        elif a["action"] == "keep_modified":
            kept.append(a["path"])
    mp = os.path.join(target, MANIFEST)
    if os.path.exists(mp):
        os.remove(mp)
    return {"removed": removed, "kept_modified": kept}
