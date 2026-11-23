"""Phase 4: scope leases — another agent's changes inside a leased scope are refused (L2); reads stay open; leases
end on release, expiry or when the holder is disabled; every lease change is audited."""
import time
import unittest

from tests.unit.test_approval_flow import Base, P

L = "tdflow_lease_scope"


class LeaseTests(Base):
    def test_lease_blocks_other_agents_changes_not_reads(self):
        err, out = self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        self.assertFalse(err, out)
        self.assertTrue(out["audit_seq"])
        err, out = self.call(self.BD, "tdflow_set_parameters", {"path": P, "values": {"opacity": 0.5}})
        self.assertEqual((out["code"], out["rule_id"]), ("LEASED", "L2"))
        self.assertIn("Op holds a lease on /project1/fx", out["message"])
        self.assertEqual(self.ad.executed, [])
        err, out = self.call(self.BD, "tdflow_get_operator", {"path": P})
        self.assertFalse(err)                                                 # reads stay open
        err, out = self.call(self.OP, "tdflow_set_parameters", {"path": P, "values": {"opacity": 0.5}})
        self.assertFalse(err, out)                                            # the holder works normally
        err, out = self.call(self.BD, L, {"scope": "/project1/fx/sub"})
        self.assertEqual(out["code"], "LEASED")                               # overlapping lease refused
        err, out = self.call(self.BD, L, {"scope": "/project1/fx", "release": True})
        self.assertEqual(out["code"], "NOT_FOUND")                            # only the holder releases
        err, out = self.call(self.OP, L, {"scope": "/project1/fx", "release": True})
        self.assertFalse(err, out)
        err, out = self.call(self.BD, "tdflow_set_parameters", {"path": P, "values": {"opacity": 0.4}})
        self.assertFalse(err, out)

    def test_expiry_disabled_holder_and_permissions(self):
        self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        self.eng.leases["/project1/fx"]["expires"] = time.time() - 1          # expired
        self.assertIsNone(self.eng.lease_holder(P, self.bd.id))
        self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        self.idn.set_enabled(self.op.id, False)                               # a disabled holder holds nothing
        self.assertIsNone(self.eng.lease_holder(P, self.bd.id))
        err, out = self.call(self.BD, L, {"scope": "/project1"})
        self.assertEqual(out["code"], "DENIED")                               # needs update on the scope

    def test_review17_root_renewal_undo_revocation_and_user_release(self):
        root_op, tok = self.idn.create_agent("Root", "operator", ["/project1/**"])
        ROOT = self.idn.authenticate(tok)
        err, out = self.call(ROOT, L, {"scope": "/project1"})
        self.assertEqual(out["code"], "DENIED")                               # the whole project (L4)
        err, out = self.call(self.BD, "tdflow_set_parameters", {"path": P, "values": {"opacity": 0.5}})
        self.assertFalse(err, out)                                            # builder's change, undoable
        self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        err, out = self.call(self.BD, "tdflow_undo", {})
        self.assertEqual(out["code"], "LEASED")                               # undo is a change like any other
        self.eng.leases["/project1/fx"]["since"] = time.time() - 4000
        err, out = self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        self.assertEqual(out["code"], "LEASE_LIMIT")                          # renewal capped
        self.assertTrue(self.eng.release_lease("/project1/fx"))               # the user's dashboard release
        self.assertIsNone(self.eng.lease_holder(P, self.bd.id))
        self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        for tid in [t for t, v in self.idn._snap.items() if v[2] == self.op.id]:
            self.idn.revoke(tid)                                              # its last token revoked
        self.assertIsNone(self.eng.lease_holder(P, self.bd.id))
        self.assertFalse(self.eng.leases_view()[0]["valid"])

    def test_overlapping_leases_cannot_both_be_granted(self):
        err, a = self.call(self.OP, L, {"scope": "/project1/fx", "ttl_s": 60})
        err, b = self.call(self.OP2, L, {"scope": "/project1/fx/level1", "ttl_s": 60})
        self.assertFalse(a.get("code"))
        self.assertEqual(b["code"], "LEASED")
        self.assertEqual(list(self.eng.leases), ["/project1/fx"])

    def test_a_lease_taken_between_plan_and_execute_stops_the_change(self):
        orig = self.ad.execute
        lease_now = {"done": False}

        def resolve_then_lease(tool, args):
            r = type(self.ad).resolve(self.ad, tool, args)
            if tool.name == "tdflow_set_parameters" and not lease_now["done"] and self._planned:
                lease_now["done"] = True
                self.eng.leases["/project1/fx"] = {"agent_id": self.op.id, "expires": time.time() + 60}
            self._planned = True
            return r
        self._planned = False
        self.ad.resolve = resolve_then_lease
        try:
            err, out = self.call(self.BD, "tdflow_set_parameters", {"path": P, "values": {"opacity": 0.3}})
        finally:
            self.ad.resolve = type(self.ad).resolve.__get__(self.ad)
            self.ad.execute = orig
        self.assertEqual(out["code"], "LEASED")
        self.assertEqual(self.ad.executed, [])


if __name__ == "__main__":
    unittest.main()
