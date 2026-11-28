"""Durable state for Phase 1B (ADR-0006): SQLite audit chain, agents/tokens/grants, DAT snapshots.

One database file in ``Datadir`` (outside the project and the repository). Every method runs on the
thread that opened the store (TouchDesigner's main thread); sqlite3's own same-thread check enforces
it. Handler threads only ever see immutable in-memory snapshots (see ``identity``).

Audit properties (security-model §6):
- ``events`` is append-only: triggers reject UPDATE and DELETE.
- Each row stores ``prev_hash`` and ``hash = sha256(prev_hash || canonical(event))``.
- Keyed checkpoints (HMAC with a key file kept outside the database) are exported to
  ``checkpoints.jsonl``; ``verify`` reports tampering relative to the last valid checkpoint.
- ``args_hmac`` uses a per-installation key, so low-entropy arguments cannot be brute-forced.
- Any storage failure raises ``AuditUnavailable``; callers must fail closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

CRED_DB_NAME = "credentials.sqlite"      # private per-user credential store (agents, tokens, private settings)

from tdflowmcp import approvals as APR, drafts as DRAFTS

SCHEMA_VERSION = 1
GENESIS = "0" * 64
EVENT_FIELDS = ("ts", "kind", "agent_id", "token_id", "session_id", "client_info", "method", "tool",
                "args_hmac", "args_redacted", "targets", "decision", "rule_id", "policy_version", "approval_id",
                "outcome", "error_code", "duration_ms", "undo_label", "request_id", "detail")
_TOKENISH = re.compile(r"tdf_[A-Za-z0-9]{6,}\.[A-Za-z0-9_\-]{8,}|(?i:bearer\s+[A-Za-z0-9._\-]{8,})")


class AuditUnavailable(Exception):
    pass


def canonical(obj: Any) -> str:
    """Canonical JSON (RFC 8785 subset): sorted keys, no whitespace, UTF-8. Event fields are strings,
    integers and None only; floats are rejected so the encoding is unambiguous."""
    def check(o):
        if isinstance(o, float):
            raise TypeError("floats are not allowed in canonical audit fields")
        if isinstance(o, dict):
            for k, v in o.items():
                if not isinstance(k, str):
                    raise TypeError("keys must be strings")
                check(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                check(v)
    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def redact(value: Any, limit: int = 2000) -> str:
    """Arguments as stored in audit: token-shaped strings removed, size-capped (never raw secrets)."""
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = "<unserializable>"
    text = _TOKENISH.sub("<redacted>", text.encode("utf-8", "replace").decode("utf-8"))   # no lone surrogates
    return text if len(text) <= limit else text[:limit] + "…<truncated>"


_DDL = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY, ts TEXT, kind TEXT, agent_id TEXT, token_id TEXT, session_id TEXT,
  client_info TEXT, method TEXT, tool TEXT, args_hmac TEXT, args_redacted TEXT, targets TEXT,
  decision TEXT, rule_id TEXT, policy_version TEXT, approval_id TEXT, outcome TEXT, error_code TEXT,
  duration_ms INTEGER, undo_label TEXT, request_id TEXT, detail TEXT,
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE INDEX IF NOT EXISTS events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS events_agent ON events(agent_id);
CREATE INDEX IF NOT EXISTS events_tool ON events(tool);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, path TEXT NOT NULL,
  op_id INTEGER, before_text TEXT NOT NULL, before_sha TEXT NOT NULL, after_sha TEXT NOT NULL,
  audit_seq INTEGER, restored_at TEXT);
"""

