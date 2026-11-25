"""Spike S9 (2026-11-23): TouchDesigner runs no frames while its window is minimized. The transport must then
answer at once instead of timing out, run nothing later, and keep the listener only while the host window is
known to be minimized."""
import json
import os
import socket
import sys
import threading
import time
import unittest

from tdflowmcp import engine
from tdflowmcp.net import oswindow
from tests.unit.test_pyhttp import Harness, code, mcp_headers, req

API = {"X-TDflow-Request": "1"}


class NotCookingTests(unittest.TestCase):
    def test_requests_that_need_the_main_thread_fail_fast_and_never_run(self):
        h = Harness(not_cooking_after=0.2, job_timeout=5.0)
        try:
            st, _, body = h.run(lambda: req(h.port, "GET", "/setup/api/status", API))
            self.assertEqual(st, 200)                                   # pumping: served normally
            time.sleep(0.35)                                            # the owner stops pumping
            self.assertTrue(h.t.not_cooking())
            t0 = time.monotonic()
            st, hdrs, body = h.run(lambda: req(h.port, "GET", "/setup/api/status", API), pump=False)
            self.assertEqual((st, code(body)), (503, "TDFLOW_NOT_COOKING"))
            self.assertLess(time.monotonic() - t0, 1.5)                 # not the 5 s job timeout
            self.assertEqual(hdrs.get("retry-after"), ["2"])
            st, _, body = h.run(lambda: req(h.port, "POST", "/mcp", mcp_headers(h.port), b'{"jsonrpc":"2.0","id":1}'),
                                pump=False)
            self.assertEqual((st, code(body)), (503, "TDFLOW_NOT_COOKING"))
            self.assertEqual(h.dispatched_on.__len__(), 1)              # only the first request ever ran
            self.assertEqual(h.t.stats().get("not_cooking"), 2)
            # Static assets come from the immutable snapshot and need no main thread.
            self.assertEqual(h.run(lambda: req(h.port), pump=False)[0], 200)
            # Frames resume: served again, and the refused requests did not queue up behind it.
            st, _, _ = h.run(lambda: req(h.port, "GET", "/setup/api/status", API))
            self.assertEqual(st, 200)
            self.assertEqual(len(h.dispatched_on), 2)
            self.assertFalse(h.t.not_cooking())
        finally:
            h.close()

    def test_a_long_step_does_not_make_the_next_step_look_paused(self):   # review 12, F1
        from tdflowmcp.net.pyhttp import Response

        def handler(ctx):                                               # an MCP-style call with two main-thread steps
            a = ctx.call_main(lambda: (time.sleep(0.5), "one")[1])      # longer than not_cooking_after
            b = ctx.call_main(lambda: "two")                            # submitted the moment the first returns
            return Response.json(200, {"a": a, "b": b})
        h = Harness(not_cooking_after=0.2, route_handlers={"api": handler})
        try:
            st, _, body = h.run(lambda: req(h.port, "GET", "/setup/api/status", API))
            self.assertEqual((st, json.loads(body)), (200, {"a": "one", "b": "two"}))
            self.assertNotIn("not_cooking", h.t.stats())
        finally:
            h.close()

    def test_closing_is_reported_before_not_cooking(self):              # review 12, N4
        h = Harness(not_cooking_after=0.1)
        time.sleep(0.2)
        h.t._closing = True
        from tdflowmcp.net.pyhttp import Job
        resp = h.t._submit(Job(1, "GET", "/setup/api/status", "api", None, {}, b""))
        self.assertEqual(json.loads(resp.body)["error"], "TDFLOW_CLOSING")
        h.t._closing = False
        h.close()

    def test_disabled_by_default(self):
        h = Harness()
        try:
            time.sleep(0.3)
            self.assertFalse(h.t.not_cooking())                         # None: unit tests that pump by hand
        finally:
            h.close()


