"""Policy engine (security-model §3): hard denies, grants, classification, approvals, filtering."""
import unittest

from tdflowmcp.policy import (EXECUTABLE_TYPES, SAFE_CREATE_TYPES, Agent, Grant, Policy, Target, grants_for,
                              scope_matches)

P = Policy("/project1")


def agent(role="builder", scopes=("/project1/fx/**",), enabled=True, extra=()):
    return Agent("agt_1", "A", role, enabled, grants_for(role, scopes) + tuple(extra))


class ScopeTests(unittest.TestCase):
    def test_globs(self):
        self.assertTrue(scope_matches("/project1/fx/**", "/project1/fx"))
        self.assertTrue(scope_matches("/project1/fx/**", "/project1/fx/a/b"))
        self.assertFalse(scope_matches("/project1/fx/**", "/project1/fxx"))
        self.assertFalse(scope_matches("/project1/fx/**", "/project1/out1"))
        self.assertTrue(scope_matches("/project1/*", "/project1/geo1"))
        self.assertFalse(scope_matches("/project1/*", "/project1/geo1/x"))
        self.assertTrue(scope_matches("/project1/*/null1", "/project1/a/null1"))
        self.assertFalse(scope_matches("/project1/Geo", "/project1/geo"))           # case-sensitive
        with self.assertRaises(ValueError):
            Grant("project1/**", frozenset({"read"}))


class HardDenyTests(unittest.TestCase):
    def test_hard_denies_beat_any_grant(self):
        a = agent("operator", ("/**",))
        cases = [Target("read", "/sys/x"), Target("read", "/local/time"), Target("read", "/ui/panes"),
                 Target("read", "/project2/x"), Target("read", "/project1/TDflowMCP", protected="instance"),
                 Target("read", "/project1/TDflowMCP/src", protected="instance"),
                 Target("update", "/project1/mem", protected="tdflowproject"),
                 Target("delete", "/project1", ancestor_of="instance"),
                 Target("update", "/project1", ancestor_of="instance", change="flags"),
                 Target("update", "/project1", ancestor_of="instance", change="rename"),
                 Target("update", "/project1", ancestor_of="instance", change="extension"),
                 Target("update", "/project1/x", change="tags"),
                 Target("read", "/project1/../sys"), Target("read", "project1/x"), Target("read", "/project1//x"),
                 Target("read", "/project1/x/")]
        for t in cases:
            v = P.evaluate(a, [t])
            self.assertFalse(v.allow, t)
            self.assertEqual(v.code, "DENIED", t)
            self.assertTrue(v.rule_id.startswith("H"), (t, v.rule_id))

    def test_ancestor_param_change_is_not_hard_denied(self):
        a = agent("builder", ("/project1/**",))
        v = P.evaluate(a, [Target("update", "/project1", ancestor_of="instance", change="param", param="w")])
        self.assertTrue(v.allow)


class GrantTests(unittest.TestCase):
    def test_acceptance_item_1(self):
        a = Agent("agt_1", "A", "builder", True, (Grant("/project1/fx/**", frozenset({"read", "update"})),))
        self.assertTrue(P.evaluate(a, [Target("update", "/project1/fx/level1", change="param", param="opacity")]).allow)
        for t in (Target("update", "/project1/out1", change="param", param="x"),
                  Target("delete", "/project1/fx/level1"),
                  Target("create", "/project1/fx/new1", op_type="nullTOP")):
            self.assertEqual(P.evaluate(a, [t]).code, "DENIED", t)
        v = P.evaluate(a, [Target("update", "/project1/fx/level1", change="expression", param="opacity"),
                           Target("execute", "/project1/fx/level1")])
        self.assertFalse(v.allow)

    def test_role_caps_grants(self):
        a = Agent("agt_1", "A", "observer", True, (Grant("/project1/**", frozenset({"read", "update"})),))
        self.assertEqual(P.evaluate(a, [Target("update", "/project1/x")]).rule_id, "R1")
        self.assertTrue(P.evaluate(a, [Target("read", "/project1/x")]).allow)

    def test_explicit_deny_wins(self):
        a = agent("builder", ("/project1/**",), extra=(Grant("/project1/secret/**", frozenset({"read", "update"}),
                                                             effect="deny"),))
        self.assertEqual(P.evaluate(a, [Target("read", "/project1/secret/a")]).rule_id, "G1")
        self.assertTrue(P.evaluate(a, [Target("read", "/project1/other")]).allow)
        exact = agent("observer", ("/project1/**",), extra=(Grant("/project1/private", frozenset({"read"}),
                                                                  effect="deny"),))
        self.assertEqual(P.evaluate(exact, [Target("read", "/project1/private")]).rule_id, "G1")
        self.assertTrue(P.evaluate(exact, [Target("read", "/project1/private/allowed")]).allow)

    def test_all_or_nothing_mutations(self):
        a = agent()
        v = P.evaluate(a, [Target("update", "/project1/fx/a", change="param", param="x"),
                           Target("update", "/project1/out1", change="param", param="x")])
        self.assertFalse(v.allow)
        self.assertEqual(v.target, "/project1/out1")

    def test_disabled_or_missing_agent(self):
        self.assertEqual(P.evaluate(None, [Target("read", "/project1/fx")]).rule_id, "A0")
        self.assertEqual(P.evaluate(agent(enabled=False), [Target("read", "/project1/fx")]).rule_id, "A0")
        self.assertEqual(P.evaluate(agent(), []).code, "INVALID_TARGET")


