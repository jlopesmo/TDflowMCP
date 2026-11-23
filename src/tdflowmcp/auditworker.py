"""Dedicated, bounded SQLite audit worker (ADR-0006 latency gate). Pure Python, no `td`.

One daemon thread owns the ``Store`` (and therefore the SQLite connection) and executes every storage
operation in FIFO order, so the hash chain stays sequential and event order equals submission order.

Two ways to use it:
- ``submit(op, ...)`` → ``Future``: never blocks (bounded queue; overflow returns an already-failed
  future). This is the only way TouchDesigner's main thread may talk to the store.
- ``call(op, ...)``: submit and wait for the result (durable once the worker's COMMIT returned with
  ``synchronous=FULL``). Raises ``AuditUnavailable`` on queue overflow, timeout, worker failure or a
  storage error. **Refuses to run on the owner thread** (the TD main thread), so the main thread can
  never wait for disk I/O.

Fail closed: once the worker hits a storage error it marks itself failed; further operations fail.
``delay_s`` is a test seam that simulates a slow disk (sleep before every operation).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
from typing import Any, Callable, Optional

from tdflowmcp.store import AuditUnavailable, Store

_CLOSE = object()
# Storage-level failures make the worker fail closed for good; argument errors fail only that operation.
FATAL = {"OperationalError", "DatabaseError", "IntegrityError", "OSError", "PermissionError", "closed",
         "STORE_FAILED", "DiskError", "IOError"}


class Future:
    __slots__ = ("_ev", "_result", "_error", "submitted")

    def __init__(self):
        self._ev = threading.Event()
        self._result: Any = None
        self._error: Optional[BaseException] = None
        self.submitted = time.monotonic()

    def set(self, value):
        self._result = value
        self._ev.set()

    def fail(self, err: BaseException):
        self._error = err
        self._ev.set()

    def done(self) -> bool:
        return self._ev.is_set()

    def ok(self) -> bool:
        return self._ev.is_set() and self._error is None

    def error(self) -> Optional[BaseException]:
        return self._error

    def result(self, timeout: Optional[float] = None):
        if not self._ev.wait(timeout):
            raise AuditUnavailable("AUDIT_TIMEOUT")
        if self._error is not None:
            raise self._error if isinstance(self._error, AuditUnavailable) else AuditUnavailable(type(self._error).__name__)
        return self._result


class AuditWorker:
    def __init__(self, datadir: str, owner_ident: Optional[int] = None, queue_max: int = 1024,
                 timeout: float = 2.0, store_factory: Callable[..., Store] = Store, private_dir: Optional[str] = None,
                 prepare: Optional[Callable[["AuditWorker"], None]] = None,
                 after_open: Optional[Callable[[Store], Any]] = None):
        """``prepare(worker)`` runs on the worker thread before the store opens (storage resolution: may set
        ``datadir``/``private_dir``; raising fails closed). ``after_open(store)`` runs right after opening
        (project binding, integrity verification); its result is ``open_info``; raising fails closed."""
        self.datadir = datadir
        self.private_dir = private_dir
        self._prepare, self._after_open = prepare, after_open
        self.open_info: Any = None
        self.failed_detail = ""
        self.owner_ident = owner_ident if owner_ident is not None else threading.get_ident()
        self.timeout = timeout
        self.delay_s = 0.0                       # test seam: simulated slow disk
        self._q: "queue.Queue" = queue.Queue(maxsize=queue_max)
        self._factory = store_factory
        self._store: Optional[Store] = None      # worker thread only
        self._key = b""
        self.ready = threading.Event()
        self.failed = ""
        self.identity_rows = None                # loaded by the worker at open (main thread never waits)
        self.stats = {"ops": 0, "overflow": 0, "errors": 0, "max_ms": 0.0, "total_ms": 0.0}
        self._lock = threading.Lock()
        self._close_req: Optional[Future] = None
        self._thread = threading.Thread(target=self._run, name="tdflowmcp-audit", daemon=True)

    # ---- lifecycle -----------------------------------------------------------------------------
    def start(self) -> "AuditWorker":
        self._thread.start()
        return self

    @property
    def available(self) -> bool:
        return self.ready.is_set() and not self.failed and self._thread.is_alive()

    def close(self) -> Future:
        """Non-blocking and never refused (not a queue item): after everything already queued is written,
        the worker writes a checkpoint and closes the store. New submissions are refused from now on."""
        if self._close_req is None:
            self._close_req = Future()
        return self._close_req

    def is_closed(self) -> bool:
        return not self._thread.is_alive()

    # ---- API -----------------------------------------------------------------------------------
    def submit(self, op: str, *args, **kw) -> Future:
        f = Future()
        if self.failed or not self._thread.is_alive() or self._close_req is not None:
            f.fail(AuditUnavailable(self.failed or ("WORKER_CLOSING" if self._close_req else "WORKER_STOPPED")))
            return f
        try:
            self._q.put_nowait((op, args, kw, f))
        except queue.Full:
            with self._lock:
                self.stats["overflow"] += 1
            f.fail(AuditUnavailable("QUEUE_FULL"))
        return f

    def call(self, op: str, *args, timeout: Optional[float] = None, **kw):
        if threading.get_ident() == self.owner_ident:
            raise RuntimeError("the TouchDesigner main thread must not wait for audit I/O (use submit)")
        return self.submit(op, *args, **kw).result(self.timeout if timeout is None else timeout)

    def args_hmac(self, args: Any) -> Optional[str]:
        """Pure computation with the installation key (no disk I/O); None until the worker is ready."""
        if not self._key:
            return None
        try:
            blob = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8", "replace")
        except (TypeError, ValueError):
            blob = b"<unserializable>"
        return hmac.new(hmac.new(self._key, b"args", hashlib.sha256).digest(), blob, hashlib.sha256).hexdigest()

    def queued(self) -> int:
        return self._q.qsize()

    # ---- worker thread -------------------------------------------------------------------------
    def _run(self):
        try:
            if self._prepare is not None:
                self._prepare(self)
            self._store = self._factory(self.datadir, self.private_dir).open()
            if self._after_open is not None:
                self.open_info = self._after_open(self._store)
            self._key = self._store._key
            self.identity_rows = self._store.load_identity()
            self.ready.set()
        except Exception as e:                     # cannot open: everything fails closed
            self.failed = type(e).__name__ if not isinstance(e, AuditUnavailable) else (str(e) or "OPEN_FAILED")
            self.failed_detail = str(e)[:300]
            if self._store is not None:
                try:
                    self._store.close()
                except Exception:
                    pass
            self.ready.set()
            self._drain_failed()
            return
        while True:
            try:
                op, args, kw, fut = self._q.get(timeout=0.05)
            except queue.Empty:
                if self._close_req is not None:
                    try:
                        self._store.checkpoint()
                    except Exception:
                        pass
                    self._store.close()
                    self._close_req.set(True)
                    self._drain_failed("WORKER_CLOSED")        # anything that slipped in is failed, not lost
                    return
                continue
            t0 = time.perf_counter()
            try:
                if self.delay_s:
                    time.sleep(self.delay_s)
                fut.set(getattr(self._store, op)(*args, **kw))
            except Exception as e:
                with self._lock:
                    self.stats["errors"] += 1
                fut.fail(e if isinstance(e, AuditUnavailable) else AuditUnavailable(type(e).__name__))
                name = str(e) if isinstance(e, AuditUnavailable) else type(e).__name__
                if name in FATAL or not self._store.available:
                    self.failed = str(e) or "STORE_FAILED"           # sticky: fail closed from now on
                    self._drain_failed()
                    return
            dt = (time.perf_counter() - t0) * 1000
            with self._lock:
                self.stats["ops"] += 1
                self.stats["total_ms"] += dt
                self.stats["max_ms"] = max(self.stats["max_ms"], dt)

    def _drain_failed(self, why: Optional[str] = None):
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            item[3].fail(AuditUnavailable(why or self.failed or "WORKER_FAILED"))


class StoreProxy:
    """Store-compatible facade used by Engine/Identity on handler threads: every call goes through the
    worker and waits for durable completion. ``available`` reflects the worker's health."""

    def __init__(self, worker: AuditWorker):
        self._w = worker

    @property
    def available(self) -> bool:
        return self._w.available

    def args_hmac(self, args):
        return self._w.args_hmac(args)

    def __getattr__(self, op):
        if op.startswith("_"):
            raise AttributeError(op)
        return lambda *a, **kw: self._w.call(op, *a, **kw)
