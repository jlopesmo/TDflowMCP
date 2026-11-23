"""Phase 2B: approvals, pure rules (security-model §4): identical re-call, targets, expiry, single use, decision MAC."""
import unittest

from tdflowmcp import approvals as A

KEY = b"k" * 32
TARGETS = [["delete", "/project1/fx"], ["delete", "/project1/fx/a"]]


def approval(now=1000.0, **over):
    text, sha = A.digest("tdflow_delete_operator", {"path": "/project1/fx"})
    a = {"id": "apr_" + "A" * 16, "agent_id": "agt_a", "token_id": "tok", "tool": "tdflow_delete_operator", "args": text,
         "args_sha256": sha, "targets": A.targets_key(TARGETS), "policy_version": "pv1", "created_at": "t",
         "expires_at": now + A.EXPIRY_S, "state": "pending", "reasons": '["deleting more than 50 operators"]'}
    a.update(over)
    return a


def approved(now=1000.0, **over):
    a = approval(now, **over)
    a.update(state="approved", decided_at="2026-11-23T00:00:00Z")
    a["mac"] = A.decision_mac(KEY, a, "approved", a["decided_at"])
    return a


class DigestTests(unittest.TestCase):
    def test_same_arguments_same_digest_whatever_the_key_order(self):
        a = A.digest("tdflow_set_parameters", {"path": "/p", "expressions": {"tx": "absTime.seconds", "ty": "1"}})
        b = A.digest("tdflow_set_parameters", {"expressions": {"ty": "1", "tx": "absTime.seconds"}, "path": "/p"})
        self.assertEqual(a, b)
        self.assertNotEqual(a[1], A.digest("tdflow_set_parameters", {"path": "/p", "expressions": {"tx": "absTime.seconds ", "ty": "1"}})[1])
        self.assertNotEqual(a[1], A.digest("tdflow_set_dat_text", {"path": "/p", "expressions": {"tx": "absTime.seconds", "ty": "1"}})[1])
        self.assertIn("absTime.seconds", a[0])                          # the complete text is what the user sees

    def test_unrepresentable_too_large_or_secret_arguments_cannot_be_approved(self):
        for args, code in (({"v": float("nan")}, "INVALID_ARGS"), ({"v": {1, 2}}, "INVALID_ARGS"),
                           ({"text": "x" * (A.MAX_ARGS_CHARS + 1)}, "APPROVAL_TOO_LARGE"),
                           ({"text": "token tdf_" + "A" * 12 + "." + "b" * 43}, "APPROVAL_REFUSED")):
            with self.assertRaises(A.ApprovalError) as c:
                A.digest("tdflow_set_dat_text", args)
            self.assertEqual(c.exception.code, code)
        self.assertRegex(A.new_id(), A.ID)


class RedemptionTests(unittest.TestCase):
    def check(self, a, **kw):
        args = dict(agent_id="agt_a", tool="tdflow_delete_operator", args_sha=a["args_sha256"], targets=TARGETS, now=1001.0)
        args.update(kw)
        return A.check_redemption(a, KEY, args["agent_id"], args["tool"], args["args_sha"], args["targets"], args["now"],
                                  args.get("given_id"))

    def code(self, a, **kw):
        with self.assertRaises(A.ApprovalError) as c:
            self.check(a, **kw)
        return c.exception.code

    def test_identical_recall_of_an_approved_request_passes(self):
        self.assertIsNone(self.check(approved()))
        self.assertIsNone(self.check(approved(), given_id="apr_" + "A" * 16))

    def test_anything_not_identical_is_refused(self):
        a = approved()
        other = A.digest("tdflow_delete_operator", {"path": "/project1/other"})[1]
        self.assertEqual(self.code(a, args_sha=other), "APPROVAL_MISMATCH")
        self.assertEqual(self.code(a, agent_id="agt_b"), "APPROVAL_MISMATCH")
        self.assertEqual(self.code(a, tool="tdflow_set_dat_text"), "APPROVAL_MISMATCH")
        self.assertEqual(self.code(a, given_id="apr_" + "B" * 16), "APPROVAL_MISMATCH")

    def test_targets_are_recompared_at_redemption(self):                  # TOCTOU: the project changed since
        self.assertEqual(self.code(approved(), targets=TARGETS + [["delete", "/project1/fx/new"]]), "APPROVAL_TARGETS_CHANGED")
        self.assertEqual(self.code(approved(), targets=[["delete", "/project1/renamed"]]), "APPROVAL_TARGETS_CHANGED")

    def test_states_and_expiry(self):
        self.assertEqual(self.code(approval()), "APPROVAL_PENDING")
        self.assertEqual(self.code(approved(), now=1000.0 + A.EXPIRY_S), "APPROVAL_EXPIRED")
        for st in ("denied", "redeemed", "cancelled"):
            self.assertEqual(self.code(dict(approved(), state=st)), "APPROVAL_" + st.upper())
        self.assertEqual(A.usable(dict(approved(), state="weird"), 1001.0), "cancelled")

    def test_a_row_edited_in_the_database_is_not_an_approval(self):       # decision MAC with the private key
        forged = approval(state="approved", decided_at="2026-11-23T00:00:00Z", mac="0" * 64)
        self.assertEqual(self.code(forged), "APPROVAL_INVALID")
        self.assertEqual(self.code(approval(state="approved", decided_at="x")), "APPROVAL_INVALID")
        moved = approved()
        moved["expires_at"] += 3600                                       # extending the expiry breaks the MAC
        self.assertEqual(self.code(moved), "APPROVAL_INVALID")
        swapped = approved()
        swapped["policy_version"] = "pv2"
        self.assertEqual(self.code(swapped), "APPROVAL_INVALID")
        self.assertNotEqual(A.decision_mac(KEY, approved(), "approved", "t"), A.decision_mac(KEY, approved(), "denied", "t"))
        self.assertNotEqual(A.decision_mac(KEY, approved(), "approved", "t"), A.decision_mac(b"x" * 32, approved(), "approved", "t"))


class ViewTests(unittest.TestCase):
    def test_agent_sees_only_state_and_next_step(self):
        s = A.summary_for_agent(approval(), 1001.0)
        self.assertEqual((s["state"], s["expires_in_s"]), ("pending", A.EXPIRY_S - 1))
        self.assertNotIn("args", s)
        self.assertIn("identical call", A.summary_for_agent(approved(), 1001.0)["next"])
        d = A.summary_for_agent(approval(state="denied", note="no"), 1001.0)
        self.assertEqual((d["state"], d["note"], d["expires_in_s"]), ("denied", "no", 0))
        self.assertEqual(A.summary_for_agent(approval(), 1000.0 + A.EXPIRY_S + 1)["state"], "expired")

    def test_user_sees_the_complete_arguments_and_targets(self):
        v = A.view_for_user(approval(), 1001.0)
        self.assertEqual(v["args"], '{"path":"/project1/fx"}')
        self.assertEqual(v["targets"], TARGETS)
        self.assertEqual(v["reasons"], ["deleting more than 50 operators"])
        self.assertTrue(v["agent_text_untrusted"])
        self.assertEqual(A.view_for_user(approval(targets="junk", reasons="junk"), 1001.0)["targets"], [])


if __name__ == "__main__":
    unittest.main()
