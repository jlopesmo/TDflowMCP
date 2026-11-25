"""Owner D25 (ADR-0016): the capability matrix, permission-change planning, and the agent's own view.

The matrix is a view of the policy engine, so these tests check it **against** ``Policy.evaluate``: for every agent
fixture and every capability, the status the matrix claims must be what the engine decides for representative targets
inside (and outside) the scopes it lists. Protected rows must be hard denies for even the most privileged agent.
"""
import json
import os
import shutil
import tempfile
import unittest

from tdflowmcp import capabilities as C
from tdflowmcp.engine import Engine
from tdflowmcp.identity import Identity
from tdflowmcp.policy import ROLES, Agent, Grant, Policy, Target
from tdflowmcp.store import Store
from tdflowmcp.tools import core_registry

ROOT = "/project1"
MANIFEST = os.path.join(os.path.dirname(__file__), "..", "..", "src", "component", "manifest.json")
FACTS = {"code_mode": True, "approvals": True, "root": ROOT}


def targets_for(cap_id, path):
    """Representative targets of one use of the capability at ``path`` (as the TD adapter would resolve them)."""
    T = Target
    return {
        "inspect": [T("read", path)], "capture": [T("read", path)], "mem_read": [T("read", path)],
        "set_values": [T("update", path, change="param", param="tx")],
        "wire": [T("update", path, change="wire"), T("read", path + "_src")],
        "dat_data": [T("update", path, change="text")],
        "expressions": [T("update", path, change="expression", param="tx"),
                        T("execute", path, change="expression", param="tx")],
        "exec_text": [T("update", path, change="text"), T("execute", path, change="text")],
        "create_plain": [T("create", path, exists=False, op_type="noiseTOP")],
        "create_exec": [T("create", path, exists=False, op_type="executeDAT"),
                        T("execute", path, exists=False, op_type="executeDAT")],
        "activate_exec": [T("update", path, change="activation", param="active"),
                          T("execute", path, change="activation", param="active")],
        "delete_small": [T("delete", path, count=3)], "delete_large": [T("delete", path, count=51)],
        "code_mode": [T("execute", path, change="script")],
        "exp_create": [T("read", path), T("create", path + "_exp", exists=False, op_type="noiseTOP")],
        "exp_promote": [T("delete", path), T("update", path + "_exp", change="rename")],
        "exp_discard": [T("delete", path)], "undo": [T("update", path, change="param", param="tx")],
        "save": [T("execute", ROOT, change="save")], "lease": [T("update", path)],
    }.get(cap_id)


def inside(scope):
    base = scope[:-3] if scope.endswith("/**") else scope
    return (base.rstrip("/") or "") + "/zz_probe"


AGENTS = [
    Agent("a_obs", "Obs", "observer", grants=(Grant(ROOT + "/**", frozenset(ROLES["observer"])),)),
    Agent("a_bld", "Bld", "builder", grants=(Grant(ROOT + "/fx/**", frozenset(ROLES["builder"])),)),
    Agent("a_op", "Op", "operator", grants=(Grant(ROOT + "/**", frozenset(ROLES["operator"])),)),
    Agent("a_opfx", "OpFx", "operator", grants=(Grant(ROOT + "/fx/**", frozenset(ROLES["operator"])),)),
    Agent("a_narrow", "Narrow", "builder", grants=(Grant(ROOT + "/**", frozenset({"read", "update"})),
                                                  Grant(ROOT + "/secret/**", frozenset({"update"}), "deny"))),
    Agent("a_none", "None", "builder", grants=()),
    Agent("a_off", "Off", "operator", enabled=False, grants=(Grant(ROOT + "/**", frozenset(ROLES["operator"])),)),
]


