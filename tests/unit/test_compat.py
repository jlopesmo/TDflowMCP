import json
import unittest
from pathlib import Path

from tests.unit import SRC
from tdflowmcp import compat
from tdflowmcp.net.bindverify import capability_check, verdict

TROUBLE = json.loads((SRC / "setup" / "troubleshooting.json").read_text(encoding="utf-8"))
# Review 3, H1: no automatic listener restart after a failed bind; only these internal actions exist.
INTERNAL_ACTIONS = {"rerun_checks", "propose_port"}

# Facts observed in spike S5 on TouchDesigner 2025.32440 (docs/research/2026-11-22-s5-compatibility-2025.32440.md)
S5_FACTS = {
    "td_build": "2025.32440",
    "python_version": (3, 11, 10),
    "modules": {"json": True, "sqlite3": True, "ssl": True, "hmac": True, "hashlib": True, "secrets": True,
                "jsonschema": "4.23.0", "http.server": True, "socketserver": True, "threading": True,
                "queue": True, "socket": True},
    "transport": "webserver-dat",
    "webserver_par_names": ["pageindex", "active", "restart", "port", "callbacks", "secure", "privatekey",
                            "certificate", "password", "verifyclient", "minprotocol"],
}


# Facts observed in spike S6 on the same build with the embedded transport
# (docs/research/2026-11-22-s6-embedded-python-transport.md).
S6_FACTS = dict(S5_FACTS, transport="python-http", bind_host="127.0.0.1", exclusive_bind=True)
S6_FACTS.pop("webserver_par_names")


class S5ReplayTests(unittest.TestCase):
    def test_s5_facts_classification(self):
        by = {r.id: r for r in compat.run_checks(S5_FACTS)}
        self.assertEqual(by["td.build"].result, "pass")          # 2025.32440 is supported (owner D10)
        self.assertEqual(by["py.version"].result, "pass")
        self.assertEqual(by["py.deps"].result, "pass")
        self.assertEqual(by["net.capability"].result, "fail")
        for unknown in ("net.bind", "net.port", "fs.datadir", "td.instances", "agent.clients"):
            self.assertEqual(by[unknown].result, "unknown", unknown)

    def test_s5_build_never_listens_and_never_enables_mcp(self):
        results = compat.run_checks(S5_FACTS)
        self.assertFalse(compat.listener_allowed(results))
        ok, blockers = compat.mcp_enable_allowed(results, has_enabled_agent=True, security_selftests_pass=True)
        self.assertFalse(ok)
        self.assertIn("net.capability", blockers)


class S6ReplayTests(unittest.TestCase):
    def test_embedded_transport_on_32440_may_listen(self):
        by = {r.id: r for r in compat.run_checks(S6_FACTS)}
        self.assertEqual(by["td.build"].result, "pass")
        self.assertEqual(by["net.capability"].result, "pass")
        self.assertEqual(by["net.bind"].result, "unknown")       # until the gate has verified the listener
        results = compat.run_checks(dict(S6_FACTS, port_listeners_other=[]))
        self.assertTrue(compat.listener_allowed(results))

    def test_embedded_transport_refuses_other_hosts_and_shared_ports(self):
        for host in ("0.0.0.0", "localhost", "::1", ""):
            r = compat.check_net_capability(dict(S6_FACTS, bind_host=host))
            self.assertEqual((r.result, r.remediation_id), ("fail", "net.capability.bind_host"), host)
        r = compat.check_net_capability(dict(S6_FACTS, exclusive_bind=False))
        self.assertEqual(r.remediation_id, "net.capability.no_exclusive_bind")
        self.assertEqual(compat.check_net_capability(dict(S6_FACTS, exclusive_bind=None)).result, "unknown")
        self.assertEqual(compat.check_net_capability(dict(S6_FACTS, transport=None)).result, "unknown")
        shared = compat.run_checks(dict(S6_FACTS, port_listeners_other=[("0.0.0.0", 9480, 77)]))
        self.assertFalse(compat.listener_allowed(shared))

    def test_older_than_oldest_tested_build_warns(self):
        self.assertEqual(compat.check_td_build({"td_build": "2023.12000"}).result, "warn")