class PausedWatchdogTests(unittest.TestCase):
    def wait_closed(self, h, seconds):
        end = time.monotonic() + seconds
        while not h.t.is_closed() and time.monotonic() < end:
            time.sleep(0.05)
        return h.t.is_closed()

    def test_listener_is_kept_while_the_host_window_is_minimized(self):
        state = {"paused": True, "calls": 0}

        def paused():
            state["calls"] += 1
            return state["paused"]
        h = Harness(heartbeat_timeout=0.3, paused_fn=paused)
        try:
            self.assertFalse(self.wait_closed(h, 1.2))                  # four heartbeats without a pump
            self.assertGreater(state["calls"], 1)
            self.assertEqual(h.t.stats().get("paused_kept"), 1)
            socket.create_connection(("127.0.0.1", h.port), timeout=1).close()
            # Restored, and the owner pumps again within the grace period: nothing closes.
            state["paused"] = False
            end = time.monotonic() + 0.8
            while time.monotonic() < end:
                h.t.pump(h.dispatch)
                time.sleep(0.02)
            self.assertFalse(h.t.is_closed())
            self.assertNotIn("orphan_closed", h.t.stats())
        finally:
            h.close()

    def test_restored_but_never_pumped_again_is_an_orphan(self):
        state = {"paused": True}
        h = Harness(heartbeat_timeout=0.3, paused_fn=lambda: state["paused"])
        self.assertFalse(self.wait_closed(h, 0.8))
        state["paused"] = False                                         # window back, but the owner is gone
        t0 = time.monotonic()
        self.assertTrue(self.wait_closed(h, 3))
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)            # a full heartbeat of grace first
        self.assertEqual(h.t.close_reason, "heartbeat_lost")

    def test_a_pause_that_ends_before_the_heartbeat_mark_still_earns_the_grace(self):   # review 12, F2
        t0 = time.monotonic()
        h = Harness(heartbeat_timeout=1.0, paused_fn=lambda: time.monotonic() - t0 < 0.8)
        h.t.pause_sample_after, h.t.pause_sample_every = 0.2, 0.05
        self.assertFalse(self.wait_closed(h, 1.4 - (time.monotonic() - t0)))   # past the heartbeat mark: still open
        self.assertTrue(self.wait_closed(h, 3))                          # the grace ends about 1 s after the restore
        self.assertGreaterEqual(time.monotonic() - t0, 1.7)
        self.assertEqual(h.t.close_reason, "heartbeat_lost")

    def test_each_pause_episode_is_counted_once(self):                    # review 12, N3
        state = {"paused": True}
        h = Harness(heartbeat_timeout=0.3, paused_fn=lambda: state["paused"])
        h.t.pause_sample_every = 0.05
        try:
            for _ in range(2):
                state["paused"] = True
                time.sleep(0.6)                                           # paused beyond the heartbeat: kept
                self.assertFalse(h.t.is_closed())
                state["paused"] = False
                end = time.monotonic() + 0.3
                while time.monotonic() < end:                             # frames again
                    h.t.pump(h.dispatch)
                    time.sleep(0.02)
            self.assertEqual(h.t.stats().get("paused_kept"), 2)
        finally:
            h.close()

    def test_unknown_or_failing_query_closes_exactly_as_before(self):
        for fn in (lambda: None, lambda: False, lambda: (_ for _ in ()).throw(RuntimeError("x")), lambda: "yes"):
            h = Harness(heartbeat_timeout=0.3, paused_fn=fn)
            self.assertTrue(self.wait_closed(h, 5))                     # only a literal True keeps the listener
            self.assertEqual(h.t.close_reason, "heartbeat_lost")