# Credentials live in PRIVATE per-user storage, never in the project folder (owner decision 2026-11-22).
_DDL_CRED = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, display_name TEXT NOT NULL, role TEXT NOT NULL,
  enabled INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens (token_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, salt TEXT NOT NULL,
  digest TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS grants (id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, scope TEXT NOT NULL,
  actions TEXT NOT NULL, effect TEXT NOT NULL DEFAULT 'allow');
CREATE TABLE IF NOT EXISTS capabilities (agent_id TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (agent_id, name));
-- Approvals live with the credentials (private per-user storage), never in the project folder (review 13).
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at REAL NOT NULL,
  agent_id TEXT NOT NULL, token_id TEXT, tool TEXT NOT NULL, args TEXT NOT NULL, args_sha256 TEXT NOT NULL,
  targets TEXT NOT NULL, policy_version TEXT, reasons TEXT, state TEXT NOT NULL, decided_at TEXT, note TEXT,
  mac TEXT, redeemed_at TEXT, review TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS approvals_lookup ON approvals(agent_id, tool, args_sha256);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    """``datadir``: the audit database (events, sessions, DAT snapshots); by default the project-local
    ``.tdflowmcp/<toe>/`` folder. ``private_dir``: per-user private storage for credentials (agents,
    tokens, grants), the audit key and the keyed checkpoints. Defaults to ``datadir`` (tests)."""

    def __init__(self, datadir: str, private_dir: Optional[str] = None):
        self.datadir = datadir
        self.private_dir = private_dir or datadir
        self.db_path = os.path.join(datadir, "tdflowmcp.sqlite")
        self.cred_path = os.path.join(self.private_dir, CRED_DB_NAME)
        self.key_path = os.path.join(self.private_dir, "audit.key")
        self.checkpoint_path = os.path.join(self.private_dir, "checkpoints.jsonl")
        self.conn: Optional[sqlite3.Connection] = None
        self.cconn: Optional[sqlite3.Connection] = None
        self.checkpoint_every = 50
        self._key: bytes = b""
        self.last_error = ""
        self._draft_rates = {}

    # ---- lifecycle -----------------------------------------------------------------------------
    def open(self, readonly: bool = False) -> "Store":
        """``readonly``: a migration source. Nothing is created or modified (no key, no schema, no
        credentials database); only the events, meta and snapshots of an existing database are read."""
        if readonly:
            try:
                with open(self.key_path, encoding="ascii") as f:
                    self._key = bytes.fromhex(f.read().strip())
                if len(self._key) != 32:
                    raise ValueError("bad audit key")
                # Cleanly closed (no -wal file): immutable, so not even SQLite's -shm/-wal side files are
                # created. Otherwise (crash, or another TouchDesigner still writing) read-only with WAL, so
                # committed WAL frames are not missed; SQLite may then leave its -shm index file.
                clean = not os.path.exists(self.db_path + "-wal")
                uri = "file:" + os.path.abspath(self.db_path).replace("\\", "/") + ("?immutable=1" if clean else "?mode=ro")
                self.conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=2.0)
                self.conn.execute("SELECT 1 FROM events LIMIT 1")
            except (OSError, ValueError, sqlite3.Error) as e:
                self.last_error = type(e).__name__
                self.close()
                raise AuditUnavailable(self.last_error) from None
            return self
        try:
            os.makedirs(self.datadir, exist_ok=True)
            os.makedirs(self.private_dir, exist_ok=True)
            if not os.path.exists(self.key_path):
                with open(self.key_path, "x", encoding="ascii") as f:      # never overwrite a key
                    f.write(secrets.token_hex(32))
            with open(self.key_path, encoding="ascii") as f:
                self._key = bytes.fromhex(f.read().strip())
            if len(self._key) != 32:
                raise ValueError("bad audit key")
            self.conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=2.0)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.executescript(_DDL)
            self.conn.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            self.cconn = sqlite3.connect(self.cred_path, isolation_level=None, timeout=2.0)
            self.cconn.execute("PRAGMA journal_mode=WAL")
            self.cconn.execute("PRAGMA synchronous=FULL")
            self.cconn.executescript(_DDL_CRED)
            self.cconn.executescript(DRAFTS.DDL)
            self.cconn.execute("BEGIN IMMEDIATE")
            try:
                if "review" not in {row[1] for row in self.cconn.execute("PRAGMA table_info(approvals)")}:
                    self.cconn.execute("ALTER TABLE approvals ADD COLUMN review TEXT NOT NULL DEFAULT ''")
                self.cconn.execute("COMMIT")
            except BaseException:
                self.cconn.execute("ROLLBACK")
                raise
        except (OSError, ValueError, sqlite3.Error) as e:
            self.last_error = type(e).__name__
            self.close()
            raise AuditUnavailable(self.last_error) from None
        return self

    def close(self):
        for attr in ("conn", "cconn"):
            c = getattr(self, attr)
            if c is not None:
                try:
                    c.close()
                finally:
                    setattr(self, attr, None)

    def _cc(self) -> sqlite3.Connection:
        if self.cconn is None:
            raise AuditUnavailable(self.last_error or "closed")
        return self.cconn

    @property
    def available(self) -> bool:
        return self.conn is not None

    def _c(self) -> sqlite3.Connection:
        if self.conn is None:
            raise AuditUnavailable(self.last_error or "closed")
        return self.conn

    def _key_for(self, label: bytes) -> bytes:
        return hmac.new(self._key, label, hashlib.sha256).digest()

    def draft_request(self, project, action, args, audit, policy, protected, guard):
        """Serialized offline request. Candidate text never enters the audit chain."""
        row = self._c().execute("SELECT v FROM meta WHERE k='project_id'").fetchone()
        if not project or row != (project,):
            return {"code": "DRAFT_PROJECT", "message": "A verified project identity is required."}
        if not guard():
            return {"code": "DRAFT_PAUSED", "message": "The offline workspace is suspended or authorization changed."}
        agent = audit.get("agent_id")
        now = time.monotonic()
        self._draft_rates = {k: v for k, v in self._draft_rates.items() if now < v[0]}
        until, calls = self._draft_rates.get(agent, (now + 60, 0))
        global_until, global_calls = self._draft_rates.get("", (now + 60, 0))
        limit = DRAFTS.CALLS_PER_MINUTE if agent else 120
        if calls >= limit or global_calls >= 180:
            return {"code": "DRAFT_RATE", "message": "Wait for the offline workspace request limit to reset."}
        self._draft_rates[agent] = (until, calls + 1)
        self._draft_rates[""] = (global_until, global_calls + 1)
        db = self._cc()
        workspace = DRAFTS.Workspace(db, project, self._key_for(b"script-drafts"), policy, protected,
                                     agent, audit.get("token_id"))
        seq = self.append("intent", **audit, detail=workspace.grant_subject(action, args))
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                if not guard():
                    DRAFTS.fail("DRAFT_PAUSED", "The offline workspace is suspended or authorization changed.")
                result = workspace.run(action, args)
                if not guard():
                    DRAFTS.fail("DRAFT_PAUSED", "The offline workspace is suspended or authorization changed.")
                if workspace.grant_expiry and time.time() >= workspace.grant_expiry:
                    DRAFTS.fail("DENIED", "The offline draft grant expired.")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        except DRAFTS.DraftError as exc:
            self.append("outcome", **audit, outcome="error", error_code=exc.result["code"],
                        detail={"intent_seq": seq})
            return exc.result
        except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError) as exc:
            self.append("outcome", **audit, outcome="error", error_code="DRAFT_STORE",
                        detail={"intent_seq": seq})
            raise AuditUnavailable(type(exc).__name__) from None
        detail = {"intent_seq": seq}
        if "id" in result:
            detail.update(draft_id=result["id"], revision=result["revision"], state=result["state"])
        if "grant" in result:
            detail["grant"] = result["grant"]
        self.append("outcome", **audit, outcome="ok", detail=detail)
        return result

    def draft_grants_status(self, project):
        """Capability descriptions need only verified temporary grant metadata."""
        owner = DRAFTS.Workspace(self._cc(), project, self._key_for(b"script-drafts"), None, {}, None, None)
        try:
            return owner._owner_grants() if self._c().execute(
                "SELECT v FROM meta WHERE k='project_id'").fetchone() == (project,) else []
        except DRAFTS.DraftError:
            return []

    # ---- audit ---------------------------------------------------------------------------------
    def args_hmac(self, args: Any) -> str:
        try:
            blob = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8", "replace")
        except (TypeError, ValueError):
            blob = b"<unserializable>"
        return hmac.new(self._key_for(b"args"), blob, hashlib.sha256).hexdigest()

    def append(self, kind: str, **fields) -> int:
        """Append one event and return its seq. Raises AuditUnavailable on any failure."""
        try:
            ev = {k: None for k in EVENT_FIELDS}
            ev.update({k: v for k, v in fields.items() if k in EVENT_FIELDS})
            ev["kind"], ev["ts"] = kind, ev["ts"] or now_iso()
            for k in ("targets", "client_info", "detail"):
                if ev[k] is not None and not isinstance(ev[k], str):
                    # Free-form fields are stored as deterministic JSON text (floats allowed here);
                    # the chain hash then covers that exact text.
                    ev[k] = json.dumps(ev[k], sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
            if ev["duration_ms"] is not None:
                ev["duration_ms"] = int(float(ev["duration_ms"]))
            for k in EVENT_FIELDS:
                if ev[k] is not None and not isinstance(ev[k], (str, int)) or isinstance(ev[k], bool):
                    ev[k] = str(ev[k])
            c = self._c()
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
                body = dict(ev, seq=seq)
                h = hashlib.sha256((prev + canonical(body)).encode("utf-8")).hexdigest()
                c.execute(f"INSERT INTO events(seq, {', '.join(EVENT_FIELDS)}, prev_hash, hash) VALUES "
                          f"(?, {', '.join('?' * len(EVENT_FIELDS))}, ?, ?)",
                          (seq, *[ev[k] for k in EVENT_FIELDS], prev, h))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            if seq % self.checkpoint_every == 0:
                self.checkpoint()                   # periodic keyed checkpoints, not only at shutdown
            return seq
        except AuditUnavailable:
            raise
        except (sqlite3.Error, TypeError, ValueError) as e:
            self.last_error = type(e).__name__
            raise AuditUnavailable(self.last_error) from None

    def verify(self) -> Dict[str, Any]:
        """Recompute the chain and check every exported checkpoint. Tamper-evident relative to the
        last valid checkpoint: a fully recomputed chain is detected by the keyed checkpoint MAC."""
        c = self._c()
        cols = ["seq", *EVENT_FIELDS, "prev_hash", "hash"]
        prev, n, first_bad = GENESIS, 0, None
        hashes = {}
        for row in c.execute(f"SELECT {', '.join(cols)} FROM events ORDER BY seq"):
            r = dict(zip(cols, row))
            body = {k: r[k] for k in ["seq", *EVENT_FIELDS]}
            h = hashlib.sha256((prev + canonical(body)).encode("utf-8")).hexdigest()
            n += 1
            if r["prev_hash"] != prev or r["hash"] != h:
                first_bad = first_bad or r["seq"]
            hashes[r["seq"]] = r["hash"]
            prev = r["hash"]
        cps, cp_bad, last_ok = 0, None, 0
        for cp in self._read_checkpoints():
            cps += 1
            seq, h = cp.get("seq", -1), str(cp.get("hash", ""))
            mac_ok = isinstance(seq, int) and hmac.compare_digest(str(cp.get("mac", "")), self._cp_mac(seq, h))
            if not mac_ok or hashes.get(seq) != h:
                cp_bad = cp_bad or seq
            elif seq > last_ok:
                last_ok = seq
        row = c.execute("SELECT v FROM meta WHERE k='last_checkpoint_seq'").fetchone()
        recorded = int(row[0]) if row else 0
        missing = recorded > last_ok                 # checkpoints file deleted or truncated
        tail = n - last_ok if n else 0               # events after the last valid checkpoint (unanchored)
        return {"events": n, "chain_ok": first_bad is None, "first_bad_seq": first_bad, "checkpoints": cps,
                "checkpoints_ok": cp_bad is None and not missing, "first_bad_checkpoint": cp_bad,
                "checkpoints_missing": missing, "verified_through": last_ok, "unverified_tail": tail,
                "ok": first_bad is None and cp_bad is None and not missing}

    def bind_project(self, project_id: str) -> str:
        """Tie this audit database to one TDflowMCP project id (copy/relocation detection). A database
        that already belongs to another project is refused, never adopted."""
        c = self._c()
        try:
            row = c.execute("SELECT v FROM meta WHERE k='project_id'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta(k, v) VALUES('project_id', ?)", (project_id,))
                return project_id
        except sqlite3.Error as e:
            raise AuditUnavailable(type(e).__name__) from None
        if row[0] != project_id:
            raise AuditUnavailable("DATA_BELONGS_TO_ANOTHER_PROJECT")
        return row[0]

    def _cp_mac(self, seq: int, h: str) -> str:
        return hmac.new(self._key_for(b"checkpoint"), f"{seq}:{h}".encode(), hashlib.sha256).hexdigest()

    def checkpoint(self) -> Optional[Dict[str, Any]]:
        row = self._c().execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if not row:
            return None
        cp = {"seq": row[0], "hash": row[1], "ts": now_iso(), "mac": self._cp_mac(row[0], row[1])}
        try:
            self._repair_checkpoint_tail()
            with open(self.checkpoint_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(cp, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())            # durable before the database records it (crash safety)
            self._c().execute("INSERT OR REPLACE INTO meta(k, v) VALUES('last_checkpoint_seq', ?)", (str(row[0]),))
        except (OSError, sqlite3.Error) as e:
            raise AuditUnavailable(type(e).__name__) from None
        return cp

    def _repair_checkpoint_tail(self):
        """Review 7 F8: drop a torn, newline-less final fragment (a crash mid-write) before appending. It
        cannot validate anything (no complete MAC), so removing it loses nothing."""
        if not os.path.exists(self.checkpoint_path):
            return
        with open(self.checkpoint_path, "rb+") as f:
            data = f.read()
            if data and not data.endswith(b"\n"):
                f.truncate(data.rfind(b"\n") + 1)
                f.flush()
                os.fsync(f.fileno())

    def _read_checkpoints(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.checkpoint_path):
            return []
        out = []
        with open(self.checkpoint_path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                if i == len(lines) - 1:
                    continue        # torn final line from a crash mid-write: it validates nothing
                out.append({"seq": -1, "hash": "", "mac": ""})
        return out

    def events(self, where: str = "", params: Tuple = (), limit: int = 200) -> List[Dict[str, Any]]:
        cols = ["seq", *EVENT_FIELDS]
        q = f"SELECT {', '.join(cols)} FROM events {('WHERE ' + where) if where else ''} ORDER BY seq DESC LIMIT ?"
        return [dict(zip(cols, r)) for r in self._c().execute(q, (*params, int(limit)))]

    # ---- project-memory queries (Phase 2A; the audit log is the memory ledger) -------------------
    def max_seq(self) -> int:
        row = self._c().execute("SELECT MAX(seq) FROM events").fetchone()
        return int(row[0] or 0)

    def first_seq(self, session_id: str) -> int:
        row = self._c().execute("SELECT MIN(seq) FROM events WHERE session_id=?", (session_id,)).fetchone()
        return int(row[0] or 0)

    def memory_events(self, limit: int = 20000) -> List[Dict[str, Any]]:
        """Memory writes (intents, outcomes), creations and the user's memory claims, oldest first, from the
        latest claim on (review 10, F3). A claim resets the ledger, so nothing older matters. The latest create or
        claim (the project's memory identity) is therefore always included, however many events follow it. With
        at most MAX_RECORDS records, ``limit`` is not reached in normal use; if it is, ``truncated`` is set on the
        last row and the caller treats memory as needing a claim."""
        cols = ["seq", *EVENT_FIELDS]
        c = self._c()
        since = c.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE kind='admin' AND tool='memory_claim'").fetchone()[0]
        q = (f"SELECT {', '.join(cols)} FROM events WHERE seq >= ? AND ((kind IN ('intent','outcome') AND tool IN "
             "('tdflow_task_checkpoint','tdflow_handoff_create')) OR (kind='admin' AND tool IN "
             "('memory_claim','memory_create'))) ORDER BY seq ASC LIMIT ?")
        rows = [dict(zip(cols, r)) for r in c.execute(q, (int(since), int(limit) + 1)).fetchall()]
        if len(rows) > limit:
            rows = rows[:limit]
            rows[-1] = dict(rows[-1], truncated=True)
        if since == 0:                         # no claim yet: the identity is the latest create, wherever it is
            b = c.execute(f"SELECT {', '.join(cols)} FROM events WHERE kind='admin' AND tool='memory_create' "
                          "ORDER BY seq DESC LIMIT 1").fetchone()
            if b is not None and not any(r["seq"] == b[0] for r in rows):
                rows.insert(0, dict(zip(cols, b)))
        return rows

    def mutations_between(self, start_seq: int, end_seq: int, limit: int = 5000) -> List[Dict[str, Any]]:
        """Successful outcomes with start_seq < seq <= end_seq (the newest ``limit``), oldest first."""
        return list(reversed(self.events("seq>? AND seq<=? AND kind='outcome' AND outcome='ok' AND targets IS NOT NULL",
                                         (int(start_seq), int(end_seq)), limit)))

    def sessions(self, agent_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        q = ("SELECT session_id, agent_id, MIN(ts), MAX(ts), COUNT(*), "
             "SUM(CASE WHEN kind='outcome' AND outcome='ok' THEN 1 ELSE 0 END), "
             "SUM(CASE WHEN kind='outcome' AND tool='tdflow_session_note' THEN 1 ELSE 0 END), "
             "SUM(CASE WHEN decision IN ('DENIED','APPROVAL_UNAVAILABLE') THEN 1 ELSE 0 END) "
             "FROM events WHERE session_id IS NOT NULL " + ("AND agent_id=? " if agent_id else "") +
             "GROUP BY session_id ORDER BY MAX(seq) DESC LIMIT ?")
        rows = self._c().execute(q, ((agent_id,) if agent_id else ()) + (int(limit),))
        return [{"session_id": r[0], "agent_id": r[1], "started": r[2], "last": r[3], "events": r[4],
                 "ok_calls": r[5], "notes": r[6], "denied": r[7]} for r in rows]

    # ---- identity tables -----------------------------------------------------------------------
    def put_agent(self, agent_id: str, display_name: str, role: str, grants: Iterable[Tuple[str, str, str]],
                  capabilities: Iterable[str] = ()):
        c = self._cc()
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("INSERT INTO agents(id, display_name, role, enabled, created_at) VALUES (?,?,?,1,?)",
                      (agent_id, display_name, role, now_iso()))
            for scope, actions, effect in grants:
                c.execute("INSERT INTO grants(agent_id, scope, actions, effect) VALUES (?,?,?,?)",
                          (agent_id, scope, actions, effect))
            for name in sorted(set(capabilities)):
                c.execute("INSERT INTO capabilities(agent_id, name) VALUES (?,?)", (agent_id, name))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def put_token(self, token_id: str, agent_id: str, salt: str, digest: str):
        self._cc().execute("INSERT INTO tokens(token_id, agent_id, salt, digest, created_at) VALUES (?,?,?,?,?)",
                          (token_id, agent_id, salt, digest, now_iso()))

    def revoke_token(self, token_id: str) -> bool:
        c = self._cc()
        c.execute("BEGIN IMMEDIATE")
        try:
            cur = c.execute("UPDATE tokens SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL", (now_iso(), token_id))
            c.execute("UPDATE approvals SET state='cancelled', args='', review='' WHERE token_id=? AND "
                      "state IN ('pending','approved')", (token_id,))
            c.execute("COMMIT")
            return cur.rowcount == 1
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> bool:
        cur = self._cc().execute("UPDATE agents SET enabled=? WHERE id=?", (1 if enabled else 0, agent_id))
        return cur.rowcount == 1

    def set_grant_actions(self, agent_id: str, actions_csv: str) -> None:
        self._cc().execute("UPDATE grants SET actions=? WHERE agent_id=?", (actions_csv, agent_id))

    def grants_of(self, agent_id: str) -> List[Dict[str, Any]]:
        rows = self._cc().execute("SELECT id, scope, actions, effect FROM grants WHERE agent_id=? ORDER BY id",
                                  (agent_id,)).fetchall()
        return [{"id": r[0], "scope": r[1], "actions": r[2].split(","), "effect": r[3]} for r in rows]

    def grant_add(self, agent_id: str, scope: str, actions_csv: str, effect: str) -> int:
        return int(self._cc().execute("INSERT INTO grants(agent_id, scope, actions, effect) VALUES (?,?,?,?)",
                                      (agent_id, scope, actions_csv, effect)).lastrowid)

    def grant_remove(self, agent_id: str, grant_id: int) -> bool:
        return self._cc().execute("DELETE FROM grants WHERE agent_id=? AND id=?", (agent_id, int(grant_id))).rowcount == 1

    def permissions_apply(self, agent_id: str, role: str, rewrite: Iterable[Tuple[int, str]], remove: Iterable[int],
                          add: Iterable[Tuple[str, str, str]], capabilities: Iterable[str],
                          display_name: Optional[str] = None) -> int:
        """Owner D25: one permission change (role, grants, dedicated permissions) in a single transaction, and the
        agent's open approvals cancelled in the same transaction (review 14). Returns the number cancelled."""
        c = self._cc()
        c.execute("BEGIN IMMEDIATE")
        try:
            if c.execute("UPDATE agents SET role=? WHERE id=?", (role, agent_id)).rowcount != 1:
                raise KeyError("agent")
            if display_name:
                c.execute("UPDATE agents SET display_name=? WHERE id=?", (display_name, agent_id))
            for gid in remove:
                c.execute("DELETE FROM grants WHERE agent_id=? AND id=?", (agent_id, int(gid)))
            for gid, actions in rewrite:
                c.execute("UPDATE grants SET actions=? WHERE agent_id=? AND id=?", (actions, agent_id, int(gid)))
            for scope, actions, effect in add:
                c.execute("INSERT INTO grants(agent_id, scope, actions, effect) VALUES (?,?,?,?)",
                          (agent_id, scope, actions, effect))
            c.execute("DELETE FROM capabilities WHERE agent_id=?", (agent_id,))
            for name in sorted(set(capabilities)):
                c.execute("INSERT INTO capabilities(agent_id, name) VALUES (?,?)", (agent_id, name))
            n = c.execute("UPDATE approvals SET state='cancelled', args='', review='' WHERE agent_id=? AND "
                          "state IN ('pending','approved')", (str(agent_id),)).rowcount
            c.execute("COMMIT")
            return n
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def token_rows(self) -> List[tuple]:
        """For tests and diagnostics: (token_id, salt, digest). Never contains a secret."""
        return [tuple(r) for r in self._cc().execute("SELECT token_id, salt, digest FROM tokens").fetchall()]

    def load_identity(self) -> Tuple[List[tuple], List[tuple], List[tuple], List[tuple]]:
        c = self._cc()
        agents = c.execute("SELECT id, display_name, role, enabled, created_at FROM agents ORDER BY created_at").fetchall()
        tokens = c.execute("SELECT token_id, agent_id, salt, digest, created_at, revoked_at FROM tokens").fetchall()
        grants = c.execute("SELECT agent_id, scope, actions, effect FROM grants ORDER BY id").fetchall()
        caps = c.execute("SELECT agent_id, name FROM capabilities ORDER BY agent_id, name").fetchall()
        return agents, tokens, grants, caps

    # ---- DAT snapshots -------------------------------------------------------------------------
    def put_snapshot(self, path: str, op_id: int, before: str, before_sha: str, after_sha: str) -> int:
        cur = self._c().execute("INSERT INTO snapshots(created_at, path, op_id, before_text, before_sha, after_sha) "
                                "VALUES (?,?,?,?,?,?)", (now_iso(), path, op_id, before, before_sha, after_sha))
        return cur.lastrowid

    def link_snapshot(self, snap_id: int, audit_seq: int):
        self._c().execute("UPDATE snapshots SET audit_seq=? WHERE id=?", (audit_seq, snap_id))

    def get_snapshot(self, snap_id: int) -> Optional[Dict[str, Any]]:
        r = self._c().execute("SELECT id, path, op_id, before_text, before_sha, after_sha, audit_seq, restored_at "
                              "FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        if not r:
            return None
        return dict(zip(("id", "path", "op_id", "before_text", "before_sha", "after_sha", "audit_seq",
                         "restored_at"), r))

    def mark_restored(self, snap_id: int):
        self._c().execute("UPDATE snapshots SET restored_at=? WHERE id=?", (now_iso(), snap_id))

    # ---- settings and the optional diagnostic log (Phase 2B, owner D14) -----------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        r = self._c().execute("SELECT v FROM meta WHERE k=?", ("setting:" + str(key),)).fetchone()
        return str(r[0]) if r else default

    def set_setting(self, key: str, value: str) -> None:
        self._c().execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", ("setting:" + str(key), str(value)))

    def get_private_setting(self, key: str, default: str = "") -> str:
        """Settings that must not be changed by editing the project folder (per-user credentials store)."""
        r = self._cc().execute("SELECT v FROM meta WHERE k=?", ("setting:" + str(key),)).fetchone()
        return str(r[0]) if r else default

    def set_private_setting(self, key: str, value: str) -> None:
        self._cc().execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", ("setting:" + str(key), str(value)))

    def diag_append(self, obj: Dict[str, Any]) -> None:
        """One line in ``diagnostic.jsonl`` next to the audit database. Optional, best effort, size-capped; it is
        not part of the audit log and a failure here never affects it."""
        path = os.path.join(self.datadir, "diagnostic.jsonl")
        try:
            if os.path.exists(path) and os.path.getsize(path) > 1024 * 1024:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(_TOKENISH.sub("<redacted>", json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str)) + "\n")
        except OSError:
            pass

    # ---- approvals (Phase 2B, security-model §4) ---------------------------------------------------------
    # The rows live in the private credentials database (review 13: a project-local row could have been edited to
    # swap the approved digest or to reset a used approval). A decision is also bound to the request by a MAC
    # with the installation key, and the digest of the stored arguments is recomputed before a decision. Every transition is also an audit event (written by the caller). These methods never raise an
    # ApprovalError: the audit worker would turn it into "audit unavailable". They return {"ok": False, ...}.
    _APR_COLS = ("id", "created_at", "expires_at", "agent_id", "token_id", "tool", "args", "args_sha256", "targets",
                 "policy_version", "reasons", "state", "decided_at", "note", "mac", "redeemed_at", "review")

    def _apr_row(self, r) -> Optional[Dict[str, Any]]:
        return dict(zip(self._APR_COLS, r)) if r else None

    def _apr_expire(self, c, now: float):
        c.execute("UPDATE approvals SET state='expired', review='' WHERE state IN ('pending','approved') AND expires_at <= ?", (now,))

    def approval_get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        r = self._cc().execute(f"SELECT {', '.join(self._APR_COLS)} FROM approvals WHERE id=?", (str(approval_id),)).fetchone()
        return self._apr_row(r)

    def approval_find(self, agent_id: str, tool: str, args_sha: str, token_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The newest request of this agent for exactly this call (any state)."""
        token_clause = " AND token_id=?" if token_id is not None else ""
        params = (agent_id, tool, args_sha) + ((token_id,) if token_id is not None else ())
        r = self._cc().execute(f"SELECT {', '.join(self._APR_COLS)} FROM approvals WHERE agent_id=? AND tool=? AND "
                              f"args_sha256=?{token_clause} ORDER BY rowid DESC LIMIT 1", params).fetchone()
        return self._apr_row(r)

    def approval_request(self, agent_id: str, token_id: str, tool: str, args_text: str, args_sha: str, targets: str,
                         policy_version: str, reasons: List[str], now: Optional[float] = None,
                         ttl: float = APR.EXPIRY_S, review: str = "") -> Dict[str, Any]:
        """Create a pending request, or return the open one for the identical call. One transaction."""
        now = time.time() if now is None else float(now)
        c = self._cc()
        c.execute("BEGIN IMMEDIATE")
        try:
            self._apr_expire(c, now)
            r = c.execute(f"SELECT {', '.join(self._APR_COLS)} FROM approvals WHERE agent_id=? AND tool=? AND "
                          "args_sha256=? AND token_id=? AND state IN ('pending','approved') ORDER BY rowid DESC LIMIT 1",
                          (agent_id, tool, args_sha, token_id)).fetchone()
            if r:
                c.execute("COMMIT")
                return {"ok": True, "created": False, "approval": self._apr_row(r)}
            n = c.execute("SELECT COUNT(*) FROM approvals WHERE agent_id=? AND state='pending'", (agent_id,)).fetchone()[0]
            if n >= APR.MAX_PENDING_PER_AGENT:
                c.execute("COMMIT")
                return {"ok": False, "code": "APPROVAL_QUEUE_FULL",
                        "message": f"{n} of your requests are already waiting for the user; wait for a decision"}
            row = (APR.new_id(), now_iso(), now + float(ttl), agent_id, token_id, tool, args_text, args_sha, targets,
                   policy_version, json.dumps([str(x)[:200] for x in reasons][:10]), "pending", None, None, None, None, review)
            c.execute(f"INSERT INTO approvals({', '.join(self._APR_COLS)}) VALUES ({', '.join('?' * len(row))})", row)
            c.execute("COMMIT")
            return {"ok": True, "created": True, "approval": dict(zip(self._APR_COLS, row))}
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def approval_decide(self, approval_id: str, decision: str, note: str = "", now: Optional[float] = None,
                        shown_review_sha: Optional[str] = None) -> Dict[str, Any]:
        """The user's decision (paired admin session). Only a pending, unexpired request can be decided."""
        if decision not in ("approved", "denied"):
            return {"ok": False, "code": "INVALID_ARGS", "message": "decision must be approved or denied"}
        now = time.time() if now is None else float(now)
        c = self._cc()
        c.execute("BEGIN IMMEDIATE")
        try:
            self._apr_expire(c, now)
            a = self._apr_row(c.execute(f"SELECT {', '.join(self._APR_COLS)} FROM approvals WHERE id=?",
                                        (str(approval_id),)).fetchone())
            if a is not None and a["state"] == "pending":
                try:
                    shown_sha = APR.digest(a["tool"], json.loads(a["args"]))[1]
                    _, review_sha = APR.checked_review(a)
                except (ValueError, APR.ApprovalError):
                    shown_sha = None
                if shown_sha != a["args_sha256"]:      # what the user saw must be exactly what will run
                    c.execute("UPDATE approvals SET state='cancelled', args='', review='' WHERE id=?", (a["id"],))
                    c.execute("COMMIT")
                    return {"ok": False, "code": "APPROVAL_INVALID", "message": "the stored request does not match its "
                            "digest; it was cancelled"}
                if decision == "approved" and review_sha and shown_review_sha != review_sha:
                    c.execute("COMMIT")
                    return {"ok": False, "code": "APPROVAL_CHANGED", "message": "Review the callback source before approving."}
            if a is None or a["state"] != "pending":
                c.execute("COMMIT")
                return {"ok": False, "code": "APPROVAL_NOT_PENDING", "state": a["state"] if a else None,
                        "message": "that request is not waiting for a decision (decided, used or expired)"}
            decided_at = now_iso()
            mac = APR.decision_mac(self._key_for(b"approval"), a, decision, decided_at)
            c.execute("UPDATE approvals SET state=?, decided_at=?, note=?, mac=?, review=? WHERE id=? AND state='pending'",
                      (decision, decided_at, str(note)[:500], mac, a["review"] if decision == "approved" else "", a["id"]))
            c.execute("COMMIT")
            return {"ok": True, "approval": dict(a, state=decision, decided_at=decided_at, note=str(note)[:500], mac=mac)}
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def approval_redeem(self, approval_id: str, agent_id: str, tool: str, args_sha: str, targets: List[List[str]],
                        policy_version: str, given_id: Optional[str] = None, now: Optional[float] = None,
                        token_id: Optional[str] = None) -> Dict[str, Any]:
        """Use an approval once, for exactly this call. Checked and marked in one transaction, so two identical
        re-calls can never both run."""
        now = time.time() if now is None else float(now)
        c = self._cc()
        c.execute("BEGIN IMMEDIATE")
        try:
            a = self._apr_row(c.execute(f"SELECT {', '.join(self._APR_COLS)} FROM approvals WHERE id=?",
                                        (str(approval_id),)).fetchone())
            if a is None:
                c.execute("COMMIT")
                return {"ok": False, "code": "APPROVAL_NOT_FOUND", "message": "no such approval"}
            try:
                if token_id is not None and a.get("token_id") != token_id:
                    raise APR.ApprovalError("APPROVAL_MISMATCH", "this approval was requested by a different credential")
                APR.check_redemption(a, self._key_for(b"approval"), agent_id, tool, args_sha, targets, now, given_id)
                if str(a.get("policy_version") or "") != str(policy_version or ""):
                    raise APR.ApprovalError("APPROVAL_POLICY_CHANGED", "your permissions or the project settings changed "
                                            "since the request; ask again")
            except APR.ApprovalError as e:
                if e.code in ("APPROVAL_TARGETS_CHANGED", "APPROVAL_POLICY_CHANGED", "APPROVAL_INVALID"):
                    c.execute("UPDATE approvals SET state='cancelled', args='', review='' WHERE id=? AND state='approved'", (a["id"],))
                c.execute("COMMIT")
                return {"ok": False, "code": e.code, "message": e.message}
            # Single use; the complete arguments are no longer kept once used (review 13: retention).
            cur = c.execute("UPDATE approvals SET state='redeemed', redeemed_at=?, args='', review='' WHERE id=? AND state='approved'",
                            (now_iso(), a["id"]))
            c.execute("COMMIT")
            if cur.rowcount != 1:
                return {"ok": False, "code": "APPROVAL_REDEEMED", "message": "the approval was already used"}
            return {"ok": True, "approval": dict(a, state="redeemed")}
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def approvals_cancel_agent(self, agent_id: str) -> int:
        """Review 14: a permission change cancels the agent's open approvals (a grant added and removed again
        restores the old policy version, so the version check alone would let them be redeemed)."""
        return self._cc().execute("UPDATE approvals SET state='cancelled', args='', review='' WHERE agent_id=? AND "
                                  "state IN ('pending','approved')", (str(agent_id),)).rowcount

    def approval_cancel(self, approval_id: str) -> None:
        self._cc().execute("UPDATE approvals SET state='cancelled', args='', review='' WHERE id=? AND state IN ('pending','approved')",
                           (str(approval_id),))

    def approvals_list(self, state: Optional[str] = None, limit: int = 100, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = time.time() if now is None else float(now)
        c = self._cc()
        self._apr_expire(c, now)
        q = f"SELECT {', '.join(self._APR_COLS)} FROM approvals " + ("WHERE state=? " if state else "") + \
            "ORDER BY rowid DESC LIMIT ?"
        return [self._apr_row(r) for r in c.execute(q, ((state,) if state else ()) + (int(limit),)).fetchall()]

    def approvals_pending(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else float(now)
        return int(self._cc().execute("SELECT COUNT(*) FROM approvals WHERE state='pending' AND expires_at > ?",
                                     (now,)).fetchone()[0])
