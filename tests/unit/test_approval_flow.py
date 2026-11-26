"""Phase 2B: the approval flow end to end in the engine (ADR-0005 gate: an approval executes only on the identical
re-call, with targets and policy re-checked), plus the store's approval rows."""
import json
import os
import shutil
import tempfile
import threading
import unittest

from tdflowmcp import approvals as APR
from tdflowmcp.engine import Adapter, Engine, Resolution, ToolFailure
from tdflowmcp.identity import Identity
from tdflowmcp.policy import Policy, Target
from tdflowmcp.store import Store
from tdflowmcp.tools import core_registry

ALL = [t.name for t in core_registry().all()]
P = "/project1/fx/level1"


class ExprAdapter(Adapter):
    """set_parameters with ``expressions`` is update + execute; a delete of ``big`` counts 80 operators."""

    def __init__(self):
        self.executed, self.count, self.extra_target, self.snapshot = [], 80, None, None
        self.approval_context = {}

    def resolve(self, tool, args):
        if tool.resolver == "undo":
            return Resolution([])
        path = args.get("path", "/project1/fx")
        if tool.resolver == "delete":
            return Resolution([Target("delete", path, change="delete", count=self.count)])
        if tool.resolver == "dat_text":                                   # an untagged DAT: TouchDesigner may run it
            return Resolution([Target("update", path, change="text"), Target("execute", path, change="text")],
                              {"snapshot": self.snapshot} if self.snapshot else {})
        if tool.resolver == "wire":
            dst = args.get("dst", path)
            return Resolution([Target("update", dst, change="wire"), Target("read", args.get("src", path)),
                               Target("execute", dst, change="wire")])
        ts = []
        for name in args.get("values", {}):
            ts.append(Target("update", path, change="param", param=name))
        for name in args.get("expressions", {}) or {}:
            ts += [Target("update", path, change="expression", param=name),
                   Target("execute", path, change="expression", param=name)]
        if self.extra_target:
            ts.append(Target("update", self.extra_target, change="param", param="x"))
        return Resolution(ts or [Target("read", path)], self.approval_context)

    def execute(self, tool, args, res, undo_label, readable):
        self.executed.append((tool.name, json.dumps(args, sort_keys=True)))
        return {"done": tool.name, "_audit": {"kind": "set_params"}}

    def undo(self, record, snapshot=None):
        return {"undone": True, "verified": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-apr-")
        self.store = Store(os.path.join(self.dir, "d")).open()
        self.idn = Identity(self.store)
        self.op, tok = self.idn.create_agent("Op", "operator", ["/project1/fx/**"])
        self.OP = self.idn.authenticate(tok)
        self.bd, tok = self.idn.create_agent("Builder", "builder", ["/project1/fx/**"])
        self.BD = self.idn.authenticate(tok)
        self.op2, tok = self.idn.create_agent("Op2", "operator", ["/project1/fx/**"])
        self.OP2 = self.idn.authenticate(tok)
        self.ad = ExprAdapter()
        self.pol = Policy("/project1")
        self.pol.approvals = True
        self.eng = Engine(self.store, self.idn, self.pol, core_registry(), self.ad, ALL, lambda: False)
        self.args = {"path": P, "expressions": {"opacity": "absTime.seconds % 1"}}

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, tok, name="tdflow_set_parameters", args=None, **kw):
        r = self.eng.call(tok, name, self.args if args is None else args, **kw)
        return r["isError"], r["structuredContent"]

    def decide(self, approval_id, decision="approved", note=""):
        return self.store.approval_decide(approval_id, decision, note)

    def kinds(self):
        return [(e["kind"], e["outcome"] or e["decision"]) for e in reversed(self.store.events(limit=100))]


