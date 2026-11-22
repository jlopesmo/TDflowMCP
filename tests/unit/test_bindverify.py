import unittest

from tests.unit import SRC  # noqa: F401  (adds src/ to sys.path)
from tdflowmcp.net.bindverify import (BindGate, ListenerState, Verdict, capability_check, verdict)

S5_WEBSERVER_PARS_2025_32440 = ["pageindex", "active", "restart", "port", "callbacks", "secure", "privatekey",
                                "certificate", "password", "verifyclient", "minprotocol", "language",
                                "extension", "customext", "wordwrap"]
OK_CAP = capability_check(["localaddress"])


class CapabilityTests(unittest.TestCase):
    def test_s5_build_without_localaddress_must_not_activate(self):
        ev = capability_check(S5_WEBSERVER_PARS_2025_32440)
        self.assertIs(ev.verdict, Verdict.FAIL)
        g = BindGate()
        self.assertEqual(g.request_activation(ev, user_initiated=True), "do_not_activate")
        self.assertEqual((g.state, g.error_code), (ListenerState.CLOSED, "BIND_UNSUPPORTED"))

    def test_build_with_localaddress_may_activate_in_verifying(self):
        g = BindGate()
        self.assertEqual(g.request_activation(OK_CAP, user_initiated=True), "activate_verifying")
        self.assertIs(g.state, ListenerState.VERIFYING)
        self.assertFalse(g.serving)


class VerdictTests(unittest.TestCase):
    def test_loopback_only_passes(self):
        self.assertIs(verdict(["127.0.0.1"], [], {"10.0.0.5": "timeout"}).verdict, Verdict.PASS)
        self.assertIs(verdict(["127.0.0.1"], ["::1"], {}).verdict, Verdict.PASS)

    def test_s5_observed_all_interfaces_fails(self):
        ev = verdict(["0.0.0.0"], [], {"10.0.0.5": "connected"})
        self.assertIs(ev.verdict, Verdict.FAIL)
        self.assertIn("non_loopback_listener:0.0.0.0", ev.reasons)
        self.assertIn("non_loopback_connect:10.0.0.5", ev.reasons)

    def test_non_loopback_variants_fail(self):
        for v4, v6 in ((["127.0.0.1"], ["::"]), (["127.0.0.2"], []), ([], ["::ffff:127.0.0.1"]),
                       ([], ["fe80::1"]), (["10.0.0.5"], [])):
            self.assertIs(verdict(v4, v6, {}).verdict, Verdict.FAIL, (v4, v6))

    def test_connected_probe_fails_even_if_table_looks_fine(self):
        self.assertIs(verdict(["127.0.0.1"], [], {"192.168.1.2": "connected"}).verdict, Verdict.FAIL)

    def test_unavailable_table_or_no_listener_is_ambiguous(self):
        self.assertIs(verdict(None, None, {}).verdict, Verdict.AMBIGUOUS)
        self.assertIs(verdict([], None, {}).verdict, Verdict.AMBIGUOUS)
        self.assertIs(verdict([], [], {"10.0.0.5": "timeout"}).verdict, Verdict.AMBIGUOUS)


class GateStateMachineTests(unittest.TestCase):
    def _gate(self, **kw):
        g = BindGate(max_attempts=3, **kw)
        g.request_activation(OK_CAP, now=0.0, user_initiated=True)
        return g

    def test_pass_serves(self):
        g = self._gate()
        self.assertEqual(g.on_evidence(verdict(["127.0.0.1"], [])), "serve")
        self.assertTrue(g.serving)

    def test_fail_closes_immediately_then_confirms_on_later_frame(self):
        g = self._gate()
        self.assertEqual(g.on_evidence(verdict(["0.0.0.0"], [])), "close_now")
        self.assertEqual((g.state, g.error_code), (ListenerState.CLOSING, "BIND_UNVERIFIED"))
        self.assertFalse(g.serving)
        # S5: the socket was still listed right after active=False
        self.assertEqual(g.confirm_closed(["0.0.0.0"], []), "recheck_close")
        self.assertEqual(g.confirm_closed([], []), "closed")
        self.assertIs(g.state, ListenerState.CLOSED)

    def test_close_unconfirmed_escalates_to_stuck(self):
        g = self._gate(max_close_checks=2)
        g.on_evidence(verdict(["0.0.0.0"], []))
        self.assertEqual(g.confirm_closed(["0.0.0.0"], []), "recheck_close")
        self.assertEqual(g.confirm_closed(None, None), "escalate")
        self.assertEqual((g.state, g.error_code), (ListenerState.STUCK, "CLOSE_UNCONFIRMED"))

    def test_closed_is_terminal_without_user_action(self):
        g = self._gate()
        g.on_evidence(verdict(["0.0.0.0"], []))
        g.confirm_closed([], [])
        # later passing evidence never re-opens it
        self.assertEqual(g.on_evidence(verdict(["127.0.0.1"], [])), "noop")
        self.assertFalse(g.serving)
        # no automatic re-activation (review 3, H1)
        self.assertEqual(g.request_activation(OK_CAP), "remain_closed")
        self.assertIs(g.state, ListenerState.CLOSED)
        # only an explicit user action may try again
        self.assertEqual(g.request_activation(OK_CAP, user_initiated=True), "activate_verifying")

    def test_never_reopen_while_closing_or_stuck_even_by_user(self):
        g = self._gate(max_close_checks=1)
        g.on_evidence(verdict(["0.0.0.0"], []))
        self.assertEqual(g.request_activation(OK_CAP, user_initiated=True), "remain_closed")
        g.confirm_closed(["0.0.0.0"], [])
        self.assertIs(g.state, ListenerState.STUCK)
        self.assertEqual(g.request_activation(OK_CAP, user_initiated=True), "remain_closed")

    def test_socket_not_up_yet_is_retried_bounded_then_closed(self):
        g = self._gate()
        self.assertEqual(g.on_evidence(verdict([], [])), "recheck_later")
        self.assertEqual(g.on_evidence(verdict([], [])), "recheck_later")
        self.assertEqual(g.on_evidence(verdict([], [])), "close_now")

    def test_wall_clock_deadline_closes(self):
        g = self._gate(max_verify_seconds=5.0)
        self.assertEqual(g.on_evidence(verdict([], []), now=1.0), "recheck_later")
        self.assertEqual(g.on_evidence(verdict([], []), now=6.0), "close_now")

    def test_table_unavailable_closes_without_retry(self):
        g = self._gate()
        self.assertEqual(g.on_evidence(verdict(None, None)), "close_now")

    def test_config_change_requires_reverification(self):
        g = self._gate()
        g.on_evidence(verdict(["127.0.0.1"], []))
        self.assertEqual(g.on_config_change(), "reverify")
        self.assertFalse(g.serving)


if __name__ == "__main__":
    unittest.main()
