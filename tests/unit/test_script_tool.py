"""Phase 3: tdflow_execute_script through the engine — approval of the exact text, facade calls are normal audited
calls under the agent's identity and inside the declared scope, no hidden approvals, kill switch, budgets."""
import json
import unittest

from tdflowmcp import codemode as CM
from tests.unit.test_approval_flow import Base

S = "tdflow_execute_script"
CODE = ("a = tdflow.get_operator(path='/project1/fx/level1')\n"
        "tdflow.set_parameters(path='/project1/fx/level1', values={'opacity': 0.5})\n"
        "print('set')\nresult = a['done']")


class ScriptToolTests(Base):
    def setUp(self):
        super().setUp()
        self.eng.code_mode = True

    def sargs(self, code=CODE, scope="/project1/fx"):
        return {"scope": scope, "code": code}

    def approve_and_run(self, tok, args):
        err, out = self.call(tok, S, args)
        self.assertEqual(out.get("code"), "APPROVAL_REQUIRED", out)
        self.decide(out["approval_id"])
        return self.call(tok, S, args)

    def test_needs_approval_of_the_exact_text_then_runs_through_the_engine(self):
        err, out = self.call(self.OP, S, self.sargs())
        self.assertTrue(err)
        self.assertEqual((out["code"], out["rule_id"]), ("APPROVAL_REQUIRED", "A1"))
        self.assertIn("Code Mode script", out["reasons"][0])
        self.assertEqual(self.ad.executed, [])                                   # nothing ran
        row = self.store.approval_get(out["approval_id"])
        self.assertEqual(json.loads(row["args"])["code"], CODE)                   # the user sees the complete text
        self.assertIn("script_sha256", row["targets"])
        self.decide(out["approval_id"])
        err, out = self.call(self.OP, S, self.sargs(CODE + "\n"))                   # a different text is a new request
        self.assertEqual(out["code"], "APPROVAL_REQUIRED")
        err, out = self.call(self.OP, S, self.sargs())
        self.assertFalse(err, out)
        self.assertEqual((out["result"], out["output"], out["calls"]), ("tdflow_get_operator", "set", 2))
        self.assertEqual([t for t, _ in self.ad.executed], ["tdflow_get_operator", "tdflow_set_parameters"])
        self.assertTrue(out["log"][1]["audit_seq"])                              # the change is audited on its own
        self.assertEqual(self.call(self.OP, S, self.sargs())[1]["code"], "APPROVAL_REQUIRED")   # single use
        evs = [e for e in self.store.events(limit=200) if e["tool"] == "tdflow_set_parameters" and e["kind"] == "outcome"]
        self.assertEqual(evs[0]["agent_id"], self.op.id)                         # under the agent's identity

    def test_builders_and_out_of_scope_are_denied_before_any_request(self):
        err, out = self.call(self.BD, S, self.sargs())
        self.assertEqual(out["code"], "DENIED")                                  # no execute in the role
        err, out = self.call(self.OP, S, self.sargs(scope="/project1"))
        self.assertEqual(out["code"], "DENIED")                                  # no grant on the scope
        err, out = self.call(self.OP, S, self.sargs("import os"))
        self.assertEqual(out["code"], "SCRIPT_REFUSED")                          # refused before a request
        self.assertEqual(self.store.approvals_list(), [])

    def test_facade_calls_stay_in_scope_and_never_create_hidden_requests(self):
        err, out = self.approve_and_run(self.OP, self.sargs("tdflow.get_operator(path='/project1/other')"))
        self.assertEqual(out["code"], "SCOPE_OUTSIDE_SCRIPT")
        err, out = self.approve_and_run(self.OP, self.sargs(
            "tdflow.set_parameters(path='/project1/fx/level1', expressions={'opacity': 'absTime.seconds'})"))
        self.assertEqual(out["code"], "APPROVAL_REQUIRED_IN_SCRIPT")
        self.assertEqual(len([a for a in self.store.approvals_list() if a["tool"] != S]), 0)
        self.assertEqual(self.ad.executed, [])

    def test_kill_switch_and_budgets_stop_the_script(self):
        self.eng._suspended = lambda: flag[0]
        flag = [False]
        code = "i = 0\nwhile True:\n    i += 1\n    if i == 1000:\n        tdflow.get_operator(path='/project1/fx/a')\n"
        args = self.sargs(code)
        err, out = self.call(self.OP, S, args)
        self.decide(out["approval_id"])
        orig = CM.run

        def run(*a, **kw):                                       # suspend while it is running
            stop = kw["stop"]
            cnt = [0]

            def later():
                cnt[0] += 1
                if cnt[0] > 50:
                    flag[0] = True
                return stop()
            kw["stop"] = later
            return orig(*a, **kw)
        CM.run = run
        try:
            err, out = self.call(self.OP, S, args)
        finally:
            CM.run = orig
        self.assertEqual(out["code"], "BUDGET_EXCEEDED")
        self.assertIn("suspended", out["message"])

    def test_budgets_cannot_be_caught_or_forged(self):
        rep = CM.run("for i in range(500):\n    tdflow.get_operator(path='/p')\nresult = 'kept going'",
                     lambda t, a: {"structuredContent": {}}, ["tdflow_get_operator"])
        self.assertEqual((rep["ok"], rep["error"]["code"], rep["calls"], rep["result"]),
                         (False, "BUDGET_EXCEEDED", CM.MAX_CALLS, None))
        t = [0.0]
        rep = CM.run("while True:\n    x = 1\n", lambda t, a: {}, [], clock=lambda: t.__setitem__(0, t[0] + 0.01) or t[0])
        self.assertEqual(rep["error"]["code"], "BUDGET_EXCEEDED")
        self.assertTrue(issubclass(CM.Budget, BaseException) and not issubclass(CM.Budget, Exception))

    def test_code_mode_is_off_until_the_user_turns_it_on(self):
        self.eng.code_mode = False
        self.assertNotIn(S, [t["name"] for t in self.eng.list_tools(self.OP)])
        err, out = self.call(self.OP, S, self.sargs())
        self.assertEqual(out["code"], "CODE_MODE_OFF")
        self.assertEqual(self.store.approvals_list(), [])
        self.eng.code_mode = True
        self.assertIn(S, [t["name"] for t in self.eng.list_tools(self.OP)])
        err, out = self.call(self.OP, S, self.sargs())
        aid = out["approval_id"]
        self.decide(aid)
        self.eng.code_mode = False                                   # turned off after the approval
        self.assertEqual(self.call(self.OP, S, self.sargs())[1]["code"], "CODE_MODE_OFF")

    def test_undo_memory_and_unknown_tools_are_not_in_the_facade(self):
        for code in ("tdflow.undo()", "tdflow.task_list()", "tdflow.project_info()"):
            err, out = self.approve_and_run(self.OP, self.sargs(code))
            self.assertEqual(out["code"], "SCRIPT_ERROR", code)
            self.assertIn("AttributeError", out["message"])

    def test_invisible_characters_are_refused_and_audited(self):
        err, out = self.call(self.OP, S, self.sargs("result = 'a​b'"))
        self.assertTrue(err)
        self.assertNotEqual(out["code"], "APPROVAL_REQUIRED")
        self.assertTrue(out.get("audit_seq"))

    def test_scope_refusals_and_internal_errors_are_audited(self):
        err, out = self.approve_and_run(self.OP, self.sargs("tdflow.get_operator(path='/project1/other')"))
        self.assertEqual(out["code"], "SCOPE_OUTSIDE_SCRIPT")
        self.assertTrue(out["log"][0]["audit_seq"])
        denies = [e for e in self.store.events(limit=50) if e["kind"] == "deny" and e["error_code"] == "SCOPE_OUTSIDE_SCRIPT"]
        self.assertEqual(len(denies), 1)

        def boom(tool, args):
            if args.get("path") == "/project1/fx":               # the scope check itself works
                return orig(tool, args)
            raise RuntimeError("internal detail /secret/path")
        self.ad.resolve, orig = boom, self.ad.resolve
        try:
            err, out = self.approve_and_run(self.OP, self.sargs("tdflow.get_operator(path='/project1/fx/a')"))
        finally:
            self.ad.resolve = orig
        self.assertEqual(out["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