class MatrixAgainstPolicyTests(unittest.TestCase):
    def test_every_claim_matches_the_policy_engine(self):
        pol = Policy(ROOT)
        pol.approvals = True
        checked = 0
        for a in AGENTS:
            view = C.agent_view(a, FACTS)
            for c in C.CATALOG:
                t = targets_for(c.id, "")
                if t is None:
                    continue
                st = view[c.id]
                with self.subTest(agent=a.id, cap=c.id, status=st["status"]):
                    if st["status"] in ("available", "approval"):
                        self.assertTrue(st["where"], "an allowed capability names its scopes")
                        for scope in st["where"]:
                            v = pol.evaluate(a, targets_for(c.id, inside(scope)))
                            if st["status"] == "available":
                                self.assertTrue(v.allow, (v.code, v.rule_id, v.reason))
                            else:
                                self.assertEqual(v.code, "APPROVAL_REQUIRED", (v.rule_id, v.reason))
                            checked += 1
                        for scope in st["except"]:
                            self.assertFalse(pol.evaluate(a, targets_for(c.id, inside(scope))).allow)
                    else:
                        self.assertEqual(st["status"], "denied")
                        for probe in (ROOT + "/zz_probe", ROOT + "/fx/zz_probe"):
                            v = pol.evaluate(a, targets_for(c.id, probe))
                            self.assertFalse(v.allow)
                            self.assertNotEqual(v.code, "APPROVAL_REQUIRED", (probe, v.rule_id))
                        checked += 1
        self.assertGreater(checked, 100)

    def test_protected_rows_are_hard_denies_even_for_a_full_operator(self):
        pol = Policy(ROOT)
        pol.approvals = True
        op = AGENTS[2]
        T = Target
        cases = {
            "p_system": [T("update", "/sys/x", change="param", param="tx")],
            "p_outside": [T("update", "/other/x", change="param", param="tx")],
            "p_component": [T("update", ROOT + "/TDflowMCP", protected="instance", change="param", param="tx")],
            "p_memory": [T("update", ROOT + "/tdflowProject/r", protected="tdflowproject", change="text")],
            "p_ancestors": [T("delete", ROOT + "/holder", ancestor_of="instance")],
            "p_root": [T("delete", ROOT)],
            "p_params": [T("update", ROOT + "/x", change="param", param="callbacks")],
            "p_netops": [T("create", ROOT + "/web1", exists=False, op_type="webserverDAT"),
                         T("execute", ROOT + "/web1", exists=False, op_type="webserverDAT")],
        }
        for cid, t in cases.items():
            with self.subTest(cap=cid):
                self.assertEqual(C.agent_status(C.BY_ID[cid], op, FACTS)["status"], "protected")
                for approved in (False, True):
                    v = pol.evaluate(op, t, approved=approved)
                    self.assertFalse(v.allow, (cid, approved))
                    self.assertEqual(v.code, "DENIED")

    def test_every_active_tool_appears_in_the_matrix(self):
        with open(MANIFEST, encoding="utf-8") as f:
            active = json.load(f)["active_tools"]
        listed = {t for c in C.CATALOG for t in c.tools}
        self.assertEqual(sorted(set(active) - listed), [])
        names = {t.name for t in core_registry().all()}
        self.assertEqual(sorted(listed - names), [])          # never a tool that does not exist

    def test_statuses_and_labels(self):
        self.assertEqual(set(C.LABELS), set(C.STATUSES))
        self.assertEqual([C.LABELS[s] for s in C.STATUSES],
                         ["Available", "Approval Required", "Denied", "Not Implemented", "Protected"])
        seen = {C.agent_status(c, AGENTS[3], dict(FACTS, code_mode=False))["status"] for c in C.CATALOG}
        self.assertEqual(seen, set(C.STATUSES))

    def test_settings_inactive_tools_and_disabled_agents(self):
        op = AGENTS[2]
        off = C.agent_status(C.BY_ID["code_mode"], op, dict(FACTS, code_mode=False))
        self.assertEqual((off["status"], off["fix"]), ("denied", "setting:code_mode"))
        self.assertEqual(C.engine_status(C.BY_ID["code_mode"], dict(FACTS, code_mode=False))["status"], "approval")
        gone = C.agent_status(C.BY_ID["capture"], op, dict(FACTS, active=frozenset({"tdflow_whoami"})))
        self.assertEqual(gone["status"], "not_implemented")
        self.assertEqual(C.agent_status(C.BY_ID["inspect"], AGENTS[-1], FACTS)["status"], "denied")
        self.assertEqual(C.agent_status(C.BY_ID["identity"], AGENTS[-2], FACTS)["status"], "available")

    def test_save_needs_a_scope_on_the_root_itself(self):
        self.assertEqual(C.agent_status(C.BY_ID["save"], AGENTS[3], FACTS)["status"], "denied")      # /fx/** only
        self.assertEqual(C.agent_status(C.BY_ID["save"], AGENTS[2], FACTS)["status"], "approval")

    def test_role_columns(self):
        rows = {r["id"]: r for r in C.catalog_view(FACTS)}
        self.assertEqual(rows["expressions"]["roles"]["builder"]["status"], "denied")
        self.assertEqual(rows["expressions"]["roles"]["operator"]["status"], "approval")
        self.assertEqual(rows["set_values"]["roles"]["observer"]["status"], "denied")
        self.assertEqual(rows["inspect"]["roles"]["observer"]["status"], "available")
        self.assertEqual(rows["p_root"]["roles"]["operator"]["status"], "protected")
        self.assertEqual(rows["n_python"]["engine"]["status"], "not_implemented")


