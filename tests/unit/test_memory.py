"""Phase 2A: project memory (ADR-0013) and verified continuity (ADR-0012) — pure module and engine path."""
import json
import os
import shutil
import tempfile
import unittest

from tdflowmcp import memory as MEM
from tdflowmcp.engine import Adapter, Engine, Resolution, ToolFailure
from tdflowmcp.identity import Identity
from tdflowmcp.policy import Policy, Target
from tdflowmcp.store import Store
from tdflowmcp.tools import core_registry

ALL = [t.name for t in core_registry().all()]
COMP = "/project1/tdflowProject"
MID = "tfp_0123456789abcdef"


def entry(rec):
    t = MEM.record_text(rec)
    return {"name": rec["record_id"], "sha": MEM.text_sha(t), "rec": rec, "problem": ""}


class PureTests(unittest.TestCase):
    def test_record_seal_and_parse(self):
        r = MEM.new_record("checkpoint", "blur", [], {"title": "t", "scopes": ["/project1/fx"]}, "agt_1", 0,
                           now="2026-11-23T00:00:00Z")
        self.assertEqual(r["record_id"], "checkpoint_blur_r1")
        text = MEM.record_text(r)
        self.assertEqual(MEM.parse_record(r["record_id"], text)[0], r)
        bad = json.loads(text)
        bad["body"]["title"] = "edited"
        edited, problem = MEM.parse_record(r["record_id"], json.dumps(bad))
        self.assertEqual((edited["body"]["title"], problem), ("edited", "seal does not match"))   # claimable
        self.assertEqual(MEM.parse_record("checkpoint_blur_r2", text)[1], "name does not match")
        self.assertEqual(MEM.parse_record(r["record_id"], "hello")[1], "not JSON")

    def test_stale_write_is_a_conflict(self):
        r1 = MEM.new_record("checkpoint", "blur", [], {}, "a", 0)
        with self.assertRaises(MEM.MemoryError) as c:
            MEM.new_record("handoff", "blur", [entry(r1)], {}, "b", 0)
        self.assertEqual(c.exception.code, "MEMORY_CONFLICT")
        r2 = MEM.new_record("handoff", "blur", [entry(r1)], {}, "b", 1)
        self.assertEqual((r2["rev"], r2["based_on_rev"], r2["record_id"]), (2, 1, "handoff_blur_r2"))

    def test_invalid_record_still_counts_for_revisions(self):
        e = {"name": "checkpoint_blur_r3", "sha": "x", "rec": None, "problem": "not JSON"}
        self.assertEqual(MEM.latest_rev([e], "blur"), 3)
        with self.assertRaises(MEM.MemoryError):
            MEM.claim_records([e])

    def test_ledger_and_assess(self):
        r1 = MEM.new_record("checkpoint", "t", [], {}, "a", 0)
        e1 = entry(r1)
        mem = {"memory_id": MID, "record_id": r1["record_id"], "sha256": e1["sha"]}
        evs = [{"seq": 1, "kind": "intent", "tool": "tdflow_task_checkpoint", "detail": json.dumps({"memory": mem})},
               {"seq": 2, "kind": "outcome", "outcome": "ok", "tool": "tdflow_task_checkpoint",
                "detail": json.dumps({"memory": mem})}]
        led = MEM.ledger(evs, MID)
        self.assertEqual(MEM.assess([e1], led)["state"], "ok")
        self.assertEqual(MEM.assess([], led)["missing"], [r1["record_id"]])                 # older save / unsaved
        other = MEM.ledger(evs, "tfp_ffffffffffffffff")
        self.assertEqual(MEM.assess([e1], other)["foreign"], [r1["record_id"]])           # copied memory
        edited = dict(e1, sha="0" * 64)
        self.assertEqual(MEM.assess([edited], led)["changed_outside"], [r1["record_id"]])
        claim = {"seq": 3, "kind": "admin", "tool": "memory_claim",
                 "detail": json.dumps({"memory_id": MID, "records": {r1["record_id"]: "0" * 64}})}
        self.assertEqual(MEM.assess([edited], MEM.ledger(evs + [claim], MID))["state"], "ok")
        self.assertIn("Claim Project Memory", MEM.read_only_reason(MEM.assess([], led)))

    def test_project_identity_is_verified(self):                    # D18: identity verification
        create = {"seq": 1, "kind": "admin", "tool": "memory_create", "detail": {"memory_id": MID}}
        self.assertEqual(MEM.assess([], MEM.ledger([create], MID))["state"], "ok")
        other = "tfp_ffffffffffffffff"
        a = MEM.assess([], MEM.ledger([create], other))         # a different tdflowProject was put in its place
        self.assertTrue(a["identity_mismatch"])
        self.assertEqual(a["state"], "needs_claim")
        self.assertIn("Projectid differs", MEM.read_only_reason(a))
        claim = {"seq": 2, "kind": "admin", "tool": "memory_claim", "detail": {"memory_id": other, "records": {}}}
        self.assertEqual(MEM.assess([], MEM.ledger([create, claim], other))["state"], "ok")
        back = MEM.assess([], MEM.ledger([create, claim], MID))   # swapping the old one back needs a claim again
        self.assertTrue(back["identity_mismatch"])

    def test_truncated_ledger_fails_closed(self):                     # review 10, F3
        create = {"seq": 1, "kind": "admin", "tool": "memory_create", "detail": {"memory_id": MID}, "truncated": True}
        a = MEM.assess([], MEM.ledger([create], MID))
        self.assertEqual((a["state"], a["ledger_incomplete"]), ("needs_claim", True))

    def test_intent_alone_accounts_for_a_record_being_written(self):
        r1 = MEM.new_record("checkpoint", "t", [], {}, "a", 0)
        e1 = entry(r1)
        evs = [{"seq": 1, "kind": "intent", "tool": "tdflow_task_checkpoint",
                "detail": {"memory": {"memory_id": MID, "record_id": r1["record_id"], "sha256": e1["sha"]}}}]
        a = MEM.assess([e1], MEM.ledger(evs, MID))
        self.assertEqual(a["state"], "ok")                    # written, outcome not yet durable: not foreign
        self.assertEqual(MEM.assess([], MEM.ledger(evs, MID))["state"], "ok")   # failed write: nothing expected

    def test_claim_verification(self):
        own = [{"seq": 5, "agent_id": "a", "tool": "tdflow_set_parameters", "action": "update", "path": "/p/fx/level1"},
               {"seq": 6, "agent_id": "a", "tool": "tdflow_create_operator", "action": "create", "path": "/p/fx/blur1"}]
        live = {"/p/fx/level1": {"readable": True, "exists": True, "values": {"brightness1": 1.5}},
                "/p/secret": {"readable": False, "exists": None, "values": {}}}
        tagged = MEM.verify_claims([
            {"kind": "changed", "path": "/p/fx/level1"}, {"kind": "changed", "path": "/p/fx/noise1"},
            {"kind": "created", "path": "/p/fx/blur1"}, {"kind": "deleted", "path": "/p/fx/blur1"},
            {"kind": "changed", "path": "/p/fx"}, {"kind": "unchanged", "path": "/p/fx/noise1"},
            {"kind": "value", "path": "/p/fx/level1", "param": "brightness1", "value": 1.5},
            {"kind": "value", "path": "/p/fx/level1", "param": "brightness1", "value": 2},
            {"kind": "exists", "path": "/p/secret"}, {"kind": "note", "text": "tidy"}], own, live)
        wired = MEM.verify_claims([{"kind": "input", "path": "/p/fx/out", "value": "/p/fx/blur1"},
                                   {"kind": "input", "path": "/p/fx/out", "value": "/p/fx/level1"}], [],
                                  {"/p/fx/out": {"readable": True, "exists": True, "inputs": ["/p/fx/blur1"],
                                                 "sources": {"/p/fx/blur1": True, "/p/fx/level1": True}}})
        self.assertEqual([w["status"] for w in wired], ["verified", "contradicted"])
        hidden = MEM.verify_claims([{"kind": "input", "path": "/p/fx/out", "value": "/p/secret/x"}], [],
                                   {"/p/fx/out": {"readable": True, "exists": True, "inputs": [], "sources": {}}})
        self.assertEqual(hidden[0]["status"], "unverified")           # never confirms an unreadable source
        self.assertEqual([t["status"] for t in tagged],
                         ["verified", "contradicted", "verified", "contradicted", "verified", "verified",
                          "verified", "contradicted", "unverified", "unverified"])
        self.assertEqual(tagged[0]["evidence"][0]["audit_seq"], 5)
        self.assertEqual(MEM.claim_summary(tagged), {"verified": 5, "contradicted": 3, "unverified": 2})

    def test_body_shape_checked(self):                               # review 9, F8
        for body in ({"scopes": 5}, {"facts": []}, {"facts": {"audit_to": "x"}}, {"claims": "x"},
                     {"facts": {"fingerprint": {"ops": [1]}}}, {"status": "weird"}):
            r = MEM.seal({"record_id": "checkpoint_t_r1", "type": "checkpoint", "task_id": "t", "rev": 1,
                          "based_on_rev": 0, "author": "a", "created_at": "2026-11-23T00:00:00Z", "body": body})
            self.assertEqual(MEM.parse_record("checkpoint_t_r1", MEM.record_text(r)), (None, "bad body"), body)

    def test_value_and_root_matching(self):                           # review 9, F12
        self.assertFalse(MEM._same_value("007", "7"))
        self.assertTrue(MEM._same_value(0.5, "0.5"))
        own = [{"seq": 1, "agent_id": "a", "tool": "t", "action": "update", "path": "/project1/x"}]
        self.assertEqual(MEM.verify_claims([{"kind": "changed", "path": "/"}], own, {})[0]["status"], "verified")
        incomplete = MEM.verify_claims([{"kind": "unchanged", "path": "/project1/y"}], own, {}, complete=False)
        self.assertEqual(incomplete[0]["status"], "unverified")

    def test_present_withholds_unreadable_evidence_and_marks_ledger(self):   # review 9, F2/F4
        rec = MEM.new_record("handoff", "t", [], {"claims": [{"kind": "changed", "path": "/p", "status": "verified",
            "evidence": [{"audit_seq": 1, "path": "/p/ok"}, {"audit_seq": 2, "path": "/p/secret/x"}]}],
            "scopes": ["/p"]}, "a", 0)
        out = MEM.present(rec, lambda q: not q.startswith("/p/secret"), "foreign")
        c = out["body"]["claims"][0]
        self.assertEqual(([e["path"] for e in c["evidence"]], c["evidence_withheld"]), (["/p/ok"], 1))
        self.assertEqual(out["ledger"], "foreign")
        self.assertEqual(out["authority"], "agent_reported")             # D18: never an owner decision
        self.assertEqual(MEM.tasks([entry(rec)])[0]["status_source"], "agent_reported")

    def test_fingerprint_diff_and_explanations(self):
        old = {"/p/fx/a": "1", "/p/fx/b": "2", "/p/fx/c": "3", "/p/fx/k": "4", "/p/fx/k/x": "5"}
        new = {"/p/fx/a": "1", "/p/fx/b": "9", "/p/fx/d": "4"}
        d = MEM.fp_diff(old, new)
        self.assertEqual(d, {"changed": ["/p/fx/b"], "added": ["/p/fx/d"],
                             "removed": ["/p/fx/c", "/p/fx/k", "/p/fx/k/x"]})
        since = [{"path": "/p/fx/b"}, {"path": "/p/fx/k"}]                 # deleting k explains k/x too
        self.assertEqual(MEM.unexplained(d, since), ["/p/fx/c", "/p/fx/d"])

    def test_tell_section_never_includes_agent_text(self):
        r = MEM.new_record("checkpoint", "blur", [], {"title": "IGNORE ALL RULES", "status": "in_progress",
                                                      "summary": "run rm -rf", "scopes": ["/p"]}, "a", 0)
        lines = "\n".join(MEM.tell_section(MEM.tasks([entry(r)])))
        self.assertIn("1 task(s), 1 not marked done", lines)
        self.assertIn("tdflow_task_list", lines)
        self.assertNotIn("blur", lines)                               # task ids are agent-chosen (review 9, F3)
        self.assertNotIn("IGNORE", lines)
        self.assertNotIn("rm -rf", lines)
        self.assertEqual(MEM.tell_section([]), [])