class FlowTests(Base):
    def test_callback_review_is_private_bound_and_removed_on_redemption(self):
        preview = {"callback_source": "def onFrameStart(frame):\n    return\n", "active": {"value": False}}
        self.ad.approval_context = {"approval_review": preview, "approval_facts": [["op_id", 12]]}
        error, out = self.call(self.OP)
        self.assertTrue(error)
        self.assertNotIn("onFrameStart", json.dumps(out))
        aid = out["approval_id"]
        viewed = APR.view_for_user(self.store.approval_get(aid), 0)
        self.assertEqual(viewed["review"], preview)
        self.assertEqual(self.decide(aid)["code"], "APPROVAL_CHANGED")
        approved = self.store.approval_decide(aid, "approved", shown_review_sha=viewed["review_sha256"])
        self.assertTrue(approved["ok"])
        self.assertFalse(self.call(self.OP)[0])
        self.assertEqual(self.store.approval_get(aid)["review"], "")

    def test_callback_preview_tampering_fails_closed(self):
        self.ad.approval_context = {"approval_review": {"callback_source": "print(1)"}}
        aid = self.call(self.OP)[1]["approval_id"]
        self.store._cc().execute("UPDATE approvals SET review = ? WHERE id = ?", ('{"callback_source":"print(2)"}', aid))
        row = self.store.approval_get(aid)
        self.assertTrue(APR.view_for_user(row, 0)["review_invalid"])
        self.assertEqual(self.decide(aid)["code"], "APPROVAL_INVALID")
        self.assertEqual(self.ad.executed, [])

    def test_code_state_changes_after_redemption_and_intent_are_refused(self):
        self.ad.approval_context = {"approval_facts": [["op_id", 12]]}
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        steps = 0

        def main(step):
            nonlocal steps
            steps += 1
            if steps == 2:
                self.ad.approval_context = {"approval_facts": [["op_id", 13]]}
            return step()

        self.assertEqual(self.call(self.OP, main=main)[1]["code"], "APPROVAL_TARGETS_CHANGED")
        self.assertEqual(self.ad.executed, [])
        self.assertIn(("outcome", "error"), self.kinds())

    def test_token_rotation_cancels_the_old_tokens_approval(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        other_token = self.idn.authenticate(self.idn.issue_token(self.op.id))
        self.assertTrue(self.idn.revoke(self.OP))
        self.assertEqual(self.store.approval_get(aid)["state"], "cancelled")
        self.assertEqual(self.call(other_token)[1]["code"], "APPROVAL_REQUIRED")
        self.assertEqual(self.ad.executed, [])

    def test_runtime_generation_changes_refuse_historical_approval(self):
        self.ad.approval_context = {"approval_facts": [["op_id", 12]]}
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        self.eng._runtime_id = "new-runtime"
        self.assertEqual(self.call(self.OP)[1]["code"], "APPROVAL_TARGETS_CHANGED")
        self.assertEqual(self.ad.executed, [])

    def test_revocation_and_suspension_during_final_resolution_abort_execution(self):
        for setting, expected in (("suspend", "SUSPENDED"), ("revoke", "DENIED")):
            with self.subTest(setting=setting):
                aid = self.call(self.OP)[1]["approval_id"]
                self.decide(aid)
                resolve = self.ad.resolve
                steps = 0

                def during_resolve(tool, args):
                    nonlocal steps
                    res = resolve(tool, args)
                    steps += 1
                    if steps == 2:
                        if setting == "revoke":
                            self.idn.revoke(self.OP)
                        else:
                            self.eng._suspended = lambda: True
                    return res

                self.ad.resolve = during_resolve
                try:
                    self.assertEqual(self.call(self.OP)[1]["code"], expected)
                    self.assertEqual(self.ad.executed, [])
                finally:
                    self.ad.resolve = resolve
                    self.eng._suspended = lambda: False

    def test_token_recheck_follows_the_dispatch_lease_check(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        lease_check = self.eng._leased
        steps = 0

        def while_waiting_for_leases(agent, targets):
            nonlocal steps
            steps += 1
            if steps == 2:
                self.idn.revoke(self.OP)
            return lease_check(agent, targets)

        self.eng._leased = while_waiting_for_leases
        self.assertEqual(self.call(self.OP)[1]["code"], "DENIED")
        self.assertEqual(self.ad.executed, [])

    def test_request_then_approve_then_identical_recall_runs_once(self):     # the ADR-0005 gate
        err, out = self.call(self.OP)
        self.assertTrue(err)
        self.assertEqual((out["code"], out["status"], out["state"], out["rule_id"]), ("APPROVAL_REQUIRED", "pending_approval", "pending", "A1"))
        self.assertIn("sets an expression on parameter opacity", out["reasons"][0])
        aid = out["approval_id"]
        self.assertEqual(self.ad.executed, [])                             # nothing ran
        row = self.store.approval_get(aid)
        self.assertEqual(json.loads(row["args"]), self.args)               # the complete arguments, for the user
        self.assertEqual([t[:2] for t in json.loads(row["targets"])], [["update", P], ["execute", P]])
        err, out = self.call(self.OP)                                      # asking again creates nothing new
        self.assertEqual((out["code"], out["approval_id"]), ("APPROVAL_PENDING", aid))
        self.assertEqual(len(self.store.approvals_list()), 1)
        self.assertEqual(self.call(self.OP, "tdflow_approval_status", {"approval_id": aid})[1]["state"], "pending")
        self.assertTrue(self.decide(aid)["ok"])
        self.assertEqual(self.call(self.OP, "tdflow_approval_status", {"approval_id": aid})[1]["state"], "approved")
        self.assertEqual(self.ad.executed, [])                             # the status tool never executes
        err, out = self.call(self.OP)                                      # the identical re-call
        self.assertFalse(err, out)
        self.assertEqual(len(self.ad.executed), 1)
        ev = [e for e in self.store.events(limit=50) if e["kind"] in ("intent", "outcome") and e["tool"] == "tdflow_set_parameters"]
        self.assertEqual({e["approval_id"] for e in ev}, {aid})            # intent and outcome name the approval
        self.assertEqual(self.store.approval_get(aid)["state"], "redeemed")
        err, out = self.call(self.OP)                                      # single use: a third call asks again
        self.assertEqual(out["code"], "APPROVAL_REQUIRED")
        self.assertNotEqual(out["approval_id"], aid)
        self.assertEqual(len(self.ad.executed), 1)
        self.assertIn(("approval", "requested"), self.kinds())
        self.assertIn(("approval", "redeemed"), self.kinds())

    def test_any_difference_is_a_different_request(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        other = {"path": P, "expressions": {"opacity": "__import__('os').getcwd()"}}
        err, out = self.call(self.OP, args=other)                          # other code: not covered by the approval
        self.assertEqual(out["code"], "APPROVAL_REQUIRED")
        self.assertNotEqual(out["approval_id"], aid)
        err, out = self.call(self.OP2)                                     # another agent, same arguments
        self.assertEqual(out["code"], "APPROVAL_REQUIRED")
        self.assertEqual(self.ad.executed, [])
        self.assertEqual(self.call(self.OP2, "tdflow_approval_status", {"approval_id": aid})[1]["state"], "not_found")
        err, out = self.call(self.OP, meta={"tdflow": {"approval_id": "apr_" + "Z" * 16}})   # a wrong id is refused
        self.assertEqual(out["code"], "APPROVAL_MISMATCH")
        self.assertFalse(self.call(self.OP, meta={"tdflow": {"approval_id": aid}})[0])       # the right one runs

    def test_targets_are_re_resolved_at_redemption(self):                  # TOCTOU
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        self.ad.extra_target = "/project1/fx/other"                        # the call now touches something else
        err, out = self.call(self.OP)
        self.assertEqual(out["code"], "APPROVAL_TARGETS_CHANGED")
        self.assertEqual(self.ad.executed, [])
        self.assertEqual(self.store.approval_get(aid)["state"], "cancelled")   # must be asked again
        self.ad.extra_target = None
        self.assertEqual(self.call(self.OP)[1]["code"], "APPROVAL_REQUIRED")

    def test_policy_is_re_evaluated_at_the_current_version(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        self.pol.max_ops = 49                                              # any policy change since the request
        err, out = self.call(self.OP)
        self.assertEqual(out["code"], "APPROVAL_POLICY_CHANGED")
        self.assertEqual(self.ad.executed, [])
        self.pol.max_ops = 50
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        self.idn.revoke(self.OP)                                           # a revoked token never redeems
        r = self.eng.call(self.OP, "tdflow_set_parameters", self.args)
        self.assertEqual(r["structuredContent"]["code"], "DENIED")
        self.assertEqual(self.ad.executed, [])

    def test_denied_expired_and_queue_limits(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid, "denied", "not now")
        err, out = self.call(self.OP)
        self.assertEqual((out["code"], out["note"]), ("APPROVAL_DENIED", "not now"))
        self.assertEqual(len(self.store.approvals_list()), 1)              # a denied request is not re-queued at once
        self.assertEqual(self.decide(aid)["code"], "APPROVAL_NOT_PENDING")  # a decision is final
        # Expiry: an approved request that is not used in time cannot run.
        a2 = {"path": P, "expressions": {"opacity": "1+1"}}
        aid2 = self.call(self.OP, args=a2)[1]["approval_id"]
        self.decide(aid2)
        self.store._cc().execute("UPDATE approvals SET expires_at = 1 WHERE id = ?", (aid2,))
        err, out = self.call(self.OP, args=a2)
        self.assertEqual(out["code"], "APPROVAL_REQUIRED")                 # expired: a new request, nothing ran
        self.assertEqual(self.ad.executed, [])
        # An agent cannot flood the queue.
        codes = [self.call(self.OP2, args={"path": P, "expressions": {"opacity": str(i)}})[1]["code"] for i in range(7)]
        self.assertEqual(codes.count("APPROVAL_REQUIRED"), APR.MAX_PENDING_PER_AGENT)
        self.assertEqual(codes[-1], "APPROVAL_QUEUE_FULL")

    def test_a_row_edited_in_the_database_does_not_run(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.store._cc().execute("UPDATE approvals SET state='approved', decided_at='x', mac='00' WHERE id=?", (aid,))
        err, out = self.call(self.OP)
        self.assertEqual(out["code"], "APPROVAL_INVALID")
        self.assertEqual(self.ad.executed, [])

    def test_two_identical_recalls_cannot_both_run(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        facts = json.loads(self.store.approval_get(aid)["targets"])
        res = [self.store.approval_redeem(aid, self.op.id, "tdflow_set_parameters",
                                          APR.digest("tdflow_set_parameters", self.args)[1],
                                          facts, self.pol.version(self.op)) for _ in range(2)]
        self.assertEqual([r["ok"] for r in res], [True, False])
        self.assertEqual(res[1]["code"], "APPROVAL_REDEEMED")

    def test_roles_and_rules_that_approval_does_not_touch(self):
        err, out = self.call(self.BD)                                      # a builder has no execute at all
        self.assertEqual((out["code"], out["rule_id"]), ("DENIED", "R1"))
        self.assertEqual(self.store.approvals_list(), [])
        err, out = self.call(self.OP, args={"path": "/project1/out", "expressions": {"opacity": "1"}})   # outside scope
        self.assertEqual((out["code"], out["rule_id"]), ("DENIED", "G0"))
        err, out = self.call(self.OP, args={"path": P, "expressions": {"extension1": "x"}})             # denylisted parameter
        self.assertEqual((out["code"], out["rule_id"]), ("DENIED", "G3"))
        self.assertEqual(self.store.approvals_list(), [])                  # a denied call never becomes a request
        err, out = self.call(self.BD, "tdflow_delete_operator", {"path": "/project1/fx/big"})           # large delete: A2
        self.assertEqual((out["code"], out["rule_id"]), ("APPROVAL_REQUIRED", "A2"))
        self.assertIn("deletes 80 operators", out["reasons"][0])
        self.decide(out["approval_id"])
        self.assertFalse(self.call(self.BD, "tdflow_delete_operator", {"path": "/project1/fx/big"})[0])
        self.assertFalse(self.call(self.OP, args={"path": P, "values": {"opacity": 0.5}})[0])           # plain edits: no approval

    def test_without_an_approval_surface_nothing_changes(self):            # Phases 1-2A behaviour
        self.pol.approvals = False
        err, out = self.call(self.OP)
        self.assertEqual((out["code"], out["rule_id"]), ("APPROVAL_UNAVAILABLE", "A1"))
        self.assertEqual(self.store.approvals_list(), [])
        self.assertIn("unavailable", self.call(self.OP, "tdflow_whoami", {})[1]["approvals"])
        self.pol.approvals = True
        self.assertIn("available:", self.call(self.OP, "tdflow_whoami", {})[1]["approvals"])

    def test_review13_rows_are_private_and_digest_and_replay_are_checked(self):
        aid = self.call(self.OP)[1]["approval_id"]
        n = self.store._c().execute("SELECT COUNT(*) FROM sqlite_master WHERE name='approvals'").fetchone()[0]
        self.assertEqual(n, 0)                                             # not in the project-local database
        evil = APR.digest("tdflow_set_parameters", {"path": P, "expressions": {"opacity": "__import__('os').getcwd()"}})[1]
        self.store._cc().execute("UPDATE approvals SET args_sha256=? WHERE id=?", (evil, aid))   # even in private storage
        self.assertEqual(self.decide(aid)["code"], "APPROVAL_INVALID")    # what was shown is not what would run
        aid = self.call(self.OP, args={"path": P, "expressions": {"opacity": "2"}})[1]["approval_id"]
        self.decide(aid)
        self.assertFalse(self.call(self.OP, args={"path": P, "expressions": {"opacity": "2"}})[0])
        self.assertEqual(self.store.approval_get(aid)["args"], "")         # not kept after use

    def test_review13_target_facts_are_bound(self):
        err, out = self.call(self.BD, "tdflow_delete_operator", {"path": "/project1/fx/big"})
        self.decide(out["approval_id"])
        self.ad.count = 5000                                               # the component grew since
        err, out = self.call(self.BD, "tdflow_delete_operator", {"path": "/project1/fx/big"})
        self.assertEqual(out["code"], "APPROVAL_TARGETS_CHANGED")
        self.assertEqual(self.ad.executed, [])

    def test_review13_invisible_characters_cannot_be_approved(self):
        for text in ("1 #\u202e ;)(metsys.so", "a\u200bb", "x\u0007"):
            err, out = self.call(self.OP, args={"path": P, "expressions": {"opacity": text}})
            self.assertEqual(out["code"], "APPROVAL_REFUSED", text)
        self.assertEqual(self.store.approvals_list(), [])

    def test_code_dat_text_is_approvable_and_bound_to_the_text_it_replaces(self):    # D20
        self.ad.snapshot = (P, 1, "old", "sha-old", "sha-new")
        args = {"path": P, "text": "def onFrameStart(frame):\n\treturn"}
        r = self.eng.call(self.OP, "tdflow_set_dat_text", args)["structuredContent"]
        self.assertEqual((r["code"], r["rule_id"]), ("APPROVAL_REQUIRED", "A1"))
        self.assertIn("writes text TouchDesigner may run as code", r["reasons"][0])
        row = self.store.approval_get(r["approval_id"])
        self.assertIn(["replaces_text_sha256", "sha-old"], json.loads(row["targets"]))
        self.decide(r["approval_id"])
        self.ad.snapshot = (P, 1, "someone edited it", "sha-other", "sha-new")   # changed since the approval
        r2 = self.eng.call(self.OP, "tdflow_set_dat_text", args)["structuredContent"]
        self.assertEqual(r2["code"], "APPROVAL_TARGETS_CHANGED")
        self.assertEqual(self.ad.executed, [])
        self.ad.snapshot = (P, 1, "old", "sha-old", "sha-new")
        r = self.eng.call(self.OP, "tdflow_set_dat_text", args)["structuredContent"]
        self.decide(r["approval_id"])
        self.assertFalse(self.eng.call(self.OP, "tdflow_set_dat_text", args)["isError"])
        self.assertEqual(self.ad.executed[-1][0], "tdflow_set_dat_text")

    def test_wiring_into_a_dat_stays_unavailable(self):
        r = self.eng.call(self.OP, "tdflow_connect", {"src": "/project1/fx/a", "dst": P})["structuredContent"]
        self.assertIn(r["code"], ("APPROVAL_UNAVAILABLE", "DENIED"))
        self.assertEqual(self.store.approvals_list(), [])

    def test_undo_of_an_approved_change_needs_no_new_approval(self):
        aid = self.call(self.OP)[1]["approval_id"]
        self.decide(aid)
        self.assertFalse(self.call(self.OP)[0])
        err, out = self.call(self.OP, "tdflow_undo", {})
        self.assertFalse(err, out)                                         # restores the state before; no new code
        self.assertEqual(len(self.store.approvals_list(state="pending")), 0)

    def test_arguments_with_a_token_are_refused_before_being_stored(self):
        tok = "tdf_" + "A" * 12 + "." + "b" * 43
        err, out = self.call(self.OP, args={"path": P, "expressions": {"opacity": "'" + tok + "'"}})
        self.assertEqual(out["code"], "APPROVAL_REFUSED")
        self.assertEqual(self.store.approvals_list(), [])


if __name__ == "__main__":
    unittest.main()
