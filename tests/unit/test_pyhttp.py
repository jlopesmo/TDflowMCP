"""Embedded loopback HTTP transport (src/tdflowmcp/net/pyhttp.py), exercised on real loopback sockets."""
import json
import socket
import sys
import threading
import time
import unittest

from tdflowmcp.gate import GateConfig
from tdflowmcp.net import bindverify as bv
from tdflowmcp.net.bindverify import ListenerState
from tdflowmcp.net.pyhttp import (EmbeddedHTTPTransport, Response, TokenAuthenticator, TransportError,
                                  error)

TOKEN = "t" * 40


def raw(port, data: bytes, timeout=5.0, shut=True) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(data)
        if shut:
            s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            try:
                chunk = s.recv(65536)
            except (ConnectionResetError, ConnectionAbortedError):
                break
            if not chunk:
                break
            out += chunk
        return out
    finally:
        s.close()


def req(port, method="GET", path="/setup/", headers=None, body=b"", host=None):
    h = {"Host": host or f"127.0.0.1:{port}"}
    h.update(headers or {})
    if body:
        h["Content-Length"] = str(len(body))
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
    return parse(raw(port, head.encode() + body))


def parse(data: bytes):
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1]) if lines and len(lines[0].split()) > 1 else 0
    hdrs = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        hdrs.setdefault(k.strip().lower(), []).append(v.strip())
    return status, hdrs, body


def code(body: bytes):
    try:
        return json.loads(body).get("error")
    except ValueError:
        return None


class Harness:
    """Owns a transport on an ephemeral port; ``run`` executes client calls in a thread while the
    test (main) thread pumps, exactly like TouchDesigner's frame loop."""

    def __init__(self, dispatch=None, state=ListenerState.SERVING, **kw):
        self.state = state
        self.dispatched_on = []
        self.auth = TokenAuthenticator()
        self.auth.add(TOKEN, "agent-1")
        kw.setdefault("io_timeout", 2.0)
        kw.setdefault("mcp_enabled", True)
        self.t = EmbeddedHTTPTransport("127.0.0.1", 0, state_fn=lambda: self.state, authenticator=self.auth, **kw)
        self.t.set_static({"/setup/": ("text/html; charset=utf-8", b"<!doctype html><p>setup</p>"),
                           "/setup/app.js": ("text/javascript", b"console.log(1)")})
        self.t.start()
        self.port = self.t.port
        self.dispatch = dispatch or self.default_dispatch
        self.pumping = True

    def default_dispatch(self, job):
        self.dispatched_on.append(threading.get_ident())
        return Response.json(200, {"ok": True, "route": job.route, "principal": job.principal})

    def run(self, fn, timeout=20.0, pump=True):
        out = {}

        def target():
            try:
                out["v"] = fn()
            except Exception as e:  # pragma: no cover - surfaced below
                out["e"] = e
        th = threading.Thread(target=target, daemon=True)
        th.start()
        end = time.monotonic() + timeout
        while th.is_alive() and time.monotonic() < end:
            if pump and self.pumping:
                self.t.pump(self.dispatch)
            time.sleep(0.005)
        th.join(0.1)
        if "e" in out:
            raise out["e"]
        return out.get("v")

    def close(self):
        self.t.begin_close()
        end = time.monotonic() + 5
        while not self.t.is_closed() and time.monotonic() < end:
            time.sleep(0.01)


def mcp_headers(port, token=TOKEN, **extra):
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    h.update(extra)
    return h


