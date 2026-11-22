"""Component controller state machine (src/tdflowmcp/controller.py) with injected fakes."""
import unittest

from tdflowmcp.controller import CompState, Controller, Deps
from tdflowmcp.net import bindverify as bv
from tdflowmcp.net.pyhttp import TransportError


class Done:
    def __init__(self, value):
        self.value = value

    def done(self):
        return True

    def result(self):
        return self.value


class Pending(Done):
    def done(self):
        return False


class FakeTransport:
    def __init__(self, port, state_fn, fail=None):
        self.port, self.state_fn, self.fail = port, state_fn, fail
        self.started = self.closing = False
        self.pumps = 0
        self.alive = True
        self.last_activity = 0.0
        self.close_reason = ""

    def start(self):
        if self.fail:
            raise TransportError(self.fail)
        self.started = True
        return ("127.0.0.1", self.port)

    def sockname(self):
        return ("127.0.0.1", self.port)

    def pump(self, dispatch):
        self.pumps += 1

    def begin_close(self):
        self.closing = True

    def is_closed(self):
        return self.closing

    def healthy(self):
        return self.alive and not self.closing

    def stats(self):
        return {"dispatched": 0}


class World:
    def __init__(self):
        self.now = 100.0
        self.others = []
        self.own = ([], [])
        self.ev = bv.verdict(["127.0.0.1"], [], {}, sockname="127.0.0.1", others=[])
        self.launches = []
        self.launch_ok = True
        self.transports = []
        self.bind_fail = None
        self.evidence_calls = 0

    def deps(self):
        def make(port, state_fn):
            t = FakeTransport(port, state_fn, self.bind_fail)
            self.transports.append(t)
            return t

        def ev(port, host):
            self.evidence_calls += 1
            return Done(self.ev)

        def launch(url):
            self.launches.append(url)
            return Done(self.launch_ok)
        return Deps(make_transport=make, start_evidence=ev, own_listeners=lambda p: self.own,
                    other_listeners=lambda p: self.others, probe_owner=lambda p: Done("0.1.0"),
                    launch=launch, now=lambda: self.now)


