"""Phase 3: Code Mode — facade-only scripts, budgets, plain data, and the honest limits (not a sandbox)."""
import unittest

from tdflowmcp import codemode as C

TOOLS = ["tdflow_list_operators", "tdflow_set_parameters", "tdflow_execute_script", "tdflow_get_operator"]


def fake_call(log):
    def call(tool, args):
        log.append((tool, args))
        if args.get("path") == "/denied":
            return {"isError": True, "structuredContent": {"code": "DENIED", "message": "no grant"}}
        return {"isError": False, "structuredContent": {"tool": tool, "args": args, "audit_seq": len(log)}}
    return call


class CodeModeTests(unittest.TestCase):
    def test_script_calls_tools_through_the_facade_only(self):
        log = []
        rep = C.run("ops = [tdflow.get_operator(path='/p/' + n) for n in ('a', 'b')]\n"
                    "tdflow.set_parameters(path='/p/a', values={'x': 1})\nprint('done', len(ops))\nresult = len(ops)",
                    fake_call(log), TOOLS)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual((rep["result"], rep["calls"], rep["output"]), (2, 3, "done 2"))
        self.assertEqual([t for t, _ in log], ["tdflow_get_operator", "tdflow_get_operator", "tdflow_set_parameters"])
        self.assertEqual([x["audit_seq"] for x in rep["log"]], [1, 2, 3])        # every call is a normal audited call

    def test_a_denied_call_stops_the_script_with_its_code(self):
        rep = C.run("tdflow.get_operator(path='/denied')\nresult = 1", fake_call([]), TOOLS)
        self.assertEqual((rep["ok"], rep["error"]["code"], rep["result"]), (False, "DENIED", None))

    def test_no_imports_no_dunder_no_hidden_tools(self):
        for code in ("import os", "x = ().__class__", "eval('1')", "open('f')", "getattr(tdflow, 'x')",
                     "__import__('os')", "exec('1')", "globals()", "x = open", "tdflow._call('t', {})",
                     "tdflow.calls = 0", "try:\n    x = 1\nexcept:\n    pass", "with x:\n    pass",
                     "class A:\n    pass", "f = lambda: 1", "def g():\n    yield 1", "x = type(1)"):
            with self.assertRaises(C.ScriptError, msg=code):
                C.check_script(code)
        self.assertIsNone(C.check_script("d = {'a__b': 1}\nresult = [x * 2 for x in range(3)]"))   # plain code is fine
        rep = C.run("tdflow.execute_script(code='1')", fake_call([]), TOOLS)            # no recursion
        self.assertEqual(rep["error"]["code"], "SCRIPT_ERROR")
        rep = C.run("tdflow.delete_operator(path='/p')", fake_call([]), TOOLS)          # only granted tools exist
        self.assertIn("AttributeError", rep["error"]["message"])
        fac = C.Facade(fake_call([]), TOOLS)
        with self.assertRaises(AttributeError):
            fac.calls = 0                                                              # read-only facade

    def test_budgets(self):
        rep = C.run("for i in range(1000):\n    tdflow.get_operator(path='/p')", fake_call([]), TOOLS)
        self.assertEqual((rep["error"]["code"], rep["calls"]), ("BUDGET_EXCEEDED", C.MAX_CALLS))
        with self.assertRaises(C.ScriptError):
            C.check_script("x = 1\n" * 5000)

    def test_results_are_plain_data(self):
        rep = C.run("result = {'a': tdflow.get_operator(path='/p'), 'b': {1, 2}}", fake_call([]), TOOLS)
        self.assertTrue(rep["ok"])
        self.assertIsInstance(rep["result"], (dict, str))


if __name__ == "__main__":
    unittest.main()