class CheckRuleTests(unittest.TestCase):
    def test_missing_facts_are_unknown_never_pass(self):
        for r in compat.run_checks({}):
            self.assertEqual(r.result, "unknown", r.id)

    def test_target_build_passes_and_newer_tox_fails(self):
        self.assertEqual(compat.check_td_build({"td_build": "2025.33230"}).result, "pass")
        self.assertEqual(compat.check_td_build({"td_build": "2025.33230", "tox_saved_build": "2025.40000"}).result,
                         "fail")

    def test_python_mismatch_and_missing_module(self):
        self.assertEqual(compat.check_py_version({"python_version": (3, 9, 5)}).result, "fail")
        mods = dict(S5_FACTS["modules"], jsonschema=False)
        r = compat.check_py_deps({"modules": mods})
        self.assertEqual(r.result, "fail")
        self.assertIn("jsonschema", r.evidence)

    def test_full_pass_path_allows_listener_and_mcp_only_with_selftests_and_agent(self):
        facts = {
            "td_build": "2025.33230", "python_version": (3, 11, 15), "modules": S5_FACTS["modules"],
            "transport": "webserver-dat",
            "webserver_par_names": S5_FACTS["webserver_par_names"] + ["localaddress"],
            "bind_evidence": verdict(["127.0.0.1"], []), "port_listeners_other": [],
            "datadir_writable": True, "instances": 1,
            "clients": {"claude-code": {"selected": True, "support": "primary"}},
        }
        results = compat.run_checks(facts)
        self.assertTrue(all(r.result == "pass" for r in results), [(r.id, r.result) for r in results])
        self.assertTrue(compat.listener_allowed(results))
        self.assertEqual(compat.mcp_enable_allowed(results, True, True), (True, []))
        self.assertFalse(compat.mcp_enable_allowed(results, False, True)[0])
        self.assertFalse(compat.mcp_enable_allowed(results, True, False)[0])

    def test_bind_fail_and_port_conflict(self):
        self.assertEqual(compat.check_net_bind({"bind_evidence": verdict(["0.0.0.0"], [])}).result, "fail")
        r = compat.check_net_port({"port": 9480, "port_listeners_other": [("127.0.0.1", 9480, 4)],
                                   "port_probe_tdflowmcp": True})
        self.assertEqual(r.result, "fail")
        self.assertIn("unverified", r.evidence)

    def test_warn_or_fail_requires_remediation(self):
        with self.assertRaises(ValueError):
            compat.CheckResult("x", "fail", "no remediation")
        with self.assertRaises(ValueError):
            compat.CheckResult("x", "maybe", "bad result")


class FactContractTests(unittest.TestCase):
    """Review 3, M10/L: malformed or None facts are 'unknown', never 'pass'."""

    def test_none_and_malformed_facts_are_unknown(self):
        bad = {"transport": "python-http", "bind_host": None, "port_listeners_other": None, "datadir_writable": "yes", "instances": 0,
               "python_version": "three.eleven", "modules": "all", "clients": [], "bind_evidence": "pass",
               "webserver_par_names": None, "td_build": "2025"}
        for r in compat.run_checks(bad):
            self.assertEqual(r.result, "unknown", (r.id, r.evidence))

    def test_spike_style_module_facts(self):
        mods = {m: {"ok": True, "version": ""} for m in compat.REQUIRED_MODULES}
        mods["jsonschema"] = {"ok": True, "version": "4.23.0"}
        r = compat.check_py_deps({"modules": mods})
        self.assertEqual(r.result, "pass")
        self.assertIn("4.23.0", r.evidence)
        mods["jsonschema"] = {"ok": False, "error": "ModuleNotFoundError"}
        self.assertEqual(compat.check_py_deps({"modules": mods}).result, "fail")

    def test_empty_version_string_means_importable(self):
        mods = dict(S5_FACTS["modules"], sqlite3="")
        self.assertEqual(compat.check_py_deps({"modules": mods}).result, "pass")

    def test_python_version_string_accepted(self):
        self.assertEqual(compat.check_py_version({"python_version": "3.11.10"}).result, "pass")

    def test_instances_exactly_one(self):
        self.assertEqual(compat.check_instances({"instances": 1}).result, "pass")
        self.assertEqual(compat.check_instances({"instances": 2}).result, "warn")
        self.assertEqual(compat.check_instances({"instances": True}).result, "unknown")


class TroubleshootingDataTests(unittest.TestCase):
    def _all_remediation_ids(self):
        facts_variants = [
            S5_FACTS,
            dict(S6_FACTS, bind_host="0.0.0.0"),
            dict(S6_FACTS, exclusive_bind=False),
            {"td_build": "2020.10000"},
            {"td_build": "2025.33230", "tox_saved_build": "2026.1"},
            {"python_version": (3, 9, 0), "modules": {}},
            {"bind_evidence": verdict(["0.0.0.0"], []), "port_listeners_other": [1], "datadir_writable": False,
             "instances": 3, "clients": {"x": {"selected": True, "support": "documented"}}},
        ]
        ids = set()
        for f in facts_variants:
            ids |= {r.remediation_id for r in compat.run_checks(f) if r.remediation_id}
        return ids

    def test_every_warn_or_fail_has_an_entry(self):
        missing = self._all_remediation_ids() - set(TROUBLE["entries"])
        self.assertEqual(missing, set())

    def test_entries_only_allow_internal_reversible_actions(self):
        for rid, e in TROUBLE["entries"].items():
            self.assertTrue(set(e["allowed_actions"]) <= INTERNAL_ACTIONS, rid)
            for key in ("title", "cause", "confirm", "steps"):
                self.assertTrue(e.get(key), (rid, key))

    def test_entries_contain_no_install_or_config_commands(self):
        text = json.dumps(TROUBLE).lower()
        for bad in ("pip install", "winget", "choco ", "netsh", "set-netfirewall", "reg add", "claude mcp add",
                    "settings.local.json", "taskkill"):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
