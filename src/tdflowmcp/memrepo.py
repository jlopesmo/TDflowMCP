"""Repository-mode project memory (ADR-0013, Phase 2A increment 3): record files in a folder the user linked.
Pure Python, no `td`.

**File I/O only.** Never called on TouchDesigner's main thread: the engine and the admin API call it on HTTP
handler threads, the extension on a short-lived background thread.

Layout inside the linked folder (it need not be a Git repository; TDflowMCP never runs Git):
  <folder>/tdflow-project.json              {"schema", "project_id", "authority": "repo" | "embedded", ...}
  <folder>/tdflow/memory/<record_id>.json   one record per file: the canonical record JSON + "\\n"
  <folder>/tdflow/memory/MIGRATIONS.jsonl   one line per migration into or out of the folder (informational)

Rules enforced here:
- ``tdflow-project.json`` must name the same ``Projectid`` as the ``tdflowProject`` component, with authority
  ``repo``. Anything else blocks reading and writing (ADR-0013 gate: a mismatched ``Projectid`` blocks linking).
- Records are never overwritten or deleted. A record file is created only if the record set is still exactly
  the one the caller planned against (same rule as the embedded write).
- No symbolic link or junction on the folder, ``tdflow/``, ``tdflow/memory/`` or any file touched. Nothing is
  written outside ``tdflow-project.json`` and ``tdflow/memory/``. A folder inside ``.tdflowmcp`` or ``.git`` is
  refused.
- Authenticity still comes only from the audit ledger (``memory.ledger``): a file that the audit log did not
  announce is ``foreign``, an edited one ``changed_outside``. A ``git pull`` or checkout that changes records
  therefore makes memory read-only until the user's Claim Project Memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import sys
import threading
import time
from typing import Iterable, List, Mapping, Optional, Tuple

from tdflowmcp import memory as MEM

PROJECT_FILE = "tdflow-project.json"
SCHEMA = "tdflow-project/1"
SUBDIR = ("tdflow", "memory")
MIGRATIONS = "MIGRATIONS.jsonl"
MAX_FILE_BYTES = 4 * MEM.MAX_RECORD_CHARS          # UTF-8 worst case of the largest record
MAX_PROJECT_FILE_BYTES = 8192
FORBIDDEN_PARTS = (".tdflowmcp", ".git")
LOCK = threading.RLock()                           # serialises record writes and migrations (review 11)
OVERSIZE = "\x00oversize"                          # stands in for a file that is too large: never a valid record
LINKED = "\x00link"                                # ... or that is a symbolic link / junction


class RepoError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code, self.message = code, message or code


def is_link(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    if not attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return False
    # Windows: junctions (mount points) and symbolic links redirect; other reparse points (for example
    # cloud-files placeholders) do not. A missing or zero tag counts as a link.
    tag = getattr(st, "st_reparse_tag", 0)
    return tag in (0, 0xA0000003, 0xA000000C)


# ---- paths -----------------------------------------------------------------------------------------------
MAX_UP = 3                                         # how far above the .toe folder the linked folder may be
WHERE = ("Project memory can be linked to the project's own folder, a folder inside it, or one of its parent "
         f"folders (up to {MAX_UP} levels, for a repository that contains the project).")


def _relative(repopath: str) -> str:
    """A ``Repopath`` is always relative to the .toe folder: ``.``, ``sub/dir`` or ``../..`` (at most MAX_UP
    levels up, and only at the start). So the link never holds a user-profile path, moves with the project, and
    TDflowMCP never writes outside the project's own folder tree."""
    rp = str(repopath or "").strip().replace("\\", "/")
    if not rp:
        raise RepoError("MEMORY_UNLINKED", "This project's memory is in repository mode but no folder is linked. "
                        "Ask the user to link one on the TDflowMCP Setup page (Project memory).")
    if "\x00" in rp or os.path.isabs(rp) or rp.startswith("/") or ":" in rp:
        raise RepoError("MEMORY_REPO_NOT_ALLOWED", WHERE)
    parts = [p for p in rp.split("/") if p not in ("", ".")]
    ups = 0
    while ups < len(parts) and parts[ups] == "..":
        ups += 1
    if ups > MAX_UP or ".." in parts[ups:] or (ups and len(parts) > ups):
        # up-then-down would name a sibling folder outside the project's own tree
        raise RepoError("MEMORY_REPO_NOT_ALLOWED", WHERE)
    return "/".join(parts) or "."


