"""Phase 2B: governance dashboard logic (audit search, live tail, reports that verify against the checkpoint)."""
import json
import os
import shutil
import tempfile
import unittest

from tdflowmcp import dashboard as D
from tdflowmcp.store import Store


class FilterTests(unittest.TestCase):
    def test_only_validated_fields_become_parameters(self):
        where, params, limit = D.event_filter(D.clean_query({
            "after": 10, "before": "99", "agent": "agt_abc", "session": "ses_x", "tool": "tdflow_undo", "kind": "deny",
            "outcome": "error", "q": "fx/level1", "limit": 7, "unknown": "ignored"}))
        self.assertEqual(limit, 7)
        self.assertEqual(params[:6], (10, 99, "agt_abc", "ses_x", "tdflow_undo", "deny"))
        self.assertNotIn("agt_abc", where)                              # values never enter the SQL text
        self.assertNotIn("ignored", where + repr(params))
        self.assertEqual(where.count("?"), len(params))

    def test_injection_and_junk_are_refused(self):
        for q in ({"agent": "x' OR 1=1 --"}, {"tool": "tdflow_undo; DROP TABLE events"}, {"kind": "anything"},
                  {"outcome": "ok' --"}, {"q": "100% of"}, {"q": "a b"}, {"q": "x" * 65}, {"after": "abc"},
                  {"limit": 0}, {"limit": 501}, {"after": -1}, {"session": "a b"}, {"agent": "x" * 300}):
            with self.assertRaises(D.BadQuery, msg=q):
                D.event_filter(D.clean_query(q))
        self.assertEqual(D.clean_query({"a": ["x"], "b": {"c": 1}, "d": True, "e": None, 5: "x"}), {})
        self.assertEqual(D.event_filter({})[0], "")
        self.assertEqual(D.event_filter({"outcome": "denied"})[0], "kind = 'deny'")

    def test_underscore_is_literal_in_text_search(self):
        _, params, _ = D.event_filter({"q": "level_1"})
        self.assertEqual(params[0], r"%level\_1%")


class StoreBackedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-dash-")
        self.st = Store(os.path.join(self.dir, "d")).open()
        s = self.st
        for i in range(3):
            base = {"agent_id": "agt_a", "token_id": "tok", "session_id": "ses_one", "tool": "tdflow_set_parameters",
                    "request_id": f"r{i}", "targets": [["update", f"/project1/fx/n{i}"]],
                    "args_redacted": json.dumps({"path": f"/project1/fx/n{i}", "values": {"amp": i}})}
            s.append("intent", decision="ALLOW", **base)
            s.append("outcome", decision="ALLOW", outcome="ok", **base)
        s.append("deny", decision="DENIED", rule_id="G0", error_code="DENIED", agent_id="agt_b", session_id="ses_two",
                 tool="tdflow_delete_operator", request_id="r9", targets=[["delete", "/project1/out"]])
        s.append("intent", decision="ALLOW", agent_id="agt_b", token_id="tok2", session_id="ses_two",
                 tool="tdflow_create_operator", request_id="r10", targets=[["create", "/project1/fx/x"]])   # no outcome
        s.append("outcome", decision="ALLOW", outcome="error", error_code="EXECUTION_FAILED", agent_id="agt_a",
                 token_id="tok", session_id="ses_one", tool="tdflow_connect", request_id="r11",
                 targets=[["update", "/project1/fx/n1"]])

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_search_and_live_tail(self):
        page = D.events_page(self.st, {})
        self.assertEqual((len(page["events"]), page["more"], page["last_seq"]), (9, False, 9))
        self.assertEqual(page["events"][0]["seq"], 9)                    # newest first
        self.assertEqual(page["events"][0]["targets"], [["update", "/project1/fx/n1"]])   # decoded
        self.assertTrue(page["events"][0]["agent_text_untrusted"])
        tail = D.events_page(self.st, {"after": 7})
        self.assertEqual([e["seq"] for e in tail["events"]], [9, 8])
        self.st.append("kill_switch", outcome="on")
        self.assertEqual([e["seq"] for e in D.events_page(self.st, {"after": 9})["events"]], [10])   # appears at once
        self.assertEqual(len(D.events_page(self.st, {"agent": "agt_b"})["events"]), 2)
        self.assertEqual(len(D.events_page(self.st, {"outcome": "denied"})["events"]), 1)
        self.assertEqual(len(D.events_page(self.st, {"q": "fx/n1"})["events"]), 3)
        two = D.events_page(self.st, {"limit": 2})
        self.assertEqual((len(two["events"]), two["more"]), (2, True))
        self.assertNotIn("hash", two["events"][0])                       # chain internals are not part of the view

    def test_report_verifies_against_the_checkpoint(self):               # ADR-0005 gate
        rep = D.build_report(self.st, {"session": "ses_one"}, "0.0.test")
        self.assertEqual((rep["range"]["events"], rep["range"]["first_seq"], rep["range"]["last_seq"]), (7, 1, 9))
        self.assertFalse(rep["verification"]["covered"])                 # no keyed checkpoint covers the range yet
        self.assertIn("NOT fully verified", rep["verification"]["statement"])
        self.st.checkpoint()
        rep = D.build_report(self.st, {"session": "ses_one"}, "0.0.test")
        v = rep["verification"]
        self.assertEqual((v["ok"], v["covered"], v["verified_through"]), (True, True, 9))
        self.assertEqual(rep["summary"]["by_tool"]["tdflow_set_parameters"], {"calls": 3, "ok": 3, "denied": 0, "failed": 0})
        self.assertEqual(rep["summary"]["changed_paths"], ["/project1/fx/n0", "/project1/fx/n1", "/project1/fx/n2"])
        self.assertEqual(rep["summary"]["failed"], 1)
        self.assertRegex(rep["sha256"], "^[0-9a-f]{64}$")
        md = D.report_markdown(rep)
        self.assertIn("**verified**", md)
        self.assertIn("| `tdflow_set_parameters` | 3 | 3 | 0 | 0 |", md)
        # An event after the checkpoint is reported honestly as not covered.
        self.st.append("outcome", decision="ALLOW", outcome="ok", agent_id="agt_a", session_id="ses_one",
                       tool="tdflow_undo", request_id="r12", targets=[["update", "/project1/fx/n0"]])
        self.assertFalse(D.build_report(self.st, {"session": "ses_one"}, "v")["verification"]["covered"])

    def test_report_shows_tampering_and_unaccounted_executions(self):
        self.st.checkpoint()
        rng = D.build_report(self.st, {"from_seq": 1, "to_seq": 9}, "v")
        self.assertEqual([u["tool"] for u in rng["unaccounted_executions"]], ["tdflow_create_operator"])
        self.assertEqual(rng["summary"]["denied"], 1)
        self.st.conn.execute("DROP TRIGGER events_no_update")
        self.st.conn.execute("UPDATE events SET outcome='forged' WHERE seq=2")
        bad = D.build_report(self.st, {"from_seq": 1, "to_seq": 9}, "v")
        self.assertEqual((bad["verification"]["ok"], bad["verification"]["covered"], bad["verification"]["first_bad_seq"]),
                         (False, False, 2))
        self.assertIn("NOT fully verified", D.report_markdown(bad))

    def test_report_needs_a_scope(self):
        for q in ({}, {"session": "a b"}, {"from_seq": "x"}):
            with self.assertRaises(D.BadQuery):
                D.build_report(self.st, q, "v")


if __name__ == "__main__":
    unittest.main()