class BindTests(unittest.TestCase):
    def test_refuses_non_loopback_literal(self):
        for host in ("0.0.0.0", "", "localhost", "::", "::1", "192.168.1.10"):
            with self.assertRaises(TransportError) as cm:
                EmbeddedHTTPTransport(host, 0, state_fn=lambda: ListenerState.SERVING)
            self.assertEqual(cm.exception.code, "BIND_REFUSED")

    def test_sockname_is_loopback_and_verdict_passes(self):
        h = Harness()
        try:
            host, _ = h.t.sockname()
            self.assertEqual(host, "127.0.0.1")
            ev = bv.verdict(["127.0.0.1"], [], {}, sockname=host)
            self.assertIs(ev.verdict, bv.Verdict.PASS)
            self.assertIs(bv.verdict(["127.0.0.1"], [], {}, sockname="0.0.0.0").verdict, bv.Verdict.FAIL)
        finally:
            h.close()

    def test_port_conflict_reported(self):
        h = Harness()
        try:
            other = EmbeddedHTTPTransport("127.0.0.1", h.port, state_fn=lambda: ListenerState.SERVING)
            with self.assertRaises(TransportError) as cm:
                other.start()
            self.assertEqual(cm.exception.code, "PORT_IN_USE")
        finally:
            h.close()

    @unittest.skipUnless(sys.platform == "win32", "SO_EXCLUSIVEADDRUSE is Windows-only")
    def test_exclusive_bind_blocks_loopback_cobind(self):
        h = Harness()
        try:
            for reuse in (0, 1):
                s = socket.socket()
                if reuse:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with self.assertRaises(OSError):
                    s.bind(("127.0.0.1", h.port))
                s.close()
        finally:
            h.close()

    def test_other_process_on_port_fails_verdict_as_port_in_use(self):
        # Windows allows another socket on 0.0.0.0:<port> next to our 127.0.0.1:<port>; that is a conflict.
        ev = bv.verdict(["127.0.0.1"], [], {}, sockname="127.0.0.1", others=[("0.0.0.0", 9480, 4242)])
        self.assertIs(ev.verdict, bv.Verdict.FAIL)
        self.assertEqual(ev.reasons, ("port_shared:4242",))
        g = bv.BindGate()
        g.request_activation(bv.embedded_capability("127.0.0.1"))
        self.assertEqual(g.on_evidence(ev), "close_now")
        self.assertEqual(g.error_code, "PORT_IN_USE")

    def test_gate_capability_and_bind_error(self):
        self.assertIs(bv.embedded_capability("127.0.0.1").verdict, bv.Verdict.PASS)
        self.assertIs(bv.embedded_capability("0.0.0.0").verdict, bv.Verdict.FAIL)
        g = bv.BindGate()
        self.assertEqual(g.request_activation(bv.embedded_capability("127.0.0.1"), user_initiated=True),
                         "activate_verifying")
        self.assertEqual(g.on_bind_error("PORT_IN_USE"), "closed")
        self.assertEqual((g.state, g.error_code), (ListenerState.CLOSED, "PORT_IN_USE"))
        self.assertEqual(g.request_activation(bv.embedded_capability("127.0.0.1")), "remain_closed")

    def test_gate_user_close_and_transport_failure(self):
        g = bv.BindGate()
        g.request_activation(bv.embedded_capability("127.0.0.1"))
        g.on_evidence(bv.verdict(["127.0.0.1"], []))
        self.assertEqual(g.user_close(), "close_now")
        self.assertEqual((g.state, g.error_code), (ListenerState.CLOSING, ""))
        self.assertEqual(g.confirm_closed([], []), "closed")
        self.assertEqual(g.request_activation(bv.embedded_capability("127.0.0.1"), user_initiated=True),
                         "activate_verifying")
        g.on_evidence(bv.verdict(["127.0.0.1"], []))
        self.assertEqual(g.on_transport_failure(), "close_now")
        self.assertEqual(g.error_code, "TRANSPORT_FAILED")


class GateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_verifying_returns_503_before_anything(self):
        self.h.state = ListenerState.VERIFYING
        st, hd, body = req(self.h.port)
        self.assertEqual((st, code(body)), (503, "TDFLOW_VERIFYING"))
        st, _, body = req(self.h.port, "POST", "/mcp", mcp_headers(self.h.port), b"{}")
        self.assertEqual(st, 503)
        self.assertEqual(self.h.dispatched_on, [])

    def test_static_served_from_snapshot_with_security_headers(self):
        st, hd, body = req(self.h.port)
        self.assertEqual(st, 200)
        self.assertIn(b"setup", body)
        self.assertEqual(hd["content-type"], ["text/html; charset=utf-8"])
        self.assertIn("frame-ancestors 'none'", hd["content-security-policy"][0])
        self.assertEqual(hd["x-content-type-options"], ["nosniff"])
        self.assertFalse([k for k in hd if k.startswith("access-control-")])
        self.assertEqual(hd["connection"], ["close"])
        self.assertEqual(self.h.dispatched_on, [])          # static never needs the main thread
        st, _, body = req(self.h.port, path="/setup/missing.css")
        self.assertEqual((st, code(body)), (404, "NOT_FOUND"))

    def test_host_origin_csrf_and_preflight(self):
        self.assertEqual(req(self.h.port, host="evil.example:%d" % self.h.port)[0], 403)
        st, hd, _ = req(self.h.port, host="localhost:%d" % self.h.port)
        self.assertEqual(st, 308)
        self.assertEqual(hd["location"], [f"http://127.0.0.1:{self.h.port}/setup/"])
        st, _, body = req(self.h.port, "GET", "/setup/api/status",
                          {"X-TDflow-Request": "1", "Origin": "http://evil.example"})
        self.assertEqual((st, code(body)), (403, "BAD_ORIGIN"))
        st, _, body = req(self.h.port, "GET", "/setup/api/status")
        self.assertEqual((st, code(body)), (403, "MISSING_REQUEST_HEADER"))
        st, _, body = req(self.h.port, "POST", "/setup/api/x", {"Origin": f"http://127.0.0.1:{self.h.port}"}, b"{}")
        self.assertEqual((st, code(body)), (403, "MISSING_CSRF"))
        # Origin + CSRF header present but no admin session: still denied (deny by default).
        st, _, body = req(self.h.port, "POST", "/setup/api/x",
                          {"Origin": f"http://127.0.0.1:{self.h.port}", "X-TDflow-CSRF": "x"}, b"{}")
        self.assertEqual((st, code(body)), (401, "UNAUTHENTICATED"))
        self.assertEqual(req(self.h.port, "OPTIONS", "/mcp", {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(req(self.h.port, path="/setup/../api/x")[0], 400)
        self.assertEqual(self.h.dispatched_on, [])

    def test_mcp_requires_token(self):
        port = self.h.port
        st, _, body = req(port, "POST", "/mcp", {"Content-Type": "application/json"}, b"{}")
        self.assertEqual((st, code(body)), (401, "UNAUTHENTICATED"))
        st, _, _ = req(port, "POST", "/mcp", mcp_headers(port, token="x" * 40), b"{}")
        self.assertEqual(st, 401)
        dup = b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n" \
              b"Authorization: Bearer %s\r\nAuthorization: Bearer %s\r\nContent-Length: 2\r\n\r\n{}" % (
                  port, TOKEN.encode(), TOKEN.encode())
        self.assertEqual(parse(raw(port, dup))[0], 401)
        st, _, body = self.h.run(lambda: req(port, "POST", "/mcp", mcp_headers(port), b"{}"))
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["principal"], "agent-1")
        self.assertEqual(self.h.dispatched_on, [threading.get_ident()])
        audit = json.dumps(self.h.t.drain_audit())
        self.assertNotIn(TOKEN, audit)
        self.assertIn("UNAUTHENTICATED", audit)

    def test_kill_switch_suspends_mcp_only(self):
        self.h.t.suspended = True
        port = self.h.port
        st, _, body = req(port, "POST", "/mcp", mcp_headers(port), b"{}")
        self.assertEqual((st, code(body)), (503, "SUSPENDED"))
        self.assertEqual(req(port)[0], 200)
        self.h.t.suspended = False
        self.assertEqual(self.h.run(lambda: req(port, "POST", "/mcp", mcp_headers(port), b"{}"))[0], 200)

    def test_body_limits_and_framing(self):
        port = self.h.port
        big = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n"
               b"Content-Length: %d\r\n\r\n" % (port, 8 * 1024 * 1024))
        t0 = time.monotonic()
        st, _, body = parse(raw(port, big))
        self.assertEqual((st, code(body)), (413, "BODY_TOO_LARGE"))
        self.assertLess(time.monotonic() - t0, 1.5)            # rejected without reading the body
        chunked = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n"
                   b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n" % port)
        self.assertEqual(parse(raw(port, chunked))[0], 400)
        neg = b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: -1\r\n\r\n" % port
        self.assertEqual(parse(raw(port, neg))[0], 400)
        short = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\n"
                 b"Authorization: Bearer %s\r\nContent-Length: 100\r\n\r\n{}" % (port, TOKEN.encode()))
        self.assertEqual(code(parse(raw(port, short))[2]), "INCOMPLETE_BODY")
        st, hd, body = parse(raw(port, b"NONSENSE\r\n\r\n"))
        self.assertEqual(st, 400)
        self.assertEqual(hd["content-type"], ["application/json"])
        self.assertEqual(parse(raw(port, b"TRACE /setup/ HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % port))[0], 501)
        self.assertEqual(req(port)[0], 200)                    # still healthy
        self.assertEqual(self.h.dispatched_on, [])


class QueueAndThreadTests(unittest.TestCase):
    def test_pump_refuses_other_threads(self):
        h = Harness()
        try:
            errs = []

            def other():
                for fn in (lambda: h.t.pump(h.dispatch), h.t.begin_close, lambda: h.t.set_static({})):
                    try:
                        fn()
                    except RuntimeError as e:
                        errs.append(str(e))
                try:
                    h.t.suspended = True
                except RuntimeError as e:
                    errs.append(str(e))
            th = threading.Thread(target=other)
            th.start()
            th.join()
            self.assertEqual(len(errs), 4)
            self.assertTrue(h.t.healthy())
        finally:
            h.close()

    def test_dispatch_errors_recover_and_headers_are_sanitised(self):
        calls = []

        def dispatch(job):
            calls.append(job.path)
            if len(calls) == 1:
                raise ValueError("boom")
            if len(calls) == 2:
                return "not a response"
            return Response.json(200, {"ok": 1}, [("Access-Control-Allow-Origin", "*"), ("X-Ok", "1"),
                                                  ("X-Bad", "a\r\nInjected: 1")])
        h = Harness(dispatch)
        try:
            p = h.port
            r1 = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            r2 = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            r3 = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            self.assertEqual((r1[0], code(r1[2])), (500, "INTERNAL_ERROR"))
            self.assertEqual(r2[0], 500)
            self.assertEqual(r3[0], 200)
            self.assertNotIn("access-control-allow-origin", r3[1])
            self.assertNotIn("injected", r3[1])
            self.assertEqual(r3[1]["x-ok"], ["1"])
            self.assertEqual(h.t.stats()["dispatch_errors"], 2)
            self.assertNotIn(b"boom", r1[2])
        finally:
            h.close()

    def test_queue_full_returns_busy(self):
        h = Harness(queue_max=1, job_timeout=1.0, dispatch_grace=0.2, max_connections=8)
        try:
            p = h.port
            results = []
            ths = [threading.Thread(target=lambda: results.append(req(p, "POST", "/mcp", mcp_headers(p), b"{}")))
                   for _ in range(4)]
            for t in ths:
                t.start()
            for t in ths:
                t.join(10)
            codes = sorted(code(r[2]) for r in results)
            self.assertIn("TDFLOW_BUSY", codes)
            self.assertIn("TDFLOW_TIMEOUT", codes)       # nobody pumped
            self.assertEqual(h.dispatched_on, [])
        finally:
            h.close()

    def test_timed_out_job_is_never_dispatched(self):
        h = Harness(job_timeout=0.3, dispatch_grace=0.3)
        try:
            p = h.port
            st, _, body = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"), pump=False)
            self.assertEqual((st, code(body)), (504, "TDFLOW_TIMEOUT"))
            self.assertEqual(h.t.pump(h.dispatch), 0)     # the cancelled job is skipped
            self.assertEqual(h.dispatched_on, [])
        finally:
            h.close()

    def test_timeout_after_dispatch(self):
        def slow(job):
            time.sleep(0.8)
            return Response.json(200, {})
        h = Harness(slow, job_timeout=0.2, dispatch_grace=0.2)
        try:
            p = h.port
            st, _, body = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            self.assertEqual((st, code(body)), (504, "TDFLOW_TIMEOUT_AFTER_DISPATCH"))
        finally:
            h.close()

    def test_concurrent_requests_all_dispatched_on_main_thread(self):
        h = Harness(max_connections=16, queue_max=64)
        try:
            p = h.port
            res = []

            def burst():
                ths = [threading.Thread(target=lambda: res.append(req(p, "POST", "/mcp", mcp_headers(p), b"{}")))
                       for _ in range(40)]
                for t in ths:
                    t.start()
                for t in ths:
                    t.join(10)
            h.run(burst)
            ok = [r for r in res if r[0] == 200]
            self.assertGreaterEqual(len(ok), 30)
            self.assertTrue(all(r[0] in (200, 503) for r in res))
            self.assertEqual(set(h.dispatched_on), {threading.get_ident()})
        finally:
            h.close()

    def test_connection_cap_and_reaper(self):
        h = Harness(max_connections=1, max_connection_seconds=0.5, io_timeout=5.0)
        try:
            idle = socket.create_connection(("127.0.0.1", h.port))
            time.sleep(0.2)
            st, _, body = parse(raw(h.port, b"GET /setup/ HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % h.port))
            self.assertEqual((st, code(body)), (503, "TDFLOW_BUSY"))
            idle.settimeout(3)
            t0 = time.monotonic()
            try:
                self.assertEqual(idle.recv(10), b"")      # cut by the reaper
            except (ConnectionResetError, ConnectionAbortedError):
                pass
            self.assertLess(time.monotonic() - t0, 2.5)
            idle.close()
            time.sleep(0.1)
            self.assertEqual(req(h.port)[0], 200)
            self.assertGreaterEqual(h.t.stats().get("reaped", 0), 1)
        finally:
            h.close()


class Review4Tests(unittest.TestCase):
    """Regression tests for independent review 4 (docs/research/2026-11-22-s6-review.md)."""

    def _queue_one(self, h, path="/mcp", headers=None, method="POST"):
        p = h.port
        out = {}
        body = b"{}" if method == "POST" else b""
        th = threading.Thread(target=lambda: out.setdefault("r", req(p, method, path, headers or mcp_headers(p), body)))
        th.start()
        end = time.monotonic() + 3
        while h.t.stats()["queued"] == 0 and time.monotonic() < end:
            time.sleep(0.005)
        return th, out

    def test_h1_queued_job_not_dispatched_after_state_change_or_suspend(self):
        for change in ("verifying", "suspend"):
            h = Harness(job_timeout=3.0)
            try:
                th, out = self._queue_one(h)
                if change == "verifying":
                    h.state = ListenerState.VERIFYING
                else:
                    h.t.suspended = True
                self.assertEqual(h.t.pump(h.dispatch), 1)
                th.join(5)
                self.assertEqual(out["r"][0], 503)
                self.assertEqual(code(out["r"][2]), "TDFLOW_VERIFYING" if change == "verifying" else "SUSPENDED")
                self.assertEqual(h.dispatched_on, [], change)
            finally:
                h.close()

    def test_h1_pump_after_close_dispatches_nothing(self):
        h = Harness(job_timeout=3.0)
        th, out = self._queue_one(h)
        h.t.begin_close()
        h.t.pump(h.dispatch)
        th.join(5)
        self.assertEqual(code(out["r"][2]), "TDFLOW_CLOSING")
        self.assertEqual(h.dispatched_on, [])
        h.close()

    def test_m_non_ascii_or_huge_content_length_gets_a_response(self):
        h = Harness()
        try:
            for cl in ("²", "9" * 5000, "+1", "1e3", "0x10"):
                data = ("POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: %s\r\n\r\n" % (h.port, cl)).encode("utf-8")
                st = parse(raw(h.port, data))[0]
                self.assertIn(st, (400, 431), repr(cl[:5]))
            self.assertEqual(req(h.port)[0], 200)
        finally:
            h.close()

    def test_m_content_type_and_header_injection_blocked(self):
        def dispatch(job):
            if len(calls) == 0:
                calls.append(1)
                return Response(200, b"x", "text/html\r\nSet-Cookie: evil=1")
            return Response.json(200, {}, [("X-U", "☃"), ("Content-Security-Policy", "default-src *"),
                                           ("Content-Type", "text/html"), ("X-TDflow-Request-Id", "1")])
        calls = []
        h = Harness(dispatch)
        try:
            p = h.port
            r1 = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            self.assertEqual(r1[0], 500)
            self.assertNotIn("set-cookie", r1[1])
            r2 = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            self.assertEqual(r2[0], 200)
            self.assertNotIn("x-u", r2[1])
            self.assertEqual(len(r2[1]["content-security-policy"]), 1)
            self.assertNotIn("default-src *", r2[1]["content-security-policy"][0])
            self.assertEqual(r2[1]["content-type"], ["application/json"])
            self.assertEqual(len(r2[1]["x-tdflow-request-id"]), 1)
        finally:
            h.close()

    def test_m_unauthenticated_jobs_have_a_separate_quota(self):
        h = Harness(unauth_queue_max=1, job_timeout=1.0, dispatch_grace=0.1)
        try:
            p = h.port
            hdr = {"X-TDflow-Request": "1"}
            th, out = self._queue_one(h, "/setup/api/status", hdr, method="GET")
            self.assertEqual(h.t.stats()["queued"], 1)
            st, _, body = req(p, "GET", "/setup/api/status", hdr)
            self.assertEqual((st, code(body)), (503, "TDFLOW_BUSY"))
            # An authenticated MCP request still gets a queue slot.
            r = h.run(lambda: req(p, "POST", "/mcp", mcp_headers(p), b"{}"))
            self.assertEqual(r[0], 200)
            th.join(5)
        finally:
            h.close()

    def test_well_known_served_from_snapshot(self):
        h = Harness()
        try:
            h.t.set_static({"/.well-known/tdflowmcp": ("application/json", b'{"product":"TDflowMCP"}')})
            st, _, body = req(h.port, "GET", "/.well-known/tdflowmcp")
            self.assertEqual((st, body), (200, b'{"product":"TDflowMCP"}'))
            self.assertEqual(h.dispatched_on, [])
        finally:
            h.close()

    def test_l_no_parser_detail_or_100_continue_before_serving(self):
        h = Harness(state=ListenerState.VERIFYING)
        try:
            self.assertEqual(code(parse(raw(h.port, b"NONSENSE\r\n\r\n"))[2]), "TDFLOW_VERIFYING")
            self.assertEqual(parse(raw(h.port, b"BREW / HTTP/1.1\r\nHost: x\r\n\r\n"))[0], 503)
            data = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nExpect: 100-continue\r\nContent-Length: 2\r\n\r\n"
                    % h.port)
            out = raw(h.port, data)
            self.assertNotIn(b"100 Continue", out)
            self.assertEqual(parse(out)[0], 503)
        finally:
            h.close()

    def test_l_bearer_scheme_case_insensitive_single_value(self):
        a = TokenAuthenticator()
        a.add(TOKEN, "p")
        self.assertEqual(a("agent_token", {"Authorization": "bearer " + TOKEN}), "p")
        self.assertEqual(a("agent_token", {"Authorization": "BEARER  " + TOKEN}), "p")
        self.assertIsNone(a("agent_token", {"Authorization": "Basic " + TOKEN}))
        self.assertIsNone(a("admin_session", {"Authorization": "Bearer " + TOKEN}))

    def test_l_unreadable_other_listener_table_is_ambiguous(self):
        ev = bv.verdict(["127.0.0.1"], [], {}, sockname="127.0.0.1", others=None)
        self.assertIs(ev.verdict, bv.Verdict.AMBIGUOUS)


class Phase1ATests(unittest.TestCase):
    def test_mcp_disabled_before_auth_and_dispatch(self):
        h = Harness(mcp_enabled=False)
        try:
            p = h.port
            for hdr in (mcp_headers(p), {"Content-Type": "application/json"}):
                st, _, body = req(p, "POST", "/mcp", hdr, b"{}")
                self.assertEqual((st, code(body)), (503, "MCP_DISABLED"))
            for method, hdr in (("POST", dict(mcp_headers(p), Origin="http://evil.example")), ("GET", {}),
                                ("POST", {"Content-Type": "text/plain"}), ("OPTIONS", {"Origin": "http://evil.example"})):
                st, hd, body = req(p, method, "/mcp", hdr, b"{}" if method == "POST" else b"")
                self.assertEqual((st, code(body)), (503, "MCP_DISABLED"), method)
                self.assertFalse([k for k in hd if k.startswith("access-control")])
            self.assertEqual(req(p, "POST", "/mcp", mcp_headers(p), b"{}", host="evil.example")[0], 403)
            self.assertEqual(h.dispatched_on, [])
            self.assertNotIn("auth_fail", h.t.stats())
        finally:
            h.close()

    def test_heartbeat_watchdog_closes_orphaned_listener(self):
        h = Harness(heartbeat_timeout=0.3)
        p = h.port
        end = time.monotonic() + 5
        while not h.t.is_closed() and time.monotonic() < end:
            time.sleep(0.05)
        self.assertTrue(h.t.is_closed())
        self.assertEqual(h.t.close_reason, "heartbeat_lost")
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", p), timeout=1).close()

    def test_pumping_keeps_the_watchdog_quiet(self):
        h = Harness(heartbeat_timeout=0.3)
        try:
            end = time.monotonic() + 1.0
            while time.monotonic() < end:
                h.t.pump(h.dispatch)
                time.sleep(0.02)
            self.assertTrue(h.t.healthy())
            before = h.t.last_activity
            req(h.port)                                    # a page load is not activity (owner D20)
            self.assertEqual(h.t.last_activity, before)
            h.t.touch_activity()
            self.assertGreater(h.t.last_activity, before)
        finally:
            h.close()


class CloseRestartTests(unittest.TestCase):
    def test_close_fails_pending_and_releases_port_then_restart(self):
        h = Harness(job_timeout=5.0)
        p = h.port
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("r", req(p, "POST", "/mcp", mcp_headers(p), b"{}")))
        th.start()
        end = time.monotonic() + 3
        while h.t.stats()["queued"] == 0 and time.monotonic() < end:
            time.sleep(0.01)
        h.close()
        th.join(5)
        self.assertEqual(code(out["r"][2]), "TDFLOW_CLOSING")
        self.assertTrue(h.t.is_closed())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", p), timeout=1).close()
        h2 = EmbeddedHTTPTransport("127.0.0.1", p, state_fn=lambda: ListenerState.SERVING)
        h2.set_static({"/setup/": ("text/html", b"again")})
        h2.start()
        try:
            self.assertEqual(req(p)[2], b"again")
        finally:
            h2.begin_close()

    def test_begin_close_is_idempotent_and_requests_get_closing(self):
        h = Harness()
        h.t.begin_close()
        h.t.begin_close()
        self.assertEqual(h.t.effective_state(), ListenerState.CLOSING)
        h.close()
        self.assertTrue(h.t.is_closed())

    def test_error_helper(self):
        r = error(418, "X")
        self.assertEqual(json.loads(r.body), {"error": "X"})


if __name__ == "__main__":
    unittest.main()