def serve(w, c):
    c.open_setup()
    c.tick(lambda j: None)        # evidence -> SETUP, launch started
    c.tick(lambda j: None)        # launch result consumed
    return c


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.c = Controller(self.w.deps())

    def test_off_until_user_action(self):
        for _ in range(5):
            self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.OFF)
        self.assertEqual(self.w.transports, [])

    def test_open_verifies_then_serves_then_launches(self):
        self.assertEqual(self.c.open_setup(), "starting")
        self.assertEqual(self.c.state, CompState.STARTING)
        self.assertIs(self.w.transports[0].state_fn(), bv.ListenerState.VERIFYING)
        self.assertEqual(self.w.launches, [])                 # never before SERVING
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.SETUP)
        self.assertIs(self.w.transports[0].state_fn(), bv.ListenerState.SERVING)
        self.assertEqual(self.w.launches, ["http://127.0.0.1:9480/setup/"])
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.launch_status, "launched")

    def test_reopen_while_serving_launches_again_and_reports_failure(self):
        serve(self.w, self.c)
        self.w.launch_ok = False
        self.c.open_setup()
        self.c.tick(lambda j: None)
        self.c.tick(lambda j: None)
        self.assertEqual(len(self.w.launches), 2)
        self.assertIn("could not be opened", self.c.message)
        self.assertIn("http://127.0.0.1:9480/setup/", self.c.message)

    def test_failed_verification_closes_and_errors(self):
        self.w.ev = bv.verdict(["0.0.0.0"], [], {})
        self.c.open_setup()
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.STOPPING)
        self.assertTrue(self.w.transports[0].closing)
        self.c.tick(lambda j: None)
        self.assertEqual((self.c.state, self.c.error_code), (CompState.ERROR, "BIND_UNVERIFIED"))
        self.assertEqual(self.w.launches, [])
        for _ in range(3):
            self.c.tick(lambda j: None)
        self.assertEqual(len(self.w.transports), 1)           # never restarted automatically

    def test_port_in_use_by_other_process_never_starts(self):
        self.w.others = [("0.0.0.0", 9480, 4242)]
        self.assertEqual(self.c.open_setup(), "refused")
        self.assertEqual((self.c.state, self.c.error_code), (CompState.ERROR, "PORT_IN_USE"))
        self.assertEqual(self.w.transports, [])
        self.c.tick(lambda j: None)
        self.assertIn("TDflowMCP", self.c.portstatus)          # owner probe answer (display only)
        self.assertEqual(self.c.port, 9480)

    def test_unreadable_table_never_starts(self):
        self.w.others = None
        self.c.open_setup()
        self.assertEqual(self.c.error_code, "BIND_UNVERIFIED")
        self.assertEqual(self.w.transports, [])

    def test_bind_error(self):
        self.w.bind_fail = "PORT_IN_USE"
        self.c.open_setup()
        self.assertEqual((self.c.state, self.c.error_code), (CompState.ERROR, "PORT_IN_USE"))

    def test_stop_confirms_with_os_table(self):
        serve(self.w, self.c)
        self.w.own = (["127.0.0.1"], [])
        self.c.stop()
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.STOPPING)     # socket still in the table
        self.w.own = ([], [])
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.OFF)
        self.assertIsNone(self.c.transport)

    def test_close_unconfirmed_escalates(self):
        serve(self.w, self.c)
        self.w.own = (["127.0.0.1"], [])
        self.c.stop()
        for _ in range(20):                                    # many frames but < 3 s: keep checking
            self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.STOPPING)
        self.w.now += 4
        self.c.tick(lambda j: None)
        self.assertEqual((self.c.state, self.c.error_code), (CompState.ERROR, "CLOSE_UNCONFIRMED"))
        self.assertEqual(self.c.open_setup(), "stuck")

    def test_port_change_while_serving_stops_and_applies_later(self):
        serve(self.w, self.c)
        self.assertEqual(self.c.set_port(9482), "stopping")
        self.c.tick(lambda j: None)
        self.assertEqual((self.c.state, self.c.port), (CompState.OFF, 9482))
        self.assertIn("9482", self.c.message)
        self.assertEqual(len(self.w.transports), 1)           # no automatic restart on the new port
        self.c.open_setup()
        self.assertEqual(self.w.transports[-1].port, 9482)

    def test_port_change_clears_port_in_use_error(self):
        self.w.others = [("127.0.0.1", 9480, 1)]
        self.c.open_setup()
        self.w.others = []
        self.assertEqual(self.c.set_port(9481), "set")
        self.assertEqual(self.c.state, CompState.OFF)
        self.assertEqual(self.c.set_port(80), "rejected")

    def test_reverify_failure_closes(self):
        serve(self.w, self.c)
        self.w.now += 11
        self.w.ev = bv.verdict(["127.0.0.1"], [], {}, others=[("0.0.0.0", 9480, 7)])
        self.c.tick(lambda j: None)                            # starts re-verification
        self.c.tick(lambda j: None)                            # applies it
        self.assertEqual(self.c.state, CompState.STOPPING)
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.error_code, "PORT_IN_USE")
        self.assertEqual(self.c.reverifies, 1)

    def test_reverify_pass_keeps_serving(self):
        serve(self.w, self.c)
        for _ in range(3):
            self.w.now += 11
            self.c.tick(lambda j: None)
            self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.SETUP)
        self.assertGreaterEqual(self.c.reverifies, 2)

    def test_transport_failure_and_idle(self):
        serve(self.w, self.c)
        self.w.transports[0].alive = False
        self.c.tick(lambda j: None)
        self.c.tick(lambda j: None)
        self.assertEqual((self.c.state, self.c.error_code), (CompState.ERROR, "TRANSPORT_FAILED"))
        self.c.stop()                                          # user acknowledges the error
        self.assertEqual(self.c.state, CompState.OFF)
        serve(self.w, self.c)
        self.w.now += 31 * 60
        self.c.tick(lambda j: None)
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.OFF)
        self.assertIn("idle", self.c.message)

    def test_pump_only_while_starting_or_serving(self):
        self.c.tick(lambda j: None)
        serve(self.w, self.c)
        n = self.w.transports[0].pumps
        self.c.stop()
        self.c.tick(lambda j: None)
        self.assertEqual(self.w.transports[0].pumps, n)

    def test_hung_evidence_closes_while_starting(self):
        self.w.deps_ev = None
        c = Controller(self.w.deps())
        c.d.start_evidence = lambda port, host: Pending(None)
        c.open_setup()
        c.tick(lambda j: None)
        self.assertEqual(c.state, CompState.STARTING)
        self.w.now += 6
        c.tick(lambda j: None)
        self.assertEqual(c.state, CompState.STOPPING)
        c.tick(lambda j: None)
        self.assertEqual(c.error_code, "BIND_UNVERIFIED")
        self.assertIn("evidence_timeout", [r for e in c.events for r in e.get("reasons", [])])

    def test_hung_reverify_closes_while_serving(self):
        serve(self.w, self.c)
        self.c.d.start_evidence = lambda port, host: Pending(None)
        self.w.now += 11
        self.c.tick(lambda j: None)                            # re-verify starts and hangs
        self.w.now += 6
        self.c.tick(lambda j: None)
        self.assertEqual(self.c.state, CompState.STOPPING)

    def test_logged_reasons_are_redacted(self):
        self.w.ev = bv.verdict(["127.0.0.1"], [], {"192.0.2.7": "connected"}, others=[("0.0.0.0", 9480, 4242)])
        self.c.open_setup()
        self.c.tick(lambda j: None)
        text = repr(list(self.c.events))
        self.assertNotIn("192.0.2.7", text)
        self.assertNotIn("4242", text)
        self.assertIn("non_loopback_connect", text)

    def test_pending_port_applied_after_any_close(self):
        serve(self.w, self.c)
        self.c.stop()
        self.c.set_port(9490)                                  # changed while STOPPING
        self.c.tick(lambda j: None)
        self.assertEqual((self.c.state, self.c.port), (CompState.OFF, 9490))

    def test_reserved_states_never_entered_and_snapshot(self):
        serve(self.w, self.c)
        snap = self.c.snapshot()
        self.assertEqual(snap["mcp"], "disabled (Phase 1A)")
        self.assertNotIn(self.c.state, (CompState.ENABLED, CompState.SUSPENDED))
        self.assertEqual(snap["url"], "http://127.0.0.1:9480/setup/")


if __name__ == "__main__":
    unittest.main()
