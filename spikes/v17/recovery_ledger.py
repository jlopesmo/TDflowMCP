"""Disposable SQLite write-ahead experiment. The caller owns recovery and process liveness."""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class Refused(Exception):
    """The experiment cannot authorize action."""


SCHEMA = """
CREATE TABLE guard (id INTEGER PRIMARY KEY CHECK (id = 1), suspended INTEGER NOT NULL);
INSERT INTO guard VALUES (1, 0);
CREATE TABLE review (id TEXT PRIMARY KEY, action TEXT NOT NULL, binding TEXT NOT NULL,
                     expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE intent (id TEXT PRIMARY KEY, state TEXT NOT NULL, binding TEXT NOT NULL);
"""


class Ledger:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @classmethod
    def create(cls, path):
        ledger = cls(path)
        with ledger.path.open("xb"):
            pass
        with closing(sqlite3.connect(ledger.path)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
        return ledger

    @contextmanager
    def _transaction(self):
        try:
            # Opening an absent or unwritable ledger must fail instead of creating a new lineage.
            uri = self.path.as_uri() + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db, db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
        except sqlite3.Error as exc:
            raise Refused("Ledger unavailable. Stop and retain the existing evidence.") from exc

    @staticmethod
    def _require_ready(db):
        if db.execute("SELECT suspended FROM guard WHERE id = 1").fetchone() != (0,):
            raise Refused("Project is suspended.")
        if db.execute("SELECT 1 FROM intent WHERE state = 'dispatching'").fetchone():
            raise Refused("A prior intent lacks a recorded outcome.")

    def owner_review(self, review_id, action, binding, expires):
        """The fixture calls this only from its trusted owner decision path."""
        if action not in ("author", "activate"):
            raise Refused("Unknown review purpose.")
        with self._transaction() as db:
            self._require_ready(db)
            db.execute("INSERT INTO review (id, action, binding, expires) VALUES (?, ?, ?, ?)",
                       (review_id, action, binding, expires))

    def begin(self, intent_id, author_id, author_binding, activation_id, activation_binding, *, now):
        """Atomically consume two exact reviews and durably record intent before dispatch."""
        with self._transaction() as db:
            self._require_ready(db)
            for review_id, action, binding in ((author_id, "author", author_binding),
                                               (activation_id, "activate", activation_binding)):
                used = db.execute("""
                    UPDATE review SET used = 1 WHERE id = ? AND action = ? AND binding = ?
                    AND expires > ? AND used = 0
                """, (review_id, action, binding, now)).rowcount
                if used != 1:
                    raise Refused("Review is absent, stale, expired or consumed.")
            db.execute("INSERT INTO intent VALUES (?, 'dispatching', ?)", (intent_id, activation_binding))

    def finish(self, intent_id, state):
        if state not in ("observed", "fault", "unknown", "cancelled"):
            raise Refused("Unknown outcome classification.")
        with self._transaction() as db:
            changed = db.execute("UPDATE intent SET state = ? WHERE id = ? AND state = 'dispatching'",
                                 (state, intent_id)).rowcount
            if changed != 1:
                raise Refused("No matching outstanding intent.")
            if state in ("fault", "unknown"):
                db.execute("UPDATE guard SET suspended = 1 WHERE id = 1")

    def recover_after_exit(self):
        """After the supervisor has verified process exit, quarantine any incomplete dispatch."""
        with self._transaction() as db:
            unknown = db.execute("UPDATE intent SET state = 'unknown' WHERE state = 'dispatching'").rowcount
            if unknown:
                db.execute("UPDATE guard SET suspended = 1 WHERE id = 1")
        return unknown

    def status(self):
        with self._transaction() as db:
            suspended, = db.execute("SELECT suspended FROM guard WHERE id = 1").fetchone()
            return bool(suspended), dict(db.execute("SELECT id, state FROM intent"))
