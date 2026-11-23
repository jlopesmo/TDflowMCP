"""Phase 2B: permission editing (grants per agent) — role limits, deny wins, policy version, pending approvals."""
import os
import shutil
import tempfile
import unittest

from tdflowmcp.identity import Identity
from tdflowmcp.policy import Policy, Target
from tdflowmcp.store import Store


class GrantEditTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-grants-")
        self.st = Store(os.path.join(self.dir, "d")).open()
        self.idn = Identity(self.st)
        self.a, _ = self.idn.create_agent("A", "builder", ["/project1/fx/**"])
        self.pol = Policy("/project1")

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def allow(self, action, path):
        return self.pol.evaluate(self.idn.agents[self.a.id], [Target(action, path, change="param", param="x")]).allow

    def test_add_widens_and_remove_narrows(self):
        self.assertFalse(self.allow("update", "/project1/out/x"))
        v0 = self.pol.version(self.idn.agents[self.a.id])
        gid = self.idn.add_grant(self.a.id, "/project1/out/**", ["read", "update"])
        self.assertTrue(self.allow("update", "/project1/out/x"))
        self.assertNotEqual(self.pol.version(self.idn.agents[self.a.id]), v0)   # pending approvals stop matching
        self.assertTrue(self.idn.remove_grant(self.a.id, gid))
        self.assertFalse(self.allow("update", "/project1/out/x"))
        self.assertFalse(self.idn.remove_grant(self.a.id, gid))

    def test_deny_wins_and_role_limits_hold(self):
        self.idn.add_grant(self.a.id, "/project1/fx/secret/**", ["update", "delete"], "deny")
        self.assertFalse(self.allow("update", "/project1/fx/secret/a"))
        self.assertTrue(self.allow("update", "/project1/fx/other"))
        with self.assertRaises(ValueError):
            self.idn.add_grant(self.a.id, "/project1/**", ["execute"])          # a builder never gets execute
        with self.assertRaises(ValueError):
            self.idn.add_grant(self.a.id, "relative/**", ["read"])
        with self.assertRaises(ValueError):
            self.idn.add_grant(self.a.id, "/project1/**", [])
        with self.assertRaises(ValueError):
            self.idn.add_grant("agt_nobody", "/project1/**", ["read"])

    def test_a_grant_change_cancels_open_approvals(self):
        # Review 14: add + remove restores the same policy version; the open approvals must not come back.
        pv = self.pol.version(self.idn.agents[self.a.id])
        ap = self.st.approval_request(self.a.id, "tok", "tdflow_set_parameters", "{}", "sha", "[]", pv, ["A1"])["approval"]
        self.st.approval_decide(ap["id"], "approve")
        gid = self.idn.add_grant(self.a.id, "/project1/out/**", ["read"])
        self.idn.remove_grant(self.a.id, gid)
        self.assertEqual(self.pol.version(self.idn.agents[self.a.id]), pv)
        self.assertEqual(self.st.approval_get(ap["id"])["state"], "cancelled")

    def test_survives_a_restart(self):
        self.idn.add_grant(self.a.id, "/project1/out/**", ["read"])
        self.st.close()
        self.st = Store(os.path.join(self.dir, "d")).open()
        idn = Identity(self.st)
        self.assertEqual(sorted(g.scope for g in idn.agents[self.a.id].grants), ["/project1/fx/**", "/project1/out/**"])
        self.assertEqual([g["scope"] for g in self.st.grants_of(self.a.id)], ["/project1/fx/**", "/project1/out/**"])


if __name__ == "__main__":
    unittest.main()
