"""Where TDflowMCP keeps its data (owner decision 2026-11-22; ADR-0006 revision). Pure Python, no `td`.

Layout (default, "project-local"):
  <folder of the .toe>/.tdflowmcp/<toe-stem>/tdflowmcp.sqlite   audit events, sessions, DAT snapshots
  <folder of the .toe>/.tdflowmcp/.gitignore                      "*" (nothing in it is ever committed)
  <private root>/projects/<project-id>/                           credentials.sqlite, audit.key, checkpoints
  <private root>/registry/<project-id>.json                       last known .toe + data folder
The private root is ``%LOCALAPPDATA%\\TDflowMCP`` (per user). Credentials, tokens, the audit key and the keyed
checkpoints never leave it.

Safe fallback: unsaved projects, read-only folders, network drives and cloud-synced folders use
``<private root>/projects/<id>/data`` instead (SQLite WAL needs local, lock-correct storage; live sync
engines can copy a database mid-write). The decision and its reason are always reported to the user.

Identity: the component stores ``Projectid`` (saved in the .toe). The registry remembers where each id
was last used. A different .toe path with the same id is MOVED (the old .toe is gone) or COPIED (it still
exists); MCP stays disabled until the user explicitly confirms (move: verified migration; copy: new id).
Nothing is relocated silently, and existing databases are never moved (only copied, then verified).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "icloudrive", "box sync",
                 "boxdrive", "box", "pcloud", "mega", "nextcloud", "owncloud", "synologydrive", "sync.com")
GITIGNORE = "# TDflowMCP operational data (audit, sessions, snapshots): never commit.\n*\n"
README = ("TDflowMCP stores this project's audit log, session history and recovery snapshots here.\n"
          "Credentials, tokens and audit keys are NOT here (they stay in your per-user private storage).\n"
          "Everything in this folder is git-ignored. Do not edit these files; moving the project is\n"
          "handled by the component (Confirm Move).\n")
_ID = re.compile(r"^prj_[A-Za-z0-9]{16}\Z")      # \Z: no trailing newline (review 20)


def new_project_id() -> str:
    return "prj_" + secrets.token_hex(8)


def valid_id(pid: str) -> bool:
    return bool(_ID.match(pid or ""))


def _drive_is_remote(path: str) -> Optional[bool]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(root) == 4          # DRIVE_REMOTE
    except Exception:
        return None


def classify_location(folder: str, is_remote: Callable[[str], Optional[bool]] = _drive_is_remote,
                      env: Optional[Dict[str, str]] = None) -> str:
    """'ok' | 'missing' | 'read_only' | 'network' | 'cloud' for a project folder."""
    env = os.environ if env is None else env
    if not folder or not os.path.isdir(folder):
        return "missing"
    norm = os.path.abspath(folder)
    if norm.startswith("\\\\") or is_remote(norm):
        return "network"
    low = norm.replace("\\", "/").lower()
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = env.get(k)
        if v and low.startswith(os.path.abspath(v).replace("\\", "/").lower()):
            return "cloud"
    if any(("/" + m + "/") in (low + "/") or ("/" + m) in low.split(" - ")[0] for m in CLOUD_MARKERS
           if m not in ("box", "mega")) or any(seg in ("box", "mega") for seg in low.split("/")):
        return "cloud"
    if not os.access(norm, os.W_OK):
        return "read_only"
    return "ok"


@dataclass
class Decision:
    status: str                 # OK | NEW | FALLBACK | MOVED | COPIED | UNSAVED
    project_id: str
    data_dir: str               # where the audit database lives (or would live)
    private_dir: str
    reason: str = ""            # location class or identity finding, for the user
    usable: bool = True         # False: MCP must stay disabled until the user acts
    previous_toe: str = ""
    previous_data_dir: str = ""


class Registry:
    """Private per-user registry: ``<private root>/registry/<project-id>.json`` = {"toe", "data_dir"}.
    One small file per project, so two TouchDesigner processes never overwrite each other's entries.
    Used only on the audit worker thread (never on TouchDesigner's main thread)."""

    def __init__(self, private_root: str):
        self.root = private_root
        self.dir = os.path.join(private_root, "registry")

    def load(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        try:
            names = os.listdir(self.dir)
        except OSError:
            return out
        for n in names:
            pid = n[:-5] if n.endswith(".json") else ""
            if not valid_id(pid):
                continue
            try:
                with open(os.path.join(self.dir, n), encoding="utf-8") as f:
                    v = json.load(f)
                if isinstance(v, dict):
                    out[pid] = v
            except (OSError, ValueError):
                continue
        return out

    def put(self, pid: str, toe: str, data_dir: str):
        if not valid_id(pid):
            raise ValueError("bad project id")
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, pid + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"toe": os.path.abspath(toe) if toe else "", "data_dir": os.path.abspath(data_dir)}, f,
                      sort_keys=True)
        for i in range(20):                                   # Windows: a reader may hold the file briefly
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == 19:
                    raise
                time.sleep(0.01)