def resolve(project_folder: str, repopath: str) -> str:
    """Absolute folder for a component's ``Repopath`` (always relative to the .toe folder)."""
    rel = _relative(repopath)
    if not project_folder:
        raise RepoError("MEMORY_UNLINKED", "The linked folder is relative to the project file. Ask the user to save "
                        "the TouchDesigner project first.")
    lexical = os.path.normpath(os.path.join(os.path.abspath(project_folder), *rel.split("/")))
    # Review 11: a junction or symbolic link on ANY component between the .toe folder and the linked folder
    # (or a short 8.3 name standing for another folder) would take the files outside the project's own tree.
    # The real path of the linked folder must be exactly the real path of the .toe folder plus the link.
    expected = os.path.normpath(os.path.join(os.path.realpath(project_folder), *rel.split("/")))
    if os.path.normcase(os.path.realpath(lexical)) != os.path.normcase(expected):
        raise RepoError("SYMLINK_REJECTED", "A symbolic link or junction is in the project-memory folder path.")
    return expected


def to_repopath(project_folder: str, folder: str) -> str:
    """What to store in ``Repopath`` for a folder the user chose (typed relative to the .toe folder, or
    absolute). Refused unless it is the project folder, below it, or up to MAX_UP levels straight above it."""
    if not project_folder:
        raise RepoError("MEMORY_UNLINKED", "Save the TouchDesigner project first: the linked folder is stored "
                        "relative to it.")
    text = str(folder or "").strip()
    if not text or "\x00" in text:
        raise RepoError("MEMORY_REPO_NOT_ALLOWED", WHERE)
    base = os.path.normpath(os.path.abspath(project_folder))
    full = os.path.normpath(text if os.path.isabs(text) else os.path.join(base, text))
    if os.path.splitdrive(base)[0].lower() != os.path.splitdrive(full)[0].lower():
        raise RepoError("MEMORY_REPO_NOT_ALLOWED", WHERE)
    return _relative(os.path.relpath(full, base))


def display(project_folder: str, repopath: str) -> str:
    """For status text saved in the .toe or shown to agents: never an absolute (user-profile) path."""
    try:
        rel = _relative(repopath)
    except RepoError as e:
        return "(not linked)" if e.code == "MEMORY_UNLINKED" else "(not allowed)"
    return "<project folder>" if rel == "." else "<project folder>/" + rel


def record_dir(repo: str) -> str:
    return os.path.join(repo, *SUBDIR)


def check_folder(repo: str):
    """The linked folder must exist, be a real directory, and nothing we touch may be a link."""
    parts = [p.lower() for q in (os.path.normpath(repo), os.path.realpath(repo))
             for p in q.replace("\\", "/").split("/")]
    if any(p in FORBIDDEN_PARTS for p in parts):
        raise RepoError("MEMORY_REPO_NOT_ALLOWED", "Project memory cannot live inside a .git or .tdflowmcp folder.")
    if not os.path.isdir(repo):
        raise RepoError("MEMORY_REPO_MISSING", "The folder linked for project memory was not found. Ask the user to "
                        "check it on the TDflowMCP Setup page (Project memory).")
    for p in (repo, os.path.join(repo, SUBDIR[0]), record_dir(repo), os.path.join(repo, PROJECT_FILE)):
        if is_link(p):
            raise RepoError("SYMLINK_REJECTED", "A symbolic link or junction is in the project-memory folder path.")
    for p in (os.path.join(repo, SUBDIR[0]), record_dir(repo)):
        if os.path.exists(p) and not os.path.isdir(p):
            raise RepoError("MEMORY_REPO_NOT_ALLOWED", "tdflow/memory in the linked folder is not a directory.")


# ---- tdflow-project.json ---------------------------------------------------------------------------------
def read_project(repo: str) -> Optional[dict]:
    p = os.path.join(repo, PROJECT_FILE)
    if not os.path.exists(p):
        return None
    try:
        if os.path.getsize(p) > MAX_PROJECT_FILE_BYTES:
            raise ValueError("too large")
        with open(p, encoding="utf-8") as f:
            v = json.load(f)
        if not isinstance(v, dict) or v.get("schema") != SCHEMA or not MEM.MEMORY_ID.match(str(v.get("project_id", ""))) \
                or v.get("authority") not in ("repo", "embedded"):
            raise ValueError("bad fields")
        return v
    except (OSError, ValueError, UnicodeDecodeError):
        raise RepoError("MEMORY_LINK_INVALID", f"{PROJECT_FILE} in the linked folder is not a valid TDflowMCP "
                        "project file. Ask the user to review it.") from None