class PausedGuardTests(unittest.TestCase):                                # owner D20
    def wait(self, cond, seconds):
        end = time.monotonic() + seconds
        while not cond() and time.monotonic() < end:
            time.sleep(0.02)
        return cond()

    def test_binding_is_re_verified_in_the_background_while_paused(self):
        calls, verdict = [], {"r": None}

        def verify(host):
            calls.append(host)
            return verdict["r"]
        h = Harness(heartbeat_timeout=30, paused_fn=lambda: True, paused_verify=verify, paused_reverify_s=0.1)
        h.t.pause_sample_after, h.t.pause_sample_every = 0.05, 0.02
        try:
            self.assertTrue(self.wait(lambda: len(calls) >= 3, 3))     # repeated while paused
            self.assertEqual(calls[0], "127.0.0.1")
            self.assertFalse(h.t.is_closed())
            self.assertGreaterEqual(h.t.stats().get("paused_reverifies", 0), 1)
            verdict["r"] = "non_loopback:0.0.0.0"
            self.assertTrue(self.wait(h.t.is_closed, 3))               # a failed verdict closes at once
            self.assertEqual(h.t.close_reason, "paused_reverify_failed")
        finally:
            h.close()

    def test_a_raising_or_hanging_verifier_never_passes_and_never_blocks(self):
        h = Harness(heartbeat_timeout=30, paused_fn=lambda: True, paused_verify=lambda host: 1 / 0, paused_reverify_s=0.1)
        h.t.pause_sample_after, h.t.pause_sample_every = 0.05, 0.02
        self.assertTrue(self.wait(h.t.is_closed, 3))
        self.assertEqual(h.t.close_reason, "paused_reverify_failed")
        hang = threading.Event()
        h2 = Harness(heartbeat_timeout=30, paused_fn=lambda: True, paused_verify=lambda host: hang.wait(30), paused_reverify_s=0.1)
        h2.t.pause_sample_after, h2.t.pause_sample_every = 0.05, 0.02
        h2.t.paused_verify_timeout = 0.6
        try:
            time.sleep(0.3)
            self.assertEqual(h2.run(lambda: req(h2.port), pump=False)[0], 200)   # accept keeps working
            self.assertTrue(self.wait(h2.t.is_closed, 3))                       # review 14: a hang is a failure
            self.assertEqual((h2.t.close_reason, h2.t.close_reason_detail), ("paused_reverify_failed", "verify_timeout"))
        finally:
            hang.set()
            h2.close()

    def test_idle_stop_while_paused_counts_only_meaningful_activity(self):
        h = Harness(heartbeat_timeout=30, paused_fn=lambda: True, idle_seconds=0.6)
        h.t.pause_sample_after, h.t.pause_sample_every = 0.05, 0.02
        end = time.monotonic() + 1.0
        while time.monotonic() < end and not h.t.is_closed():
            try:
                req(h.port, "GET", "/setup/")                          # automatic polling keeps nothing alive
            except OSError:
                break                                                  # closed in between
            time.sleep(0.1)
        self.assertTrue(self.wait(h.t.is_closed, 2))
        self.assertEqual(h.t.close_reason, "idle")

    def test_not_paused_means_the_guard_does_nothing(self):
        calls = []
        h = Harness(heartbeat_timeout=30, paused_fn=lambda: False, paused_verify=lambda host: calls.append(1) or "fail",
                    paused_reverify_s=0.05, idle_seconds=0.2)
        h.t.pause_sample_after, h.t.pause_sample_every = 0.05, 0.02
        try:
            time.sleep(0.6)
            self.assertEqual(calls, [])
            self.assertFalse(h.t.is_closed())                          # the controller handles idle then
        finally:
            h.close()

    def test_what_counts_as_activity(self):
        from tdflowmcp.net.pyhttp import EmbeddedHTTPTransport as T
        m = T.meaningful
        self.assertTrue(m({"status": 200, "route": "mcp", "method": "POST"}))
        self.assertTrue(m({"status": 200, "route": "api", "method": "POST", "path": "/api/approvals/decide"}))
        self.assertFalse(m({"status": 200, "route": "pair", "method": "POST", "path": "/setup/api/pair"}))  # no session
        for rec in ({"status": 200, "route": "static", "method": "GET"}, {"status": 200, "route": "api", "method": "GET"},
                    {"status": 200, "route": "api", "method": "POST", "path": "/api/audit/search"},
                    {"status": 401, "route": "mcp", "method": "POST"}):
            self.assertFalse(m(rec), rec)


class WindowQueryTests(unittest.TestCase):
    def test_minimized_only_when_every_visible_main_window_is(self):
        W = oswindow.MAIN_WINDOW_CLASS
        q = lambda rows: oswindow.minimized(123, windows=lambda pid: rows)
        self.assertIs(q([(W, True, True), ("ConsoleWindowClass", True, False)]), True)
        self.assertIs(q([(W, True, False)]), False)
        self.assertIs(q([(W, True, True), (W, True, False)]), False)
        self.assertIsNone(q([("ConsoleWindowClass", True, True)]))      # no main window: unknown
        self.assertIsNone(q([(W, False, True)]))                        # an invisible one does not count
        self.assertIsNone(q([]))

    def test_errors_and_other_platforms_are_unknown(self):
        def boom(pid):
            raise OSError("no user32")
        self.assertIsNone(oswindow.minimized(1, windows=boom))
        self.assertIsNone(oswindow.minimized(1, platform="linux"))       # no query at all elsewhere
        if sys.platform == "win32":                                      # the real enumeration, with explicit types
            self.assertIsInstance(oswindow._windows(os.getpid()), list)
            self.assertIsNone(oswindow.minimized(4))                     # a process without a TouchDesigner window
        # This test process has no TouchDesigner window: never True for it.
        self.assertIn(oswindow.minimized(), (None,))


class EngineMessageTests(unittest.TestCase):
    def test_reason_names_the_minimized_window(self):
        self.assertIn("minimized", engine._main_reason("TDFLOW_NOT_COOKING"))
        self.assertIn("restore", engine._main_reason("TDFLOW_NOT_COOKING", "x"))
        self.assertEqual(engine._main_reason("TDFLOW_TIMEOUT", "fallback"), "fallback")

        class E(Exception):
            code = "TDFLOW_NOT_COOKING"
        self.assertEqual(engine._main_code(E()), "TDFLOW_NOT_COOKING")  # passed to the agent unchanged
        self.assertNotIn("\\", json.dumps(engine.NOT_COOKING))


if __name__ == "__main__":
    unittest.main()