def private_dir_for(private_root: str, pid: str) -> str:
    return os.path.join(private_root, "projects", pid)


DB_NAME = "tdflowmcp.sqlite"
REASONS = {"user_choice": "Data Location is set to private storage",
           "read_only": "the project folder is read-only", "network": "the project folder is on a network drive",
           "cloud": "the project folder is cloud-synced (a live SQLite database must not be in a syncing folder)",
           "missing": "the project folder was not found"}


_VERSIONED = re.compile(r"^(.*?)\.(\d+)$")


def base_stem(toe_path: str) -> str:
    """'show.12.toe' -> 'show': TouchDesigner's incremental save numbers belong to the same project."""
    stem = os.path.splitext(os.path.basename(toe_path or ""))[0]
    m = _VERSIONED.match(stem)
    stem = (m.group(1) if m and m.group(1) else stem) or "project"
    # Review 7 F10: never a path component that escapes .tdflowmcp/ ('..', '.', separators, drive letters).
    if not stem.strip(". ") or any(c in stem for c in '/\\:') or stem != stem.strip():
        return "project"
    return stem


def same_project_file(a: str, b: str) -> bool:
    """Same .toe, or an incremental save of it (same folder, same base name)."""
    if _same(a, b):
        return True
    return bool(a) and bool(b) and _same(os.path.dirname(os.path.abspath(a)), os.path.dirname(os.path.abspath(b)))         and base_stem(a).lower() == base_stem(b).lower()


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def resolve(toe_path: str, project_id: str, private_root: str, registry: Registry, mode: str = "project",
            new_id: str = "", recover: bool = True, location: Callable[[str], str] = classify_location,
            exists: Callable[[str], bool] = os.path.exists) -> Decision:
    """Decide where data goes for this .toe. Never creates, moves or modifies anything.

    ``project_id``: the component's saved ``Projectid`` ('' for a component that never had one; then
    ``new_id`` (or a fresh id) is used and the decision is NEW; ``recover`` False (Claim As New Project)
    skips reusing the id the registry remembers for this .toe). ``mode``: 'project' (default: next to the
    .toe when the folder is suitable) or 'private'. Unusable decisions (``usable`` False) keep MCP disabled
    until the user explicitly confirms (Confirm Move / Claim As New Project)."""
    saved = bool(toe_path) and exists(toe_path)
    new = not valid_id(project_id)
    if new and saved and recover:
        # The component got an id last time but the .toe was not saved since: same file, same identity.
        mine = [k for k, v in registry.load().items() if valid_id(k) and same_project_file(v.get("toe", ""), toe_path)]
        if len(mine) == 1:
            project_id, new = mine[0], False
    pid = (new_id if valid_id(new_id) else new_project_id()) if new else project_id
    priv = private_dir_for(private_root, pid)
    fallback = os.path.join(priv, "data")
    folder = os.path.dirname(os.path.abspath(toe_path)) if toe_path else ""
    stem = base_stem(toe_path)
    if not saved:
        data_dir, status, reason = fallback, "UNSAVED", "the project has not been saved yet, so data stays in " \
                                                         "private storage until it is"
    else:
        loc = "user_choice" if mode == "private" else location(folder)
        if loc != "ok":
            data_dir, status, reason = fallback, "FALLBACK", REASONS.get(loc, loc)
        else:
            data_dir, status, reason = os.path.join(folder, ".tdflowmcp", stem), ("NEW" if new else "OK"), ""
    if new:
        if status in ("NEW", "OK") and exists(os.path.join(data_dir, DB_NAME)):
            # A copied project folder carries another identity's database: leave it untouched.
            data_dir = f"{data_dir}-{pid[4:12]}"
        return Decision("NEW" if status == "OK" else status, pid, data_dir, priv, reason)
    known = registry.load().get(pid)
    if not known:
        # The id is saved in the .toe but this user's registry does not know it (another computer or user, a
        # copy of a project, or a reset): its credentials and keys are not here. Treated like a copy.
        return Decision("COPIED", pid, data_dir, priv, "this TDflowMCP identity is not known on this computer",
                        False)
    prev_toe, prev_dd = known.get("toe", ""), known.get("data_dir", "")
    if not saved and prev_toe and (exists(prev_toe) or _sibling_save_exists(prev_toe)):
        # Review 20 (3): an unsaved project carrying the identity of a saved project that still exists (a pasted
        # component, a .tox made from it): a copy, never that project's data.
        return Decision("COPIED", pid, data_dir, priv, "this unsaved project carries the TDflowMCP identity of "
                        "another project", False, prev_toe, prev_dd)
    if saved and prev_toe and not same_project_file(prev_toe, toe_path):
        if exists(prev_toe) or _sibling_save_exists(prev_toe):
            return Decision("COPIED", pid, data_dir, priv, "this .toe is a copy of another project that uses the "
                            "same TDflowMCP identity", False, prev_toe, prev_dd)
        return Decision("MOVED", pid, data_dir, priv, "the project was moved or renamed", False, prev_toe, prev_dd)
    if prev_dd and not _same(prev_dd, data_dir):
        same_kind = os.path.dirname(os.path.abspath(prev_dd)) == os.path.dirname(os.path.abspath(data_dir))
        if same_kind and status in ("OK", "NEW"):
            data_dir = prev_dd                                  # e.g. a claimed "<stem>-<id>" folder
        elif exists(os.path.join(prev_dd, DB_NAME)):
            return Decision("RELOCATE", pid, data_dir, priv, "the data location changed (" +
                            (reason or "the project folder is now usable") + ")", False, prev_toe, prev_dd)
    return Decision(status, pid, data_dir, priv, reason)