def check_link(repo: str, memory_id: str) -> dict:
    """ADR-0013 gate: the folder must be linked to exactly this project's memory, as the authority."""
    check_folder(repo)
    pj = read_project(repo)
    if pj is None:
        raise RepoError("MEMORY_UNLINKED", f"The linked folder has no {PROJECT_FILE}. Ask the user to link project "
                        "memory again on the TDflowMCP Setup page.")
    if pj["project_id"] != memory_id:
        raise RepoError("MEMORY_LINK_MISMATCH", f"{PROJECT_FILE} in the linked folder belongs to another project "
                        "(its Projectid differs from this project's tdflowProject component), so project memory is "
                        "not used. Ask the user to link the right folder.")
    if pj["authority"] != "repo":
        raise RepoError("MEMORY_LINK_STALE", "The linked folder is marked as a derived export of embedded project "
                        "memory, not as the authority. Ask the user to migrate project memory again.")
    return pj


def _write_new(path: str, data: bytes):
    """Create ``path`` with ``data``; never replaces an existing file, never follows a link."""
    tmp = f"{path}.{secrets.token_hex(8)}.tdflow-tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.lexists(path):
            raise FileExistsError(path)
        if sys.platform == "win32":
            os.rename(tmp, path)                   # fails if the destination exists
        else:
            os.link(tmp, path)                     # fails if the destination exists
            os.remove(tmp)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _replace(path: str, data: bytes):
    tmp = f"{path}.{secrets.token_hex(8)}.tdflow-tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if is_link(path):
            raise RepoError("SYMLINK_REJECTED")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_project(repo: str, memory_id: str, authority: str, note: str = "") -> dict:
    """Create or update ``tdflow-project.json`` for this memory. Another project's file is never replaced."""
    if authority not in ("repo", "embedded") or not MEM.MEMORY_ID.match(memory_id or ""):
        raise RepoError("INVALID_ARGS")
    check_folder(repo)
    cur = read_project(repo)
    if cur is not None and cur["project_id"] != memory_id:
        raise RepoError("MEMORY_LINK_MISMATCH", f"{PROJECT_FILE} in that folder belongs to another project; nothing "
                        "was changed.")
    doc = {"schema": SCHEMA, "project_id": memory_id, "authority": authority,
           "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "note": note or ("Project memory for a TouchDesigner project (TDflowMCP). Records are in tdflow/memory/. "
                            "Edits made outside TDflowMCP make memory read-only until Claim Project Memory.")}
    if cur is not None and cur.get("created_at"):
        doc["created_at"] = cur["created_at"]
    else:
        doc["created_at"] = doc["updated_at"]
    data = (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode("utf-8")
    p = os.path.join(repo, PROJECT_FILE)
    if cur is None:
        _write_new(p, data)
    else:
        _replace(p, data)
    return doc


# ---- records ---------------------------------------------------------------------------------------------
def read_records(repo: str) -> List[Tuple[str, str]]:
    """(name, text) of every record-named file. Unreadable, oversized or linked files come back as text that
    can never parse, so they are reported ``invalid`` instead of being skipped silently."""
    d = record_dir(repo)
    try:
        names = sorted(os.listdir(d))
    except FileNotFoundError:
        return []
    except OSError:
        raise RepoError("MEMORY_REPO_UNREADABLE", "The project-memory folder could not be read.") from None
    out: List[Tuple[str, str]] = []
    for fn in names:
        if not fn.endswith(".json") or not MEM.RECORD_NAME.match(fn[:-5]):
            continue
        p = os.path.join(d, fn)
        if is_link(p) or not os.path.isfile(p):
            out.append((fn[:-5], LINKED))
            continue
        try:
            if os.path.getsize(p) > MAX_FILE_BYTES:
                out.append((fn[:-5], OVERSIZE))
                continue
            with open(p, "rb") as f:
                out.append((fn[:-5], f.read().decode("utf-8", errors="replace")))
        except OSError:
            out.append((fn[:-5], OVERSIZE))
        if len(out) > MEM.MAX_RECORDS:
            break
    return out


def write_record(repo: str, name: str, text: str, expect_digest: str, memory_id: Optional[str] = None) -> dict:
    """Add one record file, only if the record set is still exactly ``expect_digest``.

    The check and the creation happen under ``LOCK`` (review 11): two requests can therefore never both pass the
    check and write two records with the same revision, and a migration (which holds the same lock) cannot
    switch the authority in between. ``memory_id``: the link is checked again inside the lock."""
    if not MEM.RECORD_NAME.match(name or ""):
        raise RepoError("INVALID_ARGS", "bad record name")
    with LOCK:
        if memory_id is not None:
            check_link(repo, memory_id)
        check_folder(repo)
        d = record_dir(repo)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            check_folder(repo)                                 # again, after creating directories
        dst = os.path.join(d, name + ".json")
        if os.path.lexists(dst) or MEM.dats_digest(read_records(repo)) != expect_digest:
            raise RepoError("MEMORY_CONFLICT", "another record was written since the request was checked; nothing "
                            "was recorded. Resume the task and retry with its latest revision.")
        try:
            _write_new(dst, (MEM.norm_text(text) + "\n").encode("utf-8"))
        except FileExistsError:
            raise RepoError("MEMORY_CONFLICT", "another record was written at the same time; nothing was recorded. "
                            "Resume the task and retry with its latest revision.") from None
        # The file must be where we meant it to be (a link swapped in between the check and the write would
        # put it elsewhere), and it must hold the text.
        inside = os.path.normcase(os.path.realpath(dst)) == os.path.normcase(
            os.path.join(os.path.realpath(repo), *SUBDIR, name + ".json"))
        back = None
        if inside:                                 # read back just this file (each file call is slow inside TD)
            try:
                with open(dst, "rb") as f:
                    back = f.read(MAX_FILE_BYTES + 1).decode("utf-8", errors="replace")
            except OSError:
                back = None
        if back is None or MEM.text_sha(back) != MEM.text_sha(text):
            try:
                os.remove(dst)
            except OSError:
                pass
            if not inside:
                raise RepoError("SYMLINK_REJECTED", "A symbolic link or junction is in the project-memory folder path.")
            raise RepoError("WRITE_FAILED", "the record file did not keep the text; nothing was recorded")
    return {"written": True, "file": "/".join(SUBDIR) + "/" + name + ".json"}


def log_migration(repo: str, entry: Mapping):
    """Append one line to ``tdflow/memory/MIGRATIONS.jsonl`` (informational; the audit log is the record)."""
    d = record_dir(repo)
    os.makedirs(d, exist_ok=True)
    check_folder(repo)
    p = os.path.join(d, MIGRATIONS)
    if is_link(p):
        raise RepoError("SYMLINK_REJECTED")
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(dict(entry), sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---- migration: preview -> confirm the exact preview (same consent pattern as Skills) ---------------------
def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def _named(dats: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    return sorted((n, t) for n, t in dats if MEM.RECORD_NAME.match(n or ""))


def plan_to_repo(repo: str, memory_id: str, dats: Iterable[Tuple[str, str]], shown: str = "") -> dict:
    """Embedded → repository: what would be written. Writes nothing. ``shown``: the folder as displayed."""
    check_folder(repo)
    pj = read_project(repo)
    if pj is not None and pj["project_id"] != memory_id:
        raise RepoError("MEMORY_LINK_MISMATCH", f"{PROJECT_FILE} in that folder belongs to another project. Choose "
                        "another folder.")
    mine = _named(dats)
    have = dict(read_records(repo))
    actions, conflicts = [], []
    for name, text in mine:
        sha = MEM.text_sha(text)
        if name not in have:
            act = "create"
        elif MEM.text_sha(have[name]) == sha:
            act = "unchanged"
        else:
            act = "conflict"
            conflicts.append(name)
        actions.append({"record": name, "action": act, "sha256": sha})
    names = {n for n, _ in mine}
    extra = []
    for name in sorted(have):
        if name not in names:
            # Left in place, never overwritten or deleted. The audit ledger decides about it afterwards: a
            # record this project wrote earlier is accepted, anything else needs Claim Project Memory.
            actions.append({"record": name, "action": "already_in_folder", "sha256": MEM.text_sha(have[name])})
            extra.append(name)
    res = {"op": "to_repo", "target": shown or repo, "memory_id": memory_id,
           "project_file": "create" if pj is None else ("unchanged" if pj["authority"] == "repo" else "update"),
           "actions": actions, "conflicts": conflicts, "already_in_folder": extra, "records": len(mine),
           "records_digest": MEM.dats_digest(mine),
           "also_written": [PROJECT_FILE, "/".join(SUBDIR) + "/" + MIGRATIONS]}
    res["digest"] = _digest({k: res[k] for k in ("op", "memory_id", "project_file", "actions", "records_digest")}
                            | {"repo": os.path.normcase(os.path.abspath(repo))})
    return res


def to_repo(repo: str, memory_id: str, dats: Iterable[Tuple[str, str]], confirm: str) -> dict:
    """Write the records and ``tdflow-project.json``; verify that the folder then holds exactly the records."""
    mine = _named(dats)
    p = plan_to_repo(repo, memory_id, mine)
    if p["digest"] != confirm:
        raise RepoError("PREVIEW_CHANGED", "the folder or the records changed since the preview; review it again")
    if p["conflicts"]:
        raise RepoError("CONFLICT", "the folder already holds different records: " + ", ".join(p["conflicts"][:5]))
    d = record_dir(repo)
    os.makedirs(d, exist_ok=True)
    check_folder(repo)
    texts = dict(mine)
    written = []
    for a in p["actions"]:
        if a["action"] == "create":
            _write_new(os.path.join(d, a["record"] + ".json"), (MEM.norm_text(texts[a["record"]]) + "\n").encode("utf-8"))
            written.append(a["record"])
    after = read_records(repo)
    got = {n: MEM.text_sha(t) for n, t in after}
    if any(got.get(n) != MEM.text_sha(t) for n, t in mine):
        raise RepoError("VERIFY_FAILED", "the folder does not hold the migrated records; the component was not "
                        "changed")
    write_project(repo, memory_id, "repo")
    return {"written": written, "records": len(after), "records_digest": p["records_digest"]}


def plan_to_embedded(repo: str, memory_id: str, comp_dats: Iterable[Tuple[str, str]], shown: str = "") -> dict:
    """Repository → embedded: which record DATs the component would get. Writes nothing."""
    check_link(repo, memory_id)
    files = _named(read_records(repo))
    have = dict(_named(comp_dats))
    actions = []
    for name, text in files:
        sha = MEM.text_sha(text)
        act = "create" if name not in have else ("unchanged" if MEM.text_sha(have[name]) == sha else "replace")
        actions.append({"record": name, "action": act, "sha256": sha})
    names = {n for n, _ in files}
    for name in sorted(have):
        if name not in names:
            actions.append({"record": name, "action": "remove_stale_copy", "sha256": MEM.text_sha(have[name])})
    res = {"op": "to_embedded", "source": shown or repo, "memory_id": memory_id, "actions": actions,
           "conflicts": [], "records": len(files), "records_digest": MEM.dats_digest(files),
           "also_written": [PROJECT_FILE + " (marked as a derived export)", "/".join(SUBDIR) + "/" + MIGRATIONS]}
    res["digest"] = _digest({k: res[k] for k in ("op", "memory_id", "actions", "records_digest")}
                            | {"repo": os.path.normcase(os.path.abspath(repo))})
    return res


def plan_relink(repo: str, memory_id: str, shown: str = "") -> dict:
    """Repository mode with a lost or stale link (the .toe was not saved after a migration, the project was
    saved elsewhere, ``tdflow-project.json`` is missing): what linking this folder again would do. Writes
    nothing. A folder that belongs to another project is refused. The records are not touched and stay subject
    to the audit ledger: whatever the audit log does not know needs Claim Project Memory afterwards."""
    check_folder(repo)
    pj = read_project(repo)
    if pj is not None and pj["project_id"] != memory_id:
        raise RepoError("MEMORY_LINK_MISMATCH", f"{PROJECT_FILE} in that folder belongs to another project. Choose "
                        "another folder.")
    files = _named(read_records(repo))
    res = {"op": "relink", "target": shown or repo, "memory_id": memory_id,
           "project_file": "create" if pj is None else ("unchanged" if pj["authority"] == "repo" else "update"),
           "actions": [{"record": n, "action": "keep", "sha256": MEM.text_sha(t)} for n, t in files], "conflicts": [],
           "records": len(files), "records_digest": MEM.dats_digest(files),
           "also_written": [PROJECT_FILE, "/".join(SUBDIR) + "/" + MIGRATIONS]}
    res["digest"] = _digest({k: res[k] for k in ("op", "memory_id", "project_file", "actions", "records_digest")}
                            | {"repo": os.path.normcase(os.path.abspath(repo))})
    return res


def relink(repo: str, memory_id: str, confirm: str) -> dict:
    p = plan_relink(repo, memory_id)
    if p["digest"] != confirm:
        raise RepoError("PREVIEW_CHANGED", "the folder changed since the preview; review it again")
    write_project(repo, memory_id, "repo")
    return {"records": p["records"], "records_digest": p["records_digest"]}


def mark_derived(repo: str, memory_id: str, rev_note: str = ""):
    """After repository → embedded: the folder stays as a stamped, non-authoritative export."""
    write_project(repo, memory_id, "embedded",
                  "DERIVED - authority: embedded tdflowProject component (Projectid " + memory_id + "). These files "
                  "are an export as of the migration" + (" (" + rev_note + ")" if rev_note else "") + " and are not "
                  "read by TDflowMCP.")
