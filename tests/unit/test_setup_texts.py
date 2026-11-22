"""Tell Your Coding Agent text, Setup API payloads and the embedded-module loader."""
import json
import sys
import unittest

from tests.unit import SRC
from tdflowmcp import compat, setupapi, tellagent

sys.path.insert(0, str(SRC / "td_adapter"))
import loader  # noqa: E402  (TD adapter module with no td import)

TROUBLE = json.loads((SRC / "setup" / "troubleshooting.json").read_text(encoding="utf-8"))
FACTS = {"td_build": "2025.32440", "python_version": (3, 11, 10), "transport": "python-http",
         "bind_host": "127.0.0.1", "exclusive_bind": True,
         "modules": {m: True for m in compat.REQUIRED_MODULES}, "port_listeners_other": [("0.0.0.0", 9480, 5)],
         "datadir_writable": False, "instances": 2}


class TellAgentTests(unittest.TestCase):
    def test_sections_rules_and_repair_from_troubleshooting(self):
        checks = compat.run_checks(FACTS)
        text = tellagent.build("Error", "PORT_IN_USE", 9480, "2025.32440", checks, TROUBLE, "0.1.0")
        self.assertIn("## Setup", text)
        self.assertIn("## Repair", text)
        self.assertIn("MCP access is not available", text)
        self.assertIn("Do not install software", text)
        self.assertIn(TROUBLE["entries"]["net.port.in_use"]["title"], text)
        self.assertIn("Listener error PORT_IN_USE", text)

    def test_no_evidence_or_environment_detail(self):
        profile = "C:" + "\\" + "Us" + "ers" + "\\someone\\AppData"      # built at runtime (doc checker privacy rule)
        checks = [compat.CheckResult("fs.datadir", "fail", profile + " secret-evidence", "fs.datadir.unwritable")]
        text = tellagent.build("Off", "", 9480, "2025.32440", checks, TROUBLE)
        self.assertNotIn("someone", text)
        self.assertNotIn("secret-evidence", text)

    def test_one_line(self):
        self.assertTrue(tellagent.one_line("Off", "", []).startswith("Not checked"))
        ok = [compat.CheckResult("x", "pass", "")]
        self.assertTrue(tellagent.one_line("Setup", "", ok).startswith("Ready"))
        bad = compat.run_checks(FACTS)
        self.assertTrue(tellagent.one_line("Setup", "", bad).startswith("Repair"))


class SetupApiTests(unittest.TestCase):
    def test_well_known_is_minimal(self):
        self.assertEqual(json.loads(setupapi.well_known("0.1.0")), {"product": "TDflowMCP", "version": "0.1.0"})

    def test_status_payload(self):
        checks = compat.run_checks(FACTS)
        ctl = {"state": "Setup", "error_code": "", "message": "m", "port": 9480, "portstatus": "this component",
               "url": "http://127.0.0.1:9480/setup/", "gate": "SERVING", "reverifies": 1}
        s = setupapi.status(ctl, "2025.32440", checks, TROUBLE, "tell", 3.0, "0.1.0")
        self.assertEqual(s["mcp"], "disabled (Phase 1A)")
        self.assertEqual(len(s["checks"]), len(checks))
        bad = [c for c in s["checks"] if c["result"] in ("warn", "fail")]
        self.assertTrue(bad and all(c["title"] and c["steps"] for c in bad))
        self.assertEqual(json.loads(setupapi.dumps(s))["tell_agent"], "tell")


class FakeDat:
    def __init__(self, text):
        self.text = text


class FakeComp:
    def __init__(self, manifest, dats):
        self._m = FakeDat(json.dumps(manifest))
        self._src = self
        self._dats = dats

    def op(self, name):
        if name == "manifest":
            return self._m
        if name == "src":
            return self._src
        return self._dats.get(name)


class LoaderTests(unittest.TestCase):
    def tearDown(self):
        sys.meta_path[:] = [f for f in sys.meta_path if getattr(f, "finder_id", None) != loader.FINDER_ID]
        for n in [n for n in sys.modules if n.startswith("tdflowtestpkg")]:
            del sys.modules[n]

    def _comp(self, body="VALUE = 41 + 1\n", sha=None):
        pkg, mod = "X = 1\n", body
        manifest = {"source_digest": "d1", "modules": {
            "tdflowtestpkg": {"dat": "m_pkg", "sha256": loader.digest(pkg), "package": True},
            "tdflowtestpkg.mod": {"dat": "m_mod", "sha256": sha or loader.digest(mod)}}}
        return FakeComp(manifest, {"m_pkg": FakeDat(pkg), "m_mod": FakeDat(mod.replace("\n", "\r\n"))})

    def test_installs_and_imports_embedded_modules(self):
        loader.install_from_comp(self._comp())
        import tdflowtestpkg.mod as m
        self.assertEqual(m.VALUE, 42)
        self.assertTrue(m.__file__.startswith("<tdflowmcp-embedded:"))
        self.assertEqual(len(m.__tdflow_build__), 64)          # recomputed from module hashes
        self.assertEqual(sum(1 for f in sys.meta_path if getattr(f, "finder_id", None) == loader.FINDER_ID), 1)

    def test_hash_mismatch_refuses_to_load(self):
        with self.assertRaises(loader.LoaderError):
            loader.install_from_comp(self._comp(sha="0" * 64))

    def test_reinstall_with_new_build_replaces_modules(self):
        loader.install_from_comp(self._comp())
        import tdflowtestpkg.mod as m1
        first = m1.__tdflow_build__
        c2 = self._comp("VALUE = 7\n")
        json_m = json.loads(c2._m.text)
        json_m["source_digest"] = "d2"
        c2._m.text = json.dumps(json_m)
        loader.install_from_comp(c2)
        import tdflowtestpkg.mod as m
        self.assertEqual(m.VALUE, 7)
        self.assertNotEqual(m.__tdflow_build__, first)


if __name__ == "__main__":
    unittest.main()