def _sibling_save_exists(prev_toe: str) -> bool:
    """Review 7: an incremental save of the previous project still exists, so this is a copy, not a move."""
    folder = os.path.dirname(os.path.abspath(prev_toe))
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return any(n.lower().endswith(".toe") and same_project_file(prev_toe, os.path.join(folder, n)) for n in names)


def confirm_location(decision: Decision, store_factory) -> dict:
    """User-confirmed MOVED/RELOCATE: adopt the database already at the new location (it moved with the
    project folder) after verifying it belongs to this project and its chain and keyed checkpoints verify;
    otherwise make a verified copy from the previous location. Never deletes or moves the source."""
    dst_db = os.path.join(decision.data_dir, DB_NAME)
    if os.path.exists(dst_db):
        # Review 7 F9: verify read-only first (with this project's private key and checkpoints); bind only then.
        ro = store_factory(decision.data_dir, decision.private_dir).open(readonly=True)
        try:
            v = ro.verify()
            owner = ro._c().execute("SELECT v FROM meta WHERE k='project_id'").fetchone()
        finally:
            ro.close()
        if not v["ok"] or (v["events"] and not v["verified_through"]):
            raise ValueError("the audit database at the new location does not verify against this project's keys")
        if owner and owner[0] != decision.project_id:
            raise ValueError("the audit database at the new location belongs to another project")
        st = store_factory(decision.data_dir, decision.private_dir).open()
        try:
            st.bind_project(decision.project_id)
            st.append("migration", detail={"adopted": True, "events": v["events"]})
            st.checkpoint()
            return {"adopted": True, "events": v["events"], "verified": True}
        finally:
            st.close()
    if decision.previous_data_dir and os.path.exists(os.path.join(decision.previous_data_dir, DB_NAME)):
        prepare_project_folder(decision.data_dir)          # adds the .gitignore when project-local
        return migrate(decision.previous_data_dir, decision.data_dir, decision.private_dir, store_factory,
                       project_id=decision.project_id)
    raise LookupError("no audit database was found at the previous or the new location; use Claim As New Project")