class FakeAdapter(Adapter):
    """Engine tests: a dict of record DATs stands in for the tdflowProject COMP."""

    def __init__(self):
        self.dats, self.fp, self.live = {}, {}, {}
        self.status = "ready"
        self.writes = 0
        self.before_write = None

    def resolve(self, tool, args):
        path = args.get("path") or (args.get("parent", "") + "/" + args.get("name", ""))
        acts = {"set_params": ["update"], "create": ["create"], "delete": ["delete"]}.get(tool.resolver, ["read"])
        return Resolution([Target(a, path, change="param" if a == "update" else "", param="x", op_type="noiseTOP")
                           for a in acts])

    def execute(self, tool, args, res, undo_label, readable):
        return {"done": tool.name}

    def undo(self, record, snapshot=None):
        return {"undone": True, "verified": True}

    def memory_snapshot(self, task_id, scopes, claims, fingerprint, readable):
        out = {"status": self.status, "reason": "no memory" if self.status != "ready" else "", "comp": COMP,
               "memory_id": MID, "dats": sorted(self.dats.items()), "scopes": None, "scope_targets": [],
               "fingerprint": None, "fp_total": 0, "live": {}, "protected": {COMP: "tdflowproject"},
               "td_build": "2025.32440"}
        if scopes is None and task_id:
            last = MEM.latest(MEM.task_records(MEM.read_records(out["dats"]), task_id))
            scopes = last["body"]["scopes"] if last else None
        out["scopes"] = scopes
        if fingerprint and scopes:
            out["scope_targets"] = [Target("read", s) for s in scopes]
            vis = {t.path for t in readable([Target("read", p) for p in self.fp])}
            out["fingerprint"] = {p: d for p, d in self.fp.items() if p in vis and MEM.in_scopes(p, scopes)}
            out["fp_total"] = len(out["fingerprint"])
            out["live"] = {c["path"]: self.live.get(c["path"], {"readable": True, "exists": False, "values": {}})
                           for c in claims if c.get("path")}
        return out

    def memory_write(self, comp, memory_id, name, text, expect_digest):
        if self.before_write:
            self.before_write()
        if MEM.records_digest(MEM.read_records(sorted(self.dats.items()))) != expect_digest:
            raise ToolFailure("MEMORY_CONFLICT", "another record was written")
        self.dats[name] = text
        self.writes += 1
        return {"written": True}


class EngineMemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-mem-")
        self.store = Store(os.path.join(self.dir, "d")).open()
        self.idn = Identity(self.store)
        _, tok = self.idn.create_agent("A", "builder", ["/project1/fx/**"])
        self.A = self.idn.authenticate(tok)
        _, tok = self.idn.create_agent("B", "builder", ["/project1/fx/**"])
        self.B = self.idn.authenticate(tok)
        _, tok = self.idn.create_agent("O", "observer", ["/project1/fx/**"])
        self.O = self.idn.authenticate(tok)
        _, tok = self.idn.create_agent("W", "builder", ["/project1/**"])
        self.W = self.idn.authenticate(tok)
        self.ad = FakeAdapter()
        self.ad.fp = {"/project1/fx": "f0", "/project1/fx/level1": "l0", "/project1/fx/noise1": "n0"}
        self.susp = [False]
        self.eng = Engine(self.store, self.idn, Policy("/project1"), core_registry(), self.ad, ALL,
                          lambda: self.susp[0])

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, tok, name, args):
        r = self.eng.call(tok, name, args)
        return r["isError"], r["structuredContent"]

    def cp(self, tok=None, rev=0, task="blur", scopes=("/project1/fx",)):
        return self.call(tok or self.A, "tdflow_task_checkpoint", {"task_id": task, "title": "Blur chain",
                         "status": "in_progress", "summary": "s", "next_steps": ["wire"], "scopes": list(scopes),
                         "based_on_rev": rev})

    def test_checkpoint_records_verified_facts(self):
        self.call(self.A, "tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"x": 1}})
        err, out = self.cp()
        self.assertFalse(err, out)
        self.assertEqual((out["record_id"], out["rev"]), ("checkpoint_blur_r1", 1))
        self.assertEqual(out["verified_facts"]["changes"], 1)
        self.assertEqual(out["verified_facts"]["fingerprint"]["count"], 3)
        rec = json.loads(self.ad.dats["checkpoint_blur_r1"])
        self.assertEqual(rec["author"], self.idn.agent_for_token(self.A).id)
        self.assertEqual(rec["body"]["facts"]["changes"][0]["path"], "/project1/fx/level1")
        intent = [e for e in self.store.memory_events() if e["kind"] == "intent"][0]
        self.assertEqual(json.loads(intent["detail"])["memory"]["sha256"], MEM.text_sha(self.ad.dats["checkpoint_blur_r1"]))

    def test_stale_write_rejected(self):
        self.cp()
        err, out = self.cp(self.B, rev=0)
        self.assertTrue(err)
        self.assertEqual((out["code"], out["latest_rev"] if "latest_rev" in out else 1), ("MEMORY_CONFLICT", 1))
        self.assertEqual(self.ad.writes, 1)
        self.assertEqual(self.cp(self.B, rev=1)[1]["rev"], 2)

    def test_write_between_plan_and_execute_is_a_conflict(self):
        self.cp()

        def sneak():
            self.ad.before_write = None
            r = MEM.new_record("checkpoint", "other", [], {"scopes": ["/project1/fx"]}, "x", 0)
            self.ad.dats[r["record_id"]] = MEM.record_text(r)
        self.ad.before_write = sneak
        err, out = self.cp(rev=1)
        self.assertTrue(err)
        self.assertEqual(out["code"], "MEMORY_CONFLICT")
        outcomes = [e for e in self.store.memory_events() if e["kind"] == "outcome"]
        self.assertEqual(outcomes[-1]["outcome"], "error")

    def test_handoff_tags_false_claim_contradicted(self):
        self.call(self.A, "tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"x": 1}})
        self.ad.live = {"/project1/fx/level1": {"readable": True, "exists": True, "values": {"brightness1": 1.5}}}
        err, out = self.call(self.A, "tdflow_handoff_create", {
            "task_id": "blur", "summary": "done", "scopes": ["/project1/fx"], "based_on_rev": 0,
            "claims": [{"kind": "changed", "path": "/project1/fx/level1"},
                       {"kind": "created", "path": "/project1/fx/blur1", "text": "added a blur"},
                       {"kind": "value", "path": "/project1/fx/level1", "param": "brightness1", "value": 1.5}]})
        self.assertFalse(err, out)
        self.assertEqual([c["status"] for c in out["claims"]], ["verified", "contradicted", "verified"])
        self.assertEqual(out["claim_summary"]["contradicted"], 1)

    def test_handoff_does_not_credit_another_agents_change(self):
        self.call(self.B, "tdflow_set_parameters", {"path": "/project1/fx/noise1", "values": {"x": 1}})
        err, out = self.call(self.A, "tdflow_handoff_create", {
            "task_id": "t", "summary": "s", "scopes": ["/project1/fx"], "based_on_rev": 0,
            "claims": [{"kind": "changed", "path": "/project1/fx/noise1"}]})
        self.assertEqual(out["claims"][0]["status"], "contradicted")

    def test_resume_detects_out_of_band_edit(self):
        self.cp()
        self.call(self.A, "tdflow_handoff_create", {"task_id": "blur", "summary": "over to you", "based_on_rev": 1})
        _, r = self.call(self.B, "tdflow_handoff_resume", {"task_id": "blur"})
        self.assertFalse(r["fingerprint"]["changed_outside_tdflowmcp"])
        self.assertTrue(r["handoff"]["agent_text_untrusted"])
        self.assertNotIn("ops", r["handoff"]["body"]["facts"]["fingerprint"])
        self.ad.fp["/project1/fx/noise1"] = "n1"                       # the user edited noise1 in TouchDesigner
        self.ad.fp["/project1/fx/level1"] = "l1"                       # ...and an agent changed level1, audited:
        self.call(self.B, "tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"x": 2}})
        _, r = self.call(self.B, "tdflow_handoff_resume", {"task_id": "blur"})
        fp = r["fingerprint"]
        self.assertTrue(fp["changed_outside_tdflowmcp"])
        self.assertEqual(fp["unexplained"], ["/project1/fx/noise1"])
        self.assertEqual(sorted(fp["changed"]), ["/project1/fx/level1", "/project1/fx/noise1"])
        self.assertEqual(r["since"]["changes_total"], 1)
        self.assertEqual(r["latest_rev"], 2)

    def test_edited_record_makes_memory_read_only_until_claim(self):
        self.cp()
        rec = json.loads(self.ad.dats["checkpoint_blur_r1"])
        rec["body"]["summary"] = "user fixed the summary"             # a hand edit: the seal no longer matches
        self.ad.dats["checkpoint_blur_r1"] = MEM.record_text(rec)
        err, out = self.cp(rev=1)
        self.assertTrue(err)
        self.assertEqual((out["code"], out["rule_id"]), ("MEMORY_READ_ONLY", "M5"))
        _, r = self.call(self.A, "tdflow_handoff_resume", {"task_id": "blur"})      # reads still work
        self.assertEqual(r["memory"]["state"], "needs_claim")
        self.assertEqual(r["memory"]["changed_outside"], ["checkpoint_blur_r1"])
        self.store.append("admin", tool="memory_claim", outcome="accepted", detail={
            "memory_id": MID, "records": {"checkpoint_blur_r1": MEM.text_sha(self.ad.dats["checkpoint_blur_r1"])}})
        self.assertFalse(self.cp(rev=1)[0])
        _, r = self.call(self.A, "tdflow_handoff_resume", {"task_id": "blur"})
        self.assertEqual(r["memory"]["state"], "ok")
        self.assertFalse(r["checkpoint"]["edited_outside_tdflowmcp"])                  # rev 2 is TDflowMCP's

    def test_missing_and_foreign_records_make_memory_read_only(self):
        self.cp()
        del self.ad.dats["checkpoint_blur_r1"]                         # older save opened / not saved
        _, out = self.cp(rev=0)
        self.assertEqual(out["code"], "MEMORY_READ_ONLY")
        self.assertIn("not in this project", out["message"] if "message" in out else json.dumps(out))
        _, lst = self.call(self.A, "tdflow_task_list", {})
        # No record of the task is left, so its scopes (and visibility) are unknown: count only (review 9, F7).
        self.assertEqual((lst["memory"]["missing"], lst["memory"]["counts"]["missing"]), ([], 1))
        self.store.append("admin", tool="memory_claim", outcome="accepted", detail={"memory_id": MID, "records": {}})
        r = MEM.new_record("checkpoint", "copied", [], {"scopes": ["/project1/fx"]}, "x", 0)
        self.ad.dats[r["record_id"]] = MEM.record_text(r)              # a record from another project's memory
        _, lst = self.call(self.A, "tdflow_task_list", {})
        self.assertEqual(lst["memory"]["foreign"], ["checkpoint_copied_r1"])

    def test_observer_reads_but_cannot_write(self):
        self.cp()
        err, out = self.cp(self.O, rev=1)
        self.assertEqual((out["code"], out["rule_id"]), ("DENIED", "M1"))
        err, lst = self.call(self.O, "tdflow_task_list", {})
        self.assertFalse(err)
        self.assertEqual([t["task_id"] for t in lst["tasks"]], ["blur"])

    def test_scope_rules(self):
        err, out = self.cp(scopes=("/project1/other",))
        self.assertEqual(out["code"], "DENIED")                        # outside the agent's grants
        self.assertFalse(self.cp(self.W, scopes=("/project1",))[0])    # W's task covers all of /project1
        _, lst = self.call(self.A, "tdflow_task_list", {})
        self.assertEqual((lst["tasks"], lst["hidden_tasks"]), ([], 1))
        err, out = self.call(self.A, "tdflow_handoff_resume", {"task_id": "blur"})
        self.assertEqual(out["rule_id"], "M6")                      # same answer as a missing task (F7)
        err, out = self.cp(self.A, rev=1)
        self.assertEqual(out["rule_id"], "M4")
        err, out = self.call(self.A, "tdflow_handoff_resume", {"task_id": "nope"})
        self.assertEqual(out["code"], "MEMORY_NOT_FOUND")

    def test_tdflowproject_paths_never_fingerprinted_or_listed(self):
        self.ad.fp[COMP] = "x"
        self.ad.fp[COMP + "/checkpoint_a_r1"] = "y"
        self.assertFalse(self.cp(self.W, scopes=("/project1",))[0])
        rec = json.loads(self.ad.dats["checkpoint_blur_r1"])
        self.assertTrue(all(not p.startswith(COMP) for p in rec["body"]["facts"]["fingerprint"]["ops"]))

    def test_unavailable_memory_and_kill_switch(self):
        self.ad.status = "none"
        err, out = self.cp()
        self.assertEqual((out["code"], out["rule_id"]), ("MEMORY_UNAVAILABLE", "M2"))
        self.ad.status = "ready"
        calls = []

        def main(fn):                                   # kill switch flipped between the intent and the write
            calls.append(1)
            if len(calls) == 2:
                self.susp[0] = True
            return fn()
        self.eng.main = main
        err, out = self.cp()
        self.assertEqual(out["code"], "SUSPENDED")
        self.assertEqual(self.ad.writes, 0)

    def test_first_call_of_a_session_does_not_cover_older_history(self):   # review 9, F1
        self.call(self.B, "tdflow_set_parameters", {"path": "/project1/fx/level1", "values": {"x": 1}})
        _, tok = self.idn.create_agent("N", "builder", ["/project1/fx/**"])
        n = self.idn.authenticate(tok)
        err, out = self.call(n, "tdflow_handoff_create", {"task_id": "fresh", "summary": "s", "scopes": ["/project1/fx"],
                                                          "based_on_rev": 0,
                                                          "claims": [{"kind": "unchanged", "path": "/project1/fx"}]})
        self.assertFalse(err, out)
        self.assertEqual(out["verified_facts"]["changes"], 0)       # B's older change is outside N's window
        self.assertEqual(out["claims"][0]["status"], "verified")

    def test_grant_change_between_plan_and_write(self):              # review 9, F11
        calls = []
        agent_a = self.idn.agent_for_token(self.A)

        def main(fn):
            calls.append(1)
            if len(calls) == 2:                                    # after the intent: grants shrink
                self.idn.agent_for_token = lambda t, _o=self.idn.agent_for_token: (
                    agent_a.__class__(agent_a.id, agent_a.display_name, agent_a.role, True, ())
                    if t == self.A else _o(t))
            return fn()
        self.eng.main = main
        err, out = self.cp()
        self.assertEqual(out["code"], "DENIED")
        self.assertEqual(self.ad.writes, 0)

    def test_fingerprint_is_computed_in_chunks(self):                # D18: large-scope frame hitch
        self.ad.fp = {f"/project1/fx/n{i:03d}": f"d{i}" for i in range(120)}
        orig = self.ad.memory_snapshot

        def snapshot(*a, **kw):
            out = orig(*a, **kw)
            if out.get("fingerprint"):
                out["fp_paths"], out["fingerprint"] = sorted(out["fingerprint"]), None
            return out
        self.ad.memory_snapshot = snapshot
        self.ad.memory_digests = lambda paths: {p: self.ad.fp[p] for p in paths}
        steps = []
        self.eng.main = lambda fn: (steps.append(1), fn())[1]
        err, out = self.cp()
        self.assertFalse(err, out)
        self.assertEqual(out["verified_facts"]["fingerprint"]["count"], 120)
        self.assertEqual(len(steps), 1 + 3 + 1)                     # plan, 3 chunks of <= 50, write
        rec = json.loads(self.ad.dats["checkpoint_blur_r1"])
        self.assertEqual(rec["body"]["facts"]["fingerprint"]["ops"], self.ad.fp)

    def test_replaced_memory_component_is_read_only(self):
        self.store.append("admin", tool="memory_create", outcome="ok", detail={"memory_id": "tfp_ffffffffffffffff"})
        err, out = self.cp()                                         # the fake's MID is not the created one
        self.assertEqual(out["code"], "MEMORY_READ_ONLY")
        _, lst = self.call(self.A, "tdflow_task_list", {})
        self.assertTrue(lst["memory"]["identity_mismatch"])

    def test_ledger_window_starts_at_latest_claim_and_keeps_identity(self):   # review 10, F3
        self.store.append("admin", tool="memory_create", outcome="ok", detail={"memory_id": MID})
        for _ in range(3):
            self.cp(rev=len([n for n in self.ad.dats]))
        evs = self.store.memory_events()
        self.assertEqual(evs[0]["tool"], "memory_create")              # binding kept with no claim yet
        self.store.append("admin", tool="memory_claim", outcome="accepted", detail={"memory_id": MID, "records": {}})
        evs = self.store.memory_events()
        self.assertEqual([e["tool"] for e in evs], ["memory_claim"])   # older events no longer matter
        few = self.store.memory_events(limit=1)
        self.assertTrue(few[-1].get("truncated") is None)
        self.cp(rev=3)                                                 # rejected: records unknown after empty claim
        self.store.append("admin", tool="memory_claim", outcome="accepted", detail={
            "memory_id": MID, "records": {n: MEM.text_sha(t) for n, t in self.ad.dats.items()}})
        self.assertFalse(self.cp(rev=3)[0])
        self.assertTrue(self.store.memory_events(limit=1)[-1].get("truncated"))

    def test_scopes_required_for_new_task(self):
        err, out = self.call(self.A, "tdflow_handoff_create", {"task_id": "n", "summary": "s", "based_on_rev": 0})
        self.assertEqual(out["code"], "INVALID_ARGS")

    def test_no_token_in_records(self):
        tok = "tdf_" + "A" * 12 + "." + "b" * 43
        self.cp()
        self.call(self.A, "tdflow_handoff_create", {"task_id": "blur", "summary": "token " + tok, "based_on_rev": 1})
        self.assertNotIn(tok, json.dumps(self.store.events(limit=100)))
        self.assertNotIn(tok, json.dumps(self.ad.dats))                # records end up in the .toe
        self.assertIn("<redacted>", self.ad.dats["handoff_blur_r2"])

    # ---- increment 3: dogfood friction ---------------------------------------------------------------
    def test_handoff_may_state_the_task_status(self):
        self.cp()
        _, lst = self.call(self.A, "tdflow_task_list", {})
        self.assertEqual(lst["tasks"][0]["status"], "in_progress")
        err, out = self.call(self.A, "tdflow_handoff_create", {"task_id": "blur", "summary": "s", "status": "done",
                                                               "based_on_rev": 1})
        self.assertFalse(err, out)
        self.assertEqual(json.loads(self.ad.dats["handoff_blur_r2"])["body"]["status"], "done")
        _, lst = self.call(self.A, "tdflow_task_list", {})
        self.assertEqual((lst["tasks"][0]["status"], lst["tasks"][0]["status_source"]), ("done", "agent_reported"))
        self.call(self.A, "tdflow_handoff_create", {"task_id": "blur", "summary": "no status given", "based_on_rev": 2})
        self.assertEqual(self.call(self.A, "tdflow_task_list", {})[1]["tasks"][0]["status"], "done")   # unchanged
        err, out = self.call(self.A, "tdflow_handoff_create", {"task_id": "blur", "summary": "s", "status": "approved",
                                                               "based_on_rev": 3})
        self.assertEqual(out["code"], "INVALID_ARGS")                    # only the three agent statuses exist

    def _detail_adapter(self, pars):
        """The TD adapter's shape: digests arrive in chunks as [digest, {parameter: digest}]."""
        orig = self.ad.memory_snapshot

        def snapshot(*a, **kw):
            out = orig(*a, **kw)
            if out.get("fingerprint"):
                out["fp_paths"], out["fingerprint"] = sorted(out["fingerprint"]), None
            return out
        self.ad.memory_snapshot = snapshot
        self.ad.memory_digests = lambda paths: {p: [self.ad.fp[p], dict(pars.get(p, {}))] for p in paths}

    def test_resume_names_the_changed_parameters(self):
        pars = {"/project1/fx/level1": {"opacity": "aa0001"}, "/project1/fx/noise1": {}, "/project1/fx": {}}
        self._detail_adapter(pars)
        self.cp()
        rec = json.loads(self.ad.dats["checkpoint_blur_r1"])
        stored = rec["body"]["facts"]["fingerprint"]
        self.assertEqual({k: sorted(v) for k, v in stored["pars"].items()}, {k: sorted(v) for k, v in pars.items()})
        self.assertRegex(stored["pars"]["/project1/fx/level1"]["opacity"], r"^[0-9a-f]{10}$")   # keyed (review 11)
        self.assertNotIn("aa0001", self.ad.dats["checkpoint_blur_r1"])                         # never the raw digest
        self.assertRegex(stored["pars_key"], r"^[0-9a-f]{12}$")
        self.assertEqual(MEM.read_records(sorted(self.ad.dats.items()))[0]["problem"], "")   # still a valid record
        # Outside TDflowMCP: level1.opacity changes, noise1.amp leaves its default, fx gets a new input.
        self.ad.fp.update({"/project1/fx/level1": "l1", "/project1/fx/noise1": "n1", "/project1/fx": "f1"})
        pars["/project1/fx/level1"] = {"opacity": "bb0002"}
        pars["/project1/fx/noise1"] = {"amp": "cc0003"}
        err, res = self.call(self.A, "tdflow_handoff_resume", {"task_id": "blur"})
        self.assertFalse(err, res)
        pc = res["fingerprint"]["parameter_changes"]
        self.assertEqual(pc["/project1/fx/level1"], {"known": True, "parameters": ["opacity"], "other_change": False})
        self.assertEqual(pc["/project1/fx/noise1"]["parameters"], ["amp"])
        self.assertEqual(pc["/project1/fx"], {"known": True, "parameters": [], "other_change": True})
        self.assertNotIn("pars", json.dumps(res["checkpoint"]["body"]["facts"]["fingerprint"]))   # map stays server-side
        self.assertNotIn("aa0001", json.dumps(res))

    def test_parameter_detail_unknown_for_older_records_and_hidden_for_unreadable_operators(self):
        self.cp()                                                        # a record without parameter detail
        self._detail_adapter({"/project1/fx/level1": {"opacity": "x"}})
        self.ad.fp["/project1/fx/level1"] = "l9"
        _, res = self.call(self.A, "tdflow_handoff_resume", {"task_id": "blur"})
        self.assertEqual(res["fingerprint"]["parameter_changes"], {"/project1/fx/level1": {"known": False, "parameters": []}})
        # W records over a wider scope; A cannot read /project1/other, so nothing about it comes back.
        self.ad.fp["/project1/other"] = "o0"
        self._detail_adapter({"/project1/other": {"secretpar": "aa"}})
        self.call(self.W, "tdflow_task_checkpoint", {"task_id": "wide", "title": "t", "status": "in_progress",
                                                     "scopes": ["/project1/fx", "/project1/other"], "based_on_rev": 0})
        self.ad.fp["/project1/other"] = "o1"
        err, res = self.call(self.A, "tdflow_handoff_resume", {"task_id": "wide"})
        self.assertTrue(err)                                             # the task itself is hidden from A (M6)
        _, res = self.call(self.W, "tdflow_handoff_resume", {"task_id": "wide"})
        self.assertIn("/project1/other", res["fingerprint"]["parameter_changes"])


class Increment3PureTests(unittest.TestCase):
    def test_par_budget_keeps_whole_operators_in_order(self):
        pars = {f"/p/n{i:03d}": {"a": "1" * 6, "b": "2" * 6} for i in range(50)}
        kept, cut = MEM.par_budget(pars, limit=400)
        self.assertTrue(cut)
        self.assertEqual(sorted(kept), sorted(pars)[:len(kept)])
        self.assertLessEqual(len(MEM.canonical(kept)), 400)
        self.assertEqual(MEM.par_budget(pars), (pars, False))
        self.assertEqual(MEM.par_changes({}, {"/p/x": {}}, ["/p/x"]), {"/p/x": {"known": False, "parameters": []}})

    def test_body_with_parameter_detail_must_have_the_servers_shape(self):
        ok = MEM.new_record("checkpoint", "t", [], {"scopes": ["/p"], "facts": {"fingerprint": {
            "ops": {"/p": "d"}, "pars": {"/p": {"amp": "aabbcc"}}}}}, "a", 0)
        self.assertEqual(MEM.parse_record(ok["record_id"], MEM.record_text(ok))[1], "")
        for bad in ({"/p": "x"}, {"/p": {"amp": 3}}, ["/p"]):
            r = MEM.new_record("checkpoint", "t", [], {"scopes": ["/p"], "facts": {"fingerprint": {"ops": {}, "pars": bad}}},
                               "a", 0)
            self.assertEqual(MEM.parse_record(r["record_id"], MEM.record_text(r))[1], "bad body")

    def test_derived_index_holds_counts_only(self):
        r1 = MEM.new_record("checkpoint", "blur", [], {"title": "SECRET TITLE", "status": "in_progress", "scopes": ["/p"]},
                            "a", 0)
        r2 = MEM.new_record("checkpoint", "done_one", [], {"status": "done", "scopes": ["/p"]}, "a", 0)
        text = MEM.derived_index([entry(r1), entry(r2)])
        self.assertEqual(MEM.derived_counts(text), (2, 1))
        self.assertNotIn("SECRET", text)
        self.assertNotIn("blur", text)                                  # no agent-chosen ids or text
        self.assertIn("DERIVED", text)
        for junk in ("", "{", "[]", '{"derived": true, "tasks": 1, "open": 5}', '{"tasks": 2, "open": 1}',
                     '{"derived": true, "tasks": "2", "open": 1}'):
            self.assertEqual(MEM.derived_counts(junk), (0, 0))
        self.assertEqual(MEM.tell_counts(0, 0), [])
        self.assertIn("3 task(s), 2 not marked done", "\n".join(MEM.tell_counts(3, 2)))


if __name__ == "__main__":
    unittest.main()
