"""Phase 2A increment 3: dedicated capabilities (owner D18), default node placement, the gate's admin read."""
import os
import shutil
import tempfile
import unittest

from tdflowmcp import gate, layout
from tdflowmcp.net.bindverify import ListenerState
from tdflowmcp.identity import Identity
from tdflowmcp.policy import CAP_MEMORY_REPO_WRITE, CAPABILITIES, Agent, Policy
from tdflowmcp.store import Store


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-cap-")
        self.store = Store(os.path.join(self.dir, "d")).open()
        self.idn = Identity(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_none_by_default_and_persisted_when_issued(self):
        a, _ = self.idn.create_agent("plain", "builder", ["/project1/**"])
        b, tok = self.idn.create_agent("repo", "builder", ["/project1/**"], [CAP_MEMORY_REPO_WRITE])
        self.assertEqual(a.capabilities, frozenset())
        self.assertEqual(b.capabilities, frozenset({CAP_MEMORY_REPO_WRITE}))
        self.store.close()
        self.store = Store(os.path.join(self.dir, "d")).open()          # a restart
        idn = Identity(self.store)
        self.assertEqual(idn.agents[b.id].capabilities, frozenset({CAP_MEMORY_REPO_WRITE}))
        self.assertEqual(idn.agents[a.id].capabilities, frozenset())
        self.assertEqual(idn.agent_for_token(idn.authenticate(tok)).capabilities, frozenset({CAP_MEMORY_REPO_WRITE}))
        pub = {r["id"]: r for r in idn.public_list()}
        self.assertEqual((pub[a.id]["capabilities"], pub[b.id]["capabilities"]), ([], [CAP_MEMORY_REPO_WRITE]))

    def test_only_known_capabilities_and_only_for_roles_that_can_update(self):
        with self.assertRaises(ValueError):
            self.idn.create_agent("x", "builder", ["/project1/**"], ["memory:decisions:write"])
        with self.assertRaises(ValueError):
            self.idn.create_agent("x", "observer", ["/project1/**"], [CAP_MEMORY_REPO_WRITE])
        with self.assertRaises(ValueError):
            Agent("a", "n", "builder", True, (), frozenset({"anything"}))
        self.assertEqual(self.idn.agents, {})                              # nothing half-created

    def test_unknown_stored_capability_grants_nothing(self):
        a, _ = self.idn.create_agent("plain", "builder", ["/project1/**"])
        self.store._cc().execute("INSERT INTO capabilities(agent_id, name) VALUES (?, ?)", (a.id, "future:cap"))
        self.idn.reload()
        self.assertEqual(self.idn.agents[a.id].capabilities, frozenset())

    def test_three_tuple_preload_still_loads(self):                        # rows from an older worker shape
        rows = self.store.load_identity()
        self.assertEqual(len(rows), 4)
        self.assertEqual(Identity(self.store, preload=rows[:3]).agents, {})

    def test_policy_version_reflects_capabilities(self):
        pol = Policy("/project1")
        plain = Agent("a", "n", "builder", True, ())
        capped = Agent("a", "n", "builder", True, (), frozenset({CAP_MEMORY_REPO_WRITE}))
        self.assertNotEqual(pol.version(plain), pol.version(capped))
        self.assertEqual(CAPABILITIES, frozenset({"memory:repo:write"}))


class LayoutTests(unittest.TestCase):
    def test_empty_network(self):
        self.assertEqual(layout.default_position([]), (0, 0))

    def test_right_of_the_rightmost_node_on_its_row(self):
        boxes = [(0, 0, 130, 90), (200, -150, 130, 90), (400, 50, 160, 90)]
        self.assertEqual(layout.default_position(boxes), (400 + 160 + layout.GAP, 50))
        self.assertEqual(layout.default_position([(0, 0, 130, 90)]), (180, 0))

    def test_never_overlaps_and_stays_in_range(self):
        boxes = [(i * 150, (i % 3) * 100, 130, 90) for i in range(40)]
        x, y = layout.default_position(boxes)
        self.assertTrue(all(x >= bx + bw for bx, _, bw, _ in boxes))
        self.assertEqual(layout.default_position([(10 ** 9, 5, 100, 50)]), (layout.LIMIT, 5))
        self.assertEqual(layout.default_position([("a", 0, 1, 1), (0, 0, 10)]), (0, 0))   # junk is ignored


class GateTests(unittest.TestCase):
    def test_memory_status_is_an_admin_read_with_csrf(self):
        cfg = gate.GateConfig(port=9480)
        h = {"Host": "127.0.0.1:9480", "X-TDflow-Request": "1"}
        d = gate.decide("GET", "/setup/api/memory", h, ListenerState.SERVING, cfg)
        self.assertEqual((d.allow, d.route, d.next), (True, "api", "admin_session+csrf"))
        d = gate.decide("POST", "/setup/api/memory/migrate", dict(h, Origin="http://127.0.0.1:9480"), ListenerState.SERVING, cfg)
        self.assertEqual((d.allow, d.code), (False, "MISSING_CSRF"))
        d = gate.decide("POST", "/setup/api/memory/migrate",
                        dict(h, **{"Origin": "http://127.0.0.1:9481", "X-TDflow-CSRF": "x"}), ListenerState.SERVING, cfg)
        self.assertEqual((d.allow, d.code), (False, "BAD_ORIGIN"))


if __name__ == "__main__":
    unittest.main()