def prepare_project_folder(data_dir: str):
    """Create ``.tdflowmcp/<stem>/`` with a catch-all .gitignore and a README (project-local mode only)."""
    os.makedirs(data_dir, exist_ok=True)
    top = os.path.dirname(data_dir)
    if os.path.basename(top) == ".tdflowmcp":
        gi = os.path.join(top, ".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w", encoding="utf-8", newline="\n") as f:
                f.write(GITIGNORE)
        rd = os.path.join(top, "README.txt")
        if not os.path.exists(rd):
            with open(rd, "w", encoding="utf-8", newline="\n") as f:
                f.write(README)


def migrate(src_data_dir: str, dst_data_dir: str, private_dir: str, store_factory, src_private_dir: Optional[str] = None,
            project_id: str = "") -> dict:
    """Explicit, verified copy of an audit database to a new data folder. The source is left untouched.

    1. The source chain must verify with the private key (``src_private_dir`` holds the key; defaults to
       ``private_dir``). 2. The database is copied with SQLite's online backup API (consistent, includes WAL).
    3. The copy must verify, with the same event count and the same last hash. 4. A ``migration`` event is
    appended to the copy. Any failure removes the partial copy and raises; nothing is half-migrated.
    ``project_id``: the copy is bound to it (a source bound to a different project is refused).
    """
    key_dir = src_private_dir or private_dir
    src = store_factory(src_data_dir, key_dir).open(readonly=True)
    try:
        before = src.verify()
        if not before["ok"]:                                  # review 7 F9: chain AND keyed checkpoints
            raise ValueError("source audit chain does not verify; not migrating")
        bound = src._c().execute("SELECT v FROM meta WHERE k='project_id'").fetchone()
        if bound and project_id and bound[0] != project_id:
            raise ValueError("source audit database belongs to another project")
        last_hash = src._c().execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        os.makedirs(dst_data_dir, exist_ok=True)
        dst_db = os.path.join(dst_data_dir, "tdflowmcp.sqlite")
        if os.path.exists(dst_db):
            raise FileExistsError("destination already has an audit database")
        # Review 7 F7: build the copy in private storage, strip credentials there, then move it into place,
        # so a failure can never leave token digests in the project folder.
        os.makedirs(private_dir, exist_ok=True)
        staging = os.path.join(private_dir, "migration-staging.sqlite")
        for f in (staging, staging + "-wal", staging + "-shm", staging + "-journal"):
            if os.path.exists(f):
                os.remove(f)
        out = sqlite3.connect(staging, isolation_level=None)
        try:
            src._c().backup(out)
            # A project-local database never holds credentials (older layouts kept them together): drop
            # them and VACUUM so no salted digest survives in free pages. Events are not touched.
            for tbl in ("tokens", "grants", "agents"):
                out.execute(f"DROP TABLE IF EXISTS {tbl}")
            out.execute("PRAGMA journal_mode=DELETE")
            out.execute("VACUUM")
        except Exception:
            out.close()
            os.remove(staging)
            raise
        out.close()
        shutil.move(staging, dst_db)                          # may cross drives (private C:, project F:)
    finally:
        src.close()
    try:
        if key_dir != private_dir:                                   # carry the key + checkpoints (private only)
            os.makedirs(private_dir, exist_ok=True)
            for f in ("audit.key", "checkpoints.jsonl"):
                if os.path.exists(os.path.join(key_dir, f)) and not os.path.exists(os.path.join(private_dir, f)):
                    shutil.copyfile(os.path.join(key_dir, f), os.path.join(private_dir, f))
        dst = store_factory(dst_data_dir, private_dir).open()
        try:
            after = dst.verify()
            new_last = dst._c().execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            if not after["chain_ok"] or after["events"] != before["events"] or new_last != last_hash:
                raise ValueError("copied audit chain does not match the source")
            if project_id:
                dst.bind_project(project_id)
            seq = dst.append("migration", detail={"from_events": before["events"],
                                                  "last_hash": last_hash[0] if last_hash else None})
            dst.checkpoint()
            result = {"events": before["events"], "last_hash": last_hash[0] if last_hash else None,
                      "migration_seq": seq, "verified": True}
        finally:
            dst.close()
    except Exception:
        for f in ("tdflowmcp.sqlite", "tdflowmcp.sqlite-wal", "tdflowmcp.sqlite-shm"):
            try:
                os.remove(os.path.join(dst_data_dir, f))
            except OSError:
                pass
        raise
    return result
