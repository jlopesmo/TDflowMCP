"""Browser sign-in (owner D22, ADR-0015 §browser): the rc2 one-click ticket is retired, because the ticket in the URL
fragment was visible on the browser's command line and a racing process could redeem it when the real page failed to
load. A page opened from TouchDesigner now pairs: the browser shows a match code, TouchDesigner shows the same code and
the user approves there. These tests cover the pairing state machine and the gate rules."""
import unittest

from tdflowmcp import admin as A
from tdflowmcp import gate as G
from tdflowmcp.net.bindverify import ListenerState

O = "http://127.0.0.1:9480"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def hdrs(cookie=None, csrf=None):
    h = {}
    if cookie:
        h["Cookie"] = f"{A.SESSION_COOKIE}={cookie}"
    if csrf:
        h["X-TDflow-CSRF"] = csrf
    return h


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.a = A.AdminAuth(clock=self.c)

    def test_no_ticket_api_remains(self):
        for name in ("issue_ticket", "redeem_ticket", "warming", "ticket_pending"):
            self.assertFalse(hasattr(self.a, name), name)

    def test_approve_in_touchdesigner_gives_one_session(self):
        secret, code = self.a.request()
        self.assertRegex(code, r"^[A-Z0-9]{3}-[A-Z0-9]{3}$")
        self.assertEqual(self.a.poll(A._h(secret))[0], "pending")
        self.assertTrue(self.a.approve())
        self.assertIsNotNone(self.a.last_browser_auth)
        st, (sid, csrf) = self.a.poll(A._h(secret))
        self.assertEqual(st, "approved")
        self.assertTrue(self.a.check("admin_session+csrf", hdrs(sid, csrf)).startswith("admin:"))
        self.assertEqual(self.a.poll(A._h(secret))[0], "unknown")         # handed out once

    def test_a_pending_request_is_never_replaced(self):
        self.a.request()
        with self.assertRaises(LookupError):
            self.a.request()                                              # a racing page cannot swap the code
        self.c.t += self.a.pending_seconds + 1
        self.a.request()                                                  # after expiry a new one is fine

    def test_rate_limited(self):
        for _ in range(self.a.max_per_minute):
            self.a.request()
            self.a.reject()
        with self.assertRaises(PermissionError):
            self.a.request()

    def test_sessions_expire(self):
        s, _ = self.a.request()
        self.a.approve()
        _, (sid, csrf) = self.a.poll(A._h(s))
        self.assertIsNotNone(self.a.check("admin_session+csrf", hdrs(sid, csrf)))
        self.c.t += self.a.session_seconds + 1
        self.assertIsNone(self.a.check("admin_session+csrf", hdrs(sid, csrf)))    # expired: refused
        self.assertIsNone(self.a.check("admin_session+csrf", hdrs(sid, "wrong")))
        self.assertEqual(self.a.active_sessions(), 0)

    def test_one_session_at_a_time_and_logout(self):
        s1, _ = self.a.request()
        self.a.approve()
        _, (sid1, _) = self.a.poll(A._h(s1))
        s2, _ = self.a.request()
        self.a.approve()
        _, (sid2, _) = self.a.poll(A._h(s2))
        self.assertIsNone(self.a.check("admin_session", hdrs(sid1)))
        p = self.a.check("admin_session", hdrs(sid2))
        self.a.logout(p[len("admin:"):])
        self.assertIsNone(self.a.check("admin_session", hdrs(sid2)))
        self.a.request()
        self.a.logout_all()
        self.assertIsNone(self.a.pending_view())


class GateTests(unittest.TestCase):
    def d(self, method, path, **h):
        base = {"Host": "127.0.0.1:9480", "X-TDflow-Request": "1"}
        base.update(h)
        return G.decide(method, path, {k.replace("_", "-"): v for k, v in base.items()}, ListenerState.SERVING,
                        G.GateConfig(port=9480))

    def test_ticket_route_is_retired(self):
        r = self.d("POST", "/setup/api/pair/ticket", Origin=O, Sec_Fetch_Site="same-origin")
        self.assertNotEqual((r.allow, r.next), (True, ""))                 # no longer an unauthenticated route
        self.assertNotEqual(r.next, "pair")

    def test_pair_route(self):
        ok = self.d("POST", "/setup/api/pair", Origin=O, Sec_Fetch_Site="same-origin")
        self.assertEqual((ok.allow, ok.next), (True, "pair"))
        self.assertEqual(self.d("POST", "/setup/api/pair", Origin="http://evil").code, "BAD_ORIGIN")

    def test_proof_route(self):
        """ADR-0015 / review 19: the helper's challenge; unauthenticated, GET only, strict nonce."""
        ok = self.d("GET", "/.well-known/tdflowmcp-proof/" + "a" * 32)
        self.assertEqual((ok.allow, ok.route, ok.next), (True, "proof", ""))
        self.assertFalse(self.d("POST", "/.well-known/tdflowmcp-proof/" + "a" * 32).allow)
        for bad in ("A" * 32, "a" * 31, "a" * 33, "g" * 32, ""):
            self.assertFalse(self.d("GET", "/.well-known/tdflowmcp-proof/" + bad).allow, bad)
        r = G.decide("GET", "/.well-known/tdflowmcp-proof/" + "a" * 32, {"Host": "evil.example"},
                     ListenerState.SERVING, G.GateConfig(port=9480))
        self.assertFalse(r.allow)                                    # Host is checked first

    def test_no_session_resume_endpoint(self):
        r = self.d("GET", "/setup/api/session")                       # review 18: removed (sessionStorage instead)
        self.assertNotEqual(r.next, "admin_session")


if __name__ == "__main__":
    unittest.main()