class PlanChangeTests(unittest.TestCase):
    def setUp(self):
        self.a = Agent("agt_1", "Claude Code", "builder", grants=(Grant(ROOT + "/**", frozenset(ROLES["builder"])),))
        self.rows = [{"id": 7, "scope": ROOT + "/**", "actions": sorted(ROLES["builder"]), "effect": "allow"}]

    def pv(self, change, agent=None, rows=None):
        return C.preview(agent or self.a, self.rows if rows is None else rows, change, ROOT, FACTS)

    def test_role_up_is_an_escalation_and_keeps_scopes(self):
        p = self.pv({"role": "operator"})
        self.assertTrue(p["escalation"])
        self.assertEqual(p["plan"]["rewrite"], [[7, sorted(ROLES["operator"])]])
        ids = {d["id"]: d for d in p["diff"]}
        self.assertEqual((ids["expressions"]["from"], ids["expressions"]["to"]), ("denied", "approval"))
        self.assertTrue(ids["expressions"]["escalation"])

    def test_role_down_narrows_and_drops_what_the_role_cannot_hold(self):
        a = Agent("agt_1", "A", "builder", grants=(Grant(ROOT + "/**", frozenset(ROLES["builder"])),),
                  capabilities=frozenset({"memory:repo:write"}))
        rows = self.rows + [{"id": 8, "scope": ROOT + "/fx/**", "actions": ["delete"], "effect": "allow"},
                            {"id": 9, "scope": ROOT + "/s/**", "actions": ["update"], "effect": "deny"}]
        p = self.pv({"role": "observer"}, a, rows)
        self.assertFalse(p["escalation"])
        self.assertEqual(p["plan"]["rewrite"], [[7, ["read"]]])
        self.assertEqual(p["plan"]["remove"], [8])                    # nothing left for the role
        self.assertEqual(p["plan"]["capabilities"], [])               # observers cannot record
        self.assertTrue(any("deny" == g["effect"] for g in p["result"]["grants"]))   # deny grants never change
        self.assertTrue(p["notes"])

    def test_wider_scope_new_capability_and_removed_deny_are_escalations(self):
        a = Agent("agt_1", "A", "builder", grants=(Grant(ROOT + "/fx/**", frozenset(ROLES["builder"])),
                                                   Grant(ROOT + "/fx/s/**", frozenset({"update"}), "deny")))
        rows = [{"id": 1, "scope": ROOT + "/fx/**", "actions": sorted(ROLES["builder"]), "effect": "allow"},
                {"id": 2, "scope": ROOT + "/fx/s/**", "actions": ["update"], "effect": "deny"}]
        self.assertTrue(self.pv({"add_grants": [{"scope": ROOT + "/**", "actions": ["read"], "effect": "allow"}]},
                                a, rows)["escalation"])
        self.assertTrue(self.pv({"capabilities": {"memory:repo:write": True}}, a, rows)["escalation"])
        self.assertTrue(self.pv({"remove_grants": [2]}, a, rows)["escalation"])
        self.assertFalse(self.pv({"remove_grants": [1]}, a, rows)["escalation"])
        self.assertFalse(self.pv({"add_grants": [{"scope": ROOT + "/fx/t/**", "actions": ["delete"],
                                                  "effect": "deny"}]}, a, rows)["escalation"])

    def test_refusals(self):
        bad = [
            ({"protected": True}, "UNSUPPORTED_CHANGE"),
            ({"capabilities": {"python:raw": True}}, "UNSUPPORTED_CHANGE"),
            ({"role": "admin"}, "ROLE_NOT_ALLOWED"),
            ({"add_grants": [{"scope": "/sys/**", "actions": ["read"]}]}, "SCOPE_OUTSIDE_PROJECT_ROOT"),
            ({"add_grants": [{"scope": "/project10/**", "actions": ["read"]}]}, "SCOPE_OUTSIDE_PROJECT_ROOT"),
            ({"add_grants": [{"scope": ROOT + "/**", "actions": ["execute"]}]}, "ACTION_NOT_IN_ROLE"),
            ({"add_grants": [{"scope": ROOT + "/a**", "actions": ["read"]}]}, "INVALID_SCOPE"),
            ({"add_grants": [{"scope": ROOT + "/**", "actions": ["fly"]}]}, "INVALID_CHANGE"),
            ({"remove_grants": [99]}, "GRANT_NOT_FOUND"),
            ({"remove_grants": [True]}, "INVALID_CHANGE"),
            ({"role": "observer", "capabilities": {"memory:repo:write": True}}, "CAPABILITY_NOT_ALLOWED"),
            ({"add_grants": [{"scope": ROOT + "/x", "actions": ["read"]}] * 11}, "INVALID_CHANGE"),
        ]
        for change, code in bad:
            with self.subTest(change=change):
                with self.assertRaises(C.ChangeError) as cm:
                    self.pv(change)
                self.assertEqual(cm.exception.code, code)

    def test_split_grants_combine_like_the_policy(self):
        # Review 22 (security 1): execute from a second grant on a sub-scope is approval, and adding it is more access.
        pol = Policy(ROOT)
        pol.approvals = True
        op = Agent("agt_o", "O", "operator", grants=(Grant(ROOT + "/**", frozenset({"read", "create", "update", "delete"})),))
        rows = [{"id": 1, "scope": ROOT + "/**", "actions": ["create", "delete", "read", "update"], "effect": "allow"}]
        p = self.pv({"add_grants": [{"scope": ROOT + "/sub/**", "actions": ["execute"], "effect": "allow"}]}, op, rows)
        self.assertTrue(p["escalation"])
        after = p["result"]["matrix"]
        self.assertEqual(after["expressions"]["status"], "approval")
        self.assertEqual(after["expressions"]["where"], [ROOT + "/sub/**"])
        a2 = Agent("agt_o", "O", "operator", grants=op.grants + (Grant(ROOT + "/sub/**", frozenset({"execute"})),))
        v = pol.evaluate(a2, targets_for("expressions", ROOT + "/sub/n"))
        self.assertEqual(v.code, "APPROVAL_REQUIRED")

    def test_a_disabled_agent_or_code_mode_off_does_not_hide_more_access(self):
        # Review 22 (security 2): the change is judged as it would act once the agent is enabled or Code Mode is on.
        off = Agent("agt_1", "A", "builder", enabled=False, grants=(Grant(ROOT + "/a/**", frozenset({"read"})),))
        rows = [{"id": 1, "scope": ROOT + "/a/**", "actions": ["read"], "effect": "allow"}]
        p = self.pv({"add_grants": [{"scope": ROOT + "/**", "actions": ["read", "update"], "effect": "allow"}]}, off, rows)
        self.assertTrue(p["escalation"])
        op = Agent("agt_2", "B", "operator", grants=(Grant(ROOT + "/**", frozenset({"read"})),))
        rows2 = [{"id": 2, "scope": ROOT + "/**", "actions": ["read"], "effect": "allow"}]
        p2 = C.preview(op, rows2, {"add_grants": [{"scope": ROOT + "/**", "actions": ["execute"], "effect": "allow"}]},
                       ROOT, dict(FACTS, code_mode=False))
        self.assertTrue(p2["escalation"])
        deny_rows = rows + [{"id": 3, "scope": ROOT + "/a/s/**", "actions": ["read"], "effect": "deny"}]
        off2 = Agent("agt_1", "A", "builder", enabled=False, grants=off.grants + (Grant(ROOT + "/a/s/**", frozenset({"read"}), "deny"),))
        self.assertTrue(self.pv({"remove_grants": [3]}, off2, deny_rows)["escalation"])

    def test_a_role_change_keeps_the_name_true(self):
        a = Agent("agt_1", "Claude Code (Builder)", "builder", grants=self.a.grants)
        p = self.pv({"role": "operator"}, a)
        self.assertEqual(p["plan"]["display_name"], "Claude Code (Operator)")
        self.assertNotIn("display_name", self.pv({"role": "operator"})["plan"])

    def test_a_deny_grant_may_name_execute_for_any_role(self):
        p = self.pv({"add_grants": [{"scope": ROOT + "/x/**", "actions": ["execute", "update"], "effect": "deny"}]})
        self.assertFalse(p["escalation"])

    def test_digest_binds_the_stored_state_and_the_change(self):
        d1 = self.pv({"role": "operator"})["digest"]
        self.assertEqual(d1, self.pv({"role": "operator"})["digest"])
        self.assertNotEqual(d1, self.pv({"role": "observer"})["digest"])
        moved = [dict(self.rows[0], scope=ROOT + "/fx/**")]
        self.assertNotEqual(d1, self.pv({"role": "operator"}, rows=moved)["digest"])

    def test_nothing_changes(self):
        self.assertFalse(self.pv({})["changes_anything"])
        self.assertFalse(self.pv({"role": "builder"})["changes_anything"])


