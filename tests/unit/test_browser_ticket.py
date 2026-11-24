"""One-click browser sign-in (owner UX assignment D21, review 18): a single-use, short-lived ticket that TouchDesigner
puts in the fragment of the URL it opens; interception detection; the warm-up; rate limiting; the gate rules."""
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


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.a = A.AdminAuth(clock=self.c)

    def test_single_use_session_with_warm_up(self):
        t = self.a.issue_ticket()
        self.assertGreaterEqual(len(t), 40)
        st, (sid, csrf) = self.a.redeem_ticket(t)
        self.assertEqual(st, "ok")
        p = self.a.check("admin_session+csrf", hdrs(sid, csrf))
        self.assertTrue(p and p.startswith("admin:"))
        self.assertTrue(self.a.warming(p))                                  # read-only for the first seconds
        self.c.t += self.a.warmup_seconds + 0.1
        self.assertFalse(self.a.warming(p))
        self.assertFalse(self.a.warming("admin:touchdesigner"))

    def test_a_raced_ticket_ends_every_session(self):
        t = self.a.issue_ticket()
        st, (sid, _) = self.a.redeem_ticket(t)                             # another program was faster
        self.c.t += 1.0
        st2, creds = self.a.redeem_ticket(t)                               # the real page, with the same ticket
        self.assertEqual((st2, creds), ("intercepted", None))
        self.assertIsNone(self.a.check("admin_session", hdrs(sid)))        # the racer's session ended
        self.assertIsNotNone(self.a.intercepted_at)

    def test_a_much_later_replay_is_just_refused(self):
        t = self.a.issue_ticket()
        st, (sid, _) = self.a.redeem_ticket(t)
        self.c.t += self.a.replay_seconds + 1                              # e.g. the URL reopened from history
        self.assertEqual(self.a.redeem_ticket(t)[0], "invalid")
        self.assertTrue(self.a.check("admin_session", hdrs(sid)))          # no false alarm, no sign-out

    def test_expiry_wrong_tickets_and_reissue(self):
        t = self.a.issue_ticket()
        self.c.t += self.a.ticket_seconds + 1
        self.assertEqual(self.a.redeem_ticket(t)[0], "invalid")           # expired
        t = self.a.issue_ticket()
        self.assertEqual(self.a.redeem_ticket("x" * 43)[0], "invalid")     # a wrong guess voids the ticket
        self.assertEqual(self.a.redeem_ticket(t)[0], "invalid")
        old = self.a.issue_ticket()
        self.a.issue_ticket()                                              # a new launch voids the older ticket
        self.assertEqual(self.a.redeem_ticket(old)[0], "invalid")

    def test_rate_limited_after_repeated_failures(self):
        codes = [self.a.redeem_ticket("y" * 43)[0] for _ in range(self.a.max_ticket_failures + 2)]
        self.assertEqual(codes[-1], "rate_limited")
        for bad in (None, "", "short", "z" * 500, 42):
            self.assertIn(self.a.redeem_ticket(bad)[0], ("invalid", "rate_limited"))
        self.c.t += 61
        t = self.a.issue_ticket()
        self.assertEqual(self.a.redeem_ticket(t)[0], "ok")                 # the limit only lasts a minute

    def test_one_session_at_a_time_and_logout(self):
        st, (sid1, _) = self.a.redeem_ticket(self.a.issue_ticket())
        self.c.t += 20
        st, (sid2, _) = self.a.redeem_ticket(self.a.issue_ticket())
        self.assertIsNone(self.a.check("admin_session", hdrs(sid1)))       # the older session ended
        p = self.a.check("admin_session", hdrs(sid2))
        self.a.logout(p[len("admin:"):])
        self.assertIsNone(self.a.check("admin_session", hdrs(sid2)))
        self.a.issue_ticket()
        self.a.logout_all()
        self.assertFalse(self.a.ticket_pending())


class GateTests(unittest.TestCase):
    def d(self, method, path, **h):
        base = {"Host": "127.0.0.1:9480", "X-TDflow-Request": "1"}
        base.update(h)
        return G.decide(method, path, {k.replace("_", "-"): v for k, v in base.items()}, ListenerState.SERVING,
                        G.GateConfig(port=9480))

    def test_ticket_route(self):
        ok = self.d("POST", "/setup/api/pair/ticket", Origin=O, Sec_Fetch_Site="same-origin")
        self.assertEqual((ok.allow, ok.next), (True, ""))
        self.assertEqual(self.d("POST", "/setup/api/pair/ticket").code, "BAD_ORIGIN")       # Origin required
        self.assertEqual(self.d("POST", "/setup/api/pair/ticket", Origin="http://evil").code, "BAD_ORIGIN")
        self.assertEqual(self.d("POST", "/setup/api/pair/ticket", Origin=O, Sec_Fetch_Site="cross-site").code,
                         "CROSS_SITE_FETCH")
        no_hdr = G.decide("POST", "/setup/api/pair/ticket", {"Host": "127.0.0.1:9480", "Origin": O},
                          ListenerState.SERVING, G.GateConfig(port=9480))
        self.assertEqual(no_hdr.code, "MISSING_REQUEST_HEADER")
        self.assertEqual(self.d("GET", "/setup/api/pair/ticket").code, "METHOD_NOT_ALLOWED")
        self.assertEqual(self.d("POST", "/setup/api/pair/ticket", Host="localhost:9480", Origin=O).code,
                         "NON_CANONICAL_HOST")
        self.assertEqual(G.decide("POST", "/setup/api/pair/ticket", {"Host": "127.0.0.1:9480", "X-TDflow-Request": "1",
                                                                     "Origin": O}, ListenerState.VERIFYING).status, 503)

    def test_no_session_resume_endpoint(self):
        r = self.d("GET", "/setup/api/session")                       # review 18: removed (sessionStorage instead)
        self.assertNotEqual(r.next, "admin_session")


if __name__ == "__main__":
    unittest.main()