class ExecuteClassificationTests(unittest.TestCase):
    def test_execute_always_needs_approval_in_phase_1(self):
        a = agent("operator", ("/project1/**",))
        v = P.evaluate(a, [Target("update", "/project1/t", change="text"), Target("execute", "/project1/t")])
        self.assertEqual((v.allow, v.code), (False, "APPROVAL_UNAVAILABLE"))
        b = agent("builder", ("/project1/**",))
        self.assertEqual(P.evaluate(b, [Target("execute", "/project1/t")]).code, "DENIED")   # deny beats approval

    def test_create_is_allowlisted(self):
        a = agent("operator", ("/project1/**",))
        from tdflowmcp.policy import APPROVABLE_CREATE_TYPES
        for typ in APPROVABLE_CREATE_TYPES:                       # D20: approvable for the operator role, never a builder
            v = P.evaluate(a, [Target("create", "/project1/x", op_type=typ), Target("execute", "/project1/x", op_type=typ)])
            self.assertEqual(v.rule_id, "A1", typ)
            vb = P.evaluate(agent("builder", ("/project1/**",)), [Target("create", "/project1/x", op_type=typ)])
            self.assertEqual(vb.rule_id, "G2", typ)
        for typ in sorted(EXECUTABLE_TYPES - APPROVABLE_CREATE_TYPES) + ["evaluateDAT", "fileinDAT", "examineDAT", "opfindDAT", "infoDAT",
                                               "parameterDAT", "touchoutTOP", "cplusplusCHOP", "webrenderTOP",
                                               "moviefileoutTOP", "timerCHOP", "selectTOP", "", "madeupTOP"]:
            v = P.evaluate(a, [Target("create", "/project1/x", op_type=typ)])
            self.assertEqual(v.rule_id, "G2", typ)
        self.assertTrue(P.evaluate(a, [Target("create", "/project1/x", op_type="noiseTOP")]).allow)
        self.assertFalse(SAFE_CREATE_TYPES & EXECUTABLE_TYPES)

    def test_param_denylist(self):
        a = agent("builder", ("/project1/**",))
        for name in ("extension1", "promoteextension1", "callbacks", "externaltox", "script", "reinitextensions",
                     "clone", "enablecloning", "opshortcut", "parentshortcut", "file", "plugin", "ext0object",
                     "ext3name"):
            self.assertEqual(P.evaluate(a, [Target("update", "/project1/c", change="param", param=name)]).rule_id,
                             "G3", name)

    def test_large_delete_needs_approval(self):
        a = agent("builder", ("/project1/**",))
        self.assertTrue(P.evaluate(a, [Target("delete", "/project1/x", count=50)]).allow)
        self.assertEqual(P.evaluate(a, [Target("delete", "/project1/x", count=51)]).code, "APPROVAL_UNAVAILABLE")
        self.assertEqual(P.evaluate(a, [Target("delete", "/project1")]).rule_id, "H6")

    def test_max_ops(self):
        a = agent("builder", ("/project1/**",))
        ts = [Target("update", f"/project1/a{i}", change="param", param="x") for i in range(51)]
        self.assertEqual(P.evaluate(a, ts).rule_id, "L1")

    def test_custom_page_counts_once_but_checks_every_control(self):
        path = "/project1/fx/controls"
        updates = [Target("update", path, change="custom_parameters", param=f"Slot{i}") for i in range(80)]
        targets = [Target("read", path), Target("execute", path), *updates]
        policy = Policy("/project1")
        policy.approvals = True
        allowed = agent("operator")
        restricted = Agent("agt_2", "Restricted", "operator", True, (
            Grant("/project1/fx/**", frozenset({"read", "update", "execute"}), param_denylist=("Slot79",)),))
        self.assertEqual(policy.evaluate(allowed, targets).code, "APPROVAL_REQUIRED")
        self.assertTrue(policy.evaluate(allowed, targets, approved=True).allow)
        self.assertEqual(policy.evaluate(restricted, targets, approved=True).rule_id, "G3")


class FilterTests(unittest.TestCase):
    def test_recursive_reads_filter(self):
        a = agent("observer", ("/project1/fx/**",))
        ts = [Target("read", "/project1/fx"), Target("read", "/project1/fx/a"), Target("read", "/project1/out1"),
              Target("read", "/project1/fx/TDflowMCP", protected="instance")]
        keep, n = P.filter_readable(a, ts)
        self.assertEqual([t.path for t in keep], ["/project1/fx", "/project1/fx/a"])
        self.assertEqual(n, 2)

    def test_policy_version_changes_with_grants(self):
        self.assertNotEqual(P.version(agent()), P.version(agent(scopes=("/project1/**",))))


if __name__ == "__main__":
    unittest.main()