class AgentViewTests(unittest.TestCase):
    def test_own_view_keeps_effective_and_supported_apart(self):
        out = C.for_agent(AGENTS[1], FACTS, "http://127.0.0.1:9480/ui/#capabilities")
        self.assertIn("YOUR effective permissions", out["about"])
        mine = {i["capability"] for s in out["your_capabilities"].values() for i in s}
        self.assertIn("Set constant parameter values", mine)
        denied = {i["capability"] for i in out["not_available_to_you"]["denied"]}
        self.assertIn("Set expressions and parameters that name operators or files", denied)
        self.assertTrue(out["not_available_to_you"]["protected"] and out["not_available_to_you"]["not_implemented"])
        self.assertEqual(out["dashboard"]["capabilities_page"], "http://127.0.0.1:9480/ui/#capabilities")

    def test_no_page_without_a_listener(self):
        self.assertIn("Open Dashboard", C.for_agent(AGENTS[1], FACTS, "")["dashboard"]["how"])


class ApplyAndEngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-caps-")
        self.st = Store(os.path.join(self.dir, "d")).open()
        self.idn = Identity(self.st)
        self.a, tok = self.idn.create_agent("A", "builder", [ROOT + "/fx/**"])
        self.tok = tok
        self.tid = self.idn.authenticate(tok)
        pol = Policy(ROOT)
        pol.approvals = True
        self.eng = Engine(self.st, self.idn, pol, core_registry(), None, [t.name for t in core_registry().all()],
                          lambda: False)
        self.eng.dashboard_page = lambda: "http://127.0.0.1:9480/ui/#capabilities"

    def tearDown(self):
        self.st.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_apply_changes_role_grants_and_cancels_approvals_atomically(self):
        pv0 = self.eng.policy.version(self.idn.agents[self.a.id])
        ap = self.st.approval_request(self.a.id, self.tid, "tdflow_delete_operator", "{}", "sha", "[]", pv0,
                                      ["A2"])["approval"]
        rows = self.st.grants_of(self.a.id)
        p = C.preview(self.idn.agents[self.a.id], rows, {"role": "operator", "add_grants": [
            {"scope": ROOT + "/out/**", "actions": ["read"], "effect": "allow"}]}, ROOT, self.eng.capability_facts())
        n = self.idn.apply_permissions(self.a.id, p["plan"])
        self.assertEqual(n, 1)
        self.assertEqual(self.st.approval_get(ap["id"])["state"], "cancelled")
        a = self.idn.agents[self.a.id]
        self.assertEqual(a.role, "operator")
        self.assertEqual(sorted(g.scope for g in a.grants), [ROOT + "/fx/**", ROOT + "/out/**"])
        self.assertIn("execute", next(g for g in a.grants if g.scope == ROOT + "/fx/**").actions)
        self.assertNotEqual(self.eng.policy.version(a), pv0)
        # the stored state is the same after a restart
        self.st.close()
        self.st = Store(os.path.join(self.dir, "d")).open()
        self.assertEqual(Identity(self.st).agents[self.a.id].role, "operator")

    def test_failed_apply_changes_nothing(self):
        before = (self.idn.agents[self.a.id].role, self.st.grants_of(self.a.id))
        plan = {"role": "operator", "rewrite": [], "remove": [], "capabilities": [],
                "add": [{"scope": ROOT + "/x/**", "actions": ["read"], "effect": "allow"}, None]}
        with self.assertRaises(Exception):
            self.idn.apply_permissions(self.a.id, plan)
        self.assertEqual((self.idn.agents[self.a.id].role, self.st.grants_of(self.a.id)), before)

    def test_tool_reports_own_capabilities_and_the_page_without_secrets(self):
        r = self.eng.call(self.tid, "tdflow_capabilities", {})
        self.assertFalse(r["isError"])
        sc = r["structuredContent"]
        self.assertEqual(sc["agent"]["role"], "builder")
        self.assertEqual(sc["dashboard"]["capabilities_page"], "http://127.0.0.1:9480/ui/#capabilities")
        text = json.dumps(r)
        self.assertNotIn(self.tok, text)
        self.assertNotIn(self.tok.split(".", 1)[1], text)
        w = self.eng.call(self.tid, "tdflow_whoami", {})["structuredContent"]
        self.assertEqual(w["capabilities"]["tool"], "tdflow_capabilities")
        self.assertEqual(w["capabilities"]["page"], "http://127.0.0.1:9480/ui/#capabilities")

    def test_tool_is_listed_even_without_scopes_and_audited(self):
        rows = self.st.grants_of(self.a.id)
        p = C.preview(self.idn.agents[self.a.id], rows, {"remove_grants": [rows[0]["id"]]}, ROOT,
                      self.eng.capability_facts())
        self.idn.apply_permissions(self.a.id, p["plan"])
        names = [t["name"] for t in self.eng.list_tools(self.tid)]
        self.assertIn("tdflow_capabilities", names)
        self.assertNotIn("tdflow_set_parameters", names)
        r = self.eng.call(self.tid, "tdflow_capabilities", {})["structuredContent"]
        self.assertEqual(sorted(i["capability"] for i in r["your_capabilities"]["available"]),
                         sorted(C.BY_ID[x].title for x in ("identity", "sessions", "approval_status")))
        kinds = [e["kind"] for e in self.st.events(limit=5)]
        self.assertIn("outcome", kinds)


if __name__ == "__main__":
    unittest.main()
