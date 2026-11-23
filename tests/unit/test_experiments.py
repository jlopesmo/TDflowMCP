"""Phase 3: experiments and project save — how the targets a copy resolves to are judged by policy (the adapter's
classification itself is exercised in TouchDesigner, run_td_mcp_tests.experiment_flow)."""
import unittest

from tdflowmcp.engine import APPROVABLE
from tdflowmcp.identity import Agent
from tdflowmcp.policy import Grant, Policy, ROLES, Target
from tdflowmcp.tools import core_registry


def agent(role, scope="/project1/fx/**"):
    return Agent("a", "A", role, True, (Grant(scope=scope, actions=frozenset(ROLES[role])),))


class ExperimentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.pol = Policy("/project1")
        self.pol.approvals = True
        self.copy = [Target("read", "/project1/fx/geo"), Target("create", "/project1/fx/geo_exp", op_type="geometryCOMP"),
                     Target("create", "/project1/fx/geo_exp/box1", op_type="boxSOP")]

    def test_a_plain_copy_is_a_plain_create(self):
        v = self.pol.evaluate(agent("builder"), self.copy)
        self.assertTrue(v.allow, v)

    def test_copied_contents_meet_the_creation_allowlist(self):
        v = self.pol.evaluate(agent("operator"), self.copy + [Target("create", "/project1/fx/geo_exp/w", op_type="webclientDAT")])
        self.assertEqual((v.allow, v.rule_id), (False, "G2"))                 # never through a copy

    def test_set_denylisted_parameters_deny_the_copy(self):
        v = self.pol.evaluate(agent("operator"), self.copy + [Target("update", "/project1/fx/geo_exp", change="param",
                                                                        param="externaltox")])
        self.assertEqual((v.allow, v.rule_id), (False, "G3"))

    def test_code_in_the_copy_needs_approval_and_builders_cannot(self):
        code = self.copy + [Target("execute", "/project1/fx/geo_exp/box1", change="expression", param="sizex")]
        self.assertEqual(self.pol.evaluate(agent("builder"), code).rule_id, "R1")
        v = self.pol.evaluate(agent("operator"), code)
        self.assertEqual((v.code, v.rule_id), ("APPROVAL_REQUIRED", "A1"))
        ex = [Target("execute", "/project1/fx/geo_exp/ex", op_type="executeDAT", change="copy")]
        self.assertIn("copies a executeDAT, an operator that runs code", self.pol.approval_reasons(ex)[0])

    def test_save_always_needs_approval_on_the_root(self):
        save = [Target("execute", "/project1", change="save")]
        self.assertEqual(self.pol.evaluate(agent("operator"), save).rule_id, "G0")            # scoped agent: no grant
        v = self.pol.evaluate(agent("operator", "/project1/**"), save)
        self.assertEqual((v.code, v.rule_id), ("APPROVAL_REQUIRED", "A1"))
        self.assertIn("cannot be undone", self.pol.approval_reasons(save)[0])

    def test_the_approval_binds_the_code_a_copy_carries(self):
        from tdflowmcp.engine import Resolution, _snapshot_fact
        a = _snapshot_fact(Resolution([], {"content_fact": ["copies_code_sha256", "aa"]}))
        b = _snapshot_fact(Resolution([], {"content_fact": ["copies_code_sha256", "bb"]}))
        self.assertEqual(a, [["copies_code_sha256", "aa"]])
        self.assertNotEqual(a, b)                                  # changed code between approval and execution

    def test_reasons_for_copied_code(self):
        r = self.pol.approval_reasons([Target("execute", "/p/x", change="copied_expression", param="amp"),
                                       Target("execute", "/p/y", change="copied_reference", param="material")])
        self.assertIn("carries an expression or bind on parameter amp", r[0])
        self.assertIn("carries parameter material", r[1])

    def test_registration(self):
        r = core_registry()
        for name in ("tdflow_experiment_create", "tdflow_experiment_promote", "tdflow_experiment_discard",
                     "tdflow_save_project"):
            self.assertIsNotNone(r.get(name), name)
        self.assertIn("tdflow_experiment_create", APPROVABLE["A1"])
        self.assertIn("tdflow_save_project", APPROVABLE["A1"])
        self.assertIn("tdflow_experiment_promote", APPROVABLE["A2"])


if __name__ == "__main__":
    unittest.main()
