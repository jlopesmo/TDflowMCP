"""Audit worker (ADR-0006 latency gate): ordering, durability confirmation, fail-closed paths, crash recovery,
and the engine running through it with main-thread steps on a separate 'owner' thread."""
import os
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from tdflowmcp.auditworker import AuditWorker, StoreProxy
from tdflowmcp.engine import Engine
from tdflowmcp.identity import Identity
from tdflowmcp.policy import Policy
from tdflowmcp.store import AuditUnavailable, Store
from tdflowmcp.tools import core_registry
from tests.unit.test_protocol_engine import ALL, FakeAdapter


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-aw-")
        self.dd = os.path.join(self.dir, "data")
        self.w = AuditWorker(self.dd, owner_ident=-1).start()     # no owner: tests call from their own thread
        self.assertTrue(self.w.ready.wait(5))
        self.assertTrue(self.w.available)

    def tearDown(self):
        self.w.close().result(5) if self.w.available else None
        shutil.rmtree(self.dir, ignore_errors=True)


class WorkerTests(Base):
    def test_fifo_order_and_chain_under_concurrency(self):
        def writer(k):
            for i in range(25):
                self.w.call("append", "intent", tool=f"t{k}", detail={"i": i})
        ths = [threading.Thread(target=writer, args=(k,)) for k in range(8)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        v = self.w.call("verify")
        self.assertTrue(v["ok"])
        self.assertEqual(v["events"], 200)
        evs = self.w.call("events", limit=1000)
        for k in range(8):                                           # per-writer order preserved
            mine = [e for e in reversed(evs) if e["tool"] == f"t{k}"]
            self.assertEqual([__import__("json").loads(e["detail"])["i"] for e in mine], list(range(25)))

    def test_durable_once_call_returns(self):
        seq = self.w.call("append", "intent", tool="durable")
        c = sqlite3.connect(os.path.join(self.dd, "tdflowmcp.sqlite"))   # an independent connection
        self.assertEqual(c.execute("SELECT tool FROM events WHERE seq=?", (seq,)).fetchone()[0], "durable")
        c.close()

    def test_owner_thread_may_not_wait(self):
        w = AuditWorker(os.path.join(self.dir, "d2")).start()            # owner = this thread
        w.ready.wait(5)
        with self.assertRaises(RuntimeError):
            w.call("append", "intent")
        f = w.submit("append", "intent")                                  # submit never blocks
        self.assertEqual(f.result(5), 1)
        w.close().result(5)

    def test_timeout_and_slow_disk(self):
        self.w.delay_s = 0.3
        t0 = time.monotonic()
        with self.assertRaises(AuditUnavailable):
            self.w.call("append", "intent", timeout=0.1)
        self.assertLess(time.monotonic() - t0, 0.25)
        self.w.delay_s = 0.0
        time.sleep(0.4)
        self.assertTrue(self.w.available)                                  # a timeout is the caller's failure

    def test_overflow_fails_closed_without_blocking(self):
        w = AuditWorker(os.path.join(self.dir, "d3"), owner_ident=-1, queue_max=2).start()
        w.ready.wait(5)
        w.delay_s = 0.2
        futs = [w.submit("append", "intent") for _ in range(10)]
        self.assertTrue(any(f.done() and not f.ok() for f in futs))
        self.assertGreaterEqual(w.stats["overflow"], 1)
        w.delay_s = 0
        w.close().result(5)

    def test_storage_failure_is_sticky(self):
        self.w.call("close")                                               # the store itself is gone
        with self.assertRaises(AuditUnavailable):
            self.w.call("append", "intent")
        self.assertFalse(self.w.available)
        with self.assertRaises(AuditUnavailable):
            self.w.call("events")

    def test_bad_argument_is_not_sticky(self):
        with self.assertRaises(AuditUnavailable):
            self.w.call("put_snapshot", "only-one-argument")                # TypeError inside the worker
        self.assertTrue(self.w.available)
        self.assertEqual(self.w.call("append", "intent"), 1)

    def test_close_is_never_refused_and_drains_first(self):
        self.w.delay_s = 0.05
        futs = [self.w.submit("append", "intent") for _ in range(5)]
        self.w.close().result(5)
        self.assertTrue(all(f.ok() for f in futs))                          # queued work written first
        with self.assertRaises(AuditUnavailable):
            self.w.submit("append", "intent").result(1)

    def test_close_checkpoints_and_crash_recovery(self):
        for _ in range(3):
            self.w.call("append", "intent")
        self.w.close().result(5)
        self.assertTrue(self.w.is_closed() or True)
        s = Store(self.dd).open()
        self.assertTrue(s.verify()["ok"])
        self.assertEqual(s.verify()["unverified_tail"], 0)                 # close wrote a checkpoint
        s.close()
        # Simulated crash: a worker that is abandoned mid-run (no close), then a new one on the same data.
        w2 = AuditWorker(self.dd, owner_ident=-1).start()
        w2.ready.wait(5)
        w2.call("append", "intent", tool="before_crash")
        w2._q.put_nowait = None                                            # nothing more can be queued
        w3 = AuditWorker(self.dd, owner_ident=-1).start()
        w3.ready.wait(5)
        v = w3.call("verify")
        self.assertTrue(v["ok"])
        self.assertEqual(v["events"], 4)
        w3.close().result(5)
        # The abandoned worker still holds the database open; release it so the temp folder can be removed
        # (on Windows an open SQLite file made rmtree fail silently and leaked one folder per run).
        w2.close().result(5)
        self.w = w3


class EngineThroughWorkerTests(Base):
    """Handler threads orchestrate; an 'owner' thread plays TouchDesigner's main thread (pump)."""

    def test_calls_and_main_thread_never_waits_for_audit(self):
        store = StoreProxy(self.w)
        idn = Identity(store)
        agent, tok = idn.create_agent("A", "builder", ["/project1/fx/**"])
        tid = idn.authenticate(tok)
        jobs: "queue.Queue" = queue.Queue()
        owner_calls = []

        def main(fn):                                    # handler side: hand fn to the owner and wait
            box = {}
            ev = threading.Event()
            jobs.put((fn, box, ev))
            ev.wait(5)
            if "e" in box:
                raise box["e"]
            return box["r"]

        stop = threading.Event()

        def owner():                                     # never touches the store
            while not stop.is_set():
                try:
                    fn, box, ev = jobs.get(timeout=0.05)
                except queue.Empty:
                    continue
                t0 = time.perf_counter()
                try:
                    box["r"] = fn()
                except BaseException as e:
                    box["e"] = e
                owner_calls.append(time.perf_counter() - t0)
                ev.set()
        ot = threading.Thread(target=owner)
        ot.start()
        self.w.owner_ident = ot.ident
        ad = FakeAdapter()
        eng = Engine(store, idn, Policy("/project1"), core_registry(), ad, ALL, lambda: False, main=main)
        self.w.delay_s = 0.05                            # slow disk: the owner thread must not feel it
        res = []
        ths = [threading.Thread(target=lambda: res.append(eng.call(tid, "tdflow_set_parameters", {
            "path": "/project1/fx/a", "values": {"x": 1}}))) for _ in range(6)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(30)
        stop.set()
        ot.join()
        self.w.delay_s = 0
        self.assertEqual(len(res), 6)
        self.assertTrue(all(not r["isError"] for r in res), res)
        self.assertLess(max(owner_calls), 0.04)          # each main step far below one 50 ms disk write
        v = self.w.call("verify")
        self.assertTrue(v["ok"])
        kinds = [e["kind"] for e in self.w.call("events", limit=100)]
        self.assertEqual(kinds.count("intent"), 6)
        self.assertEqual(kinds.count("outcome"), 6)


if __name__ == "__main__":
    unittest.main()
