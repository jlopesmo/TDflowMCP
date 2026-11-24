"""First-run readiness: four separate states and one context-aware status line (owner UX assignment)."""
import unittest

from tdflowmcp import readiness as R


def checks(**over):
    base = {i: "pass" for i in R.ENV_CHECKS}
    base.update({"fs.datadir": "unknown", "audit.integrity": "unknown", "net.bind": "unknown", "sec.selftest": "unknown"})
    base.update(over)
    return [{"id": k.replace("_", "."), "result": v, "evidence": "e"} for k, v in base.items()]


class ReadinessTests(unittest.TestCase):
    def test_nothing_run_yet(self):
        r = R.evaluate({"checks": []})
        self.assertEqual((r["stage"], r["environment"]["state"]), ("checking", "pending"))
        self.assertIn("Run Diagnostics", r["line"])
        self.assertEqual(R.system_line([]), "Not run yet")
        self.assertIn("1 fail", R.system_line(checks(**{"net.port": "fail"})))

    def test_environment_ready_but_storage_and_binding_not_yet_verified(self):
        c = checks()
        r = R.evaluate({"checks": c, "store_open": False})
        self.assertEqual(r["environment"]["state"], "ready")
        self.assertEqual(r["mcp"]["state"], "not_verified")                     # never "ready" before it is checked
        self.assertEqual(r["stage"], "env_ready")
        self.assertIn("Connect Agent", r["line"])
        self.assertEqual(R.system_line(c), "Ready · 6 pass · 4 later")

    def test_env_failure_names_the_check(self):
        r = R.evaluate({"checks": checks(**{"net.port": "fail"})})
        self.assertEqual(r["stage"], "env_not_ready")
        self.assertIn("port", r["line"])

    def test_the_states_progress_and_are_not_confused(self):
        c = checks(**{"fs.datadir": "pass", "audit.integrity": "pass"})
        f = {"checks": c, "store_open": True, "agents_enabled": 1, "client": "Claude Code", "now": 1000.0}
        self.assertEqual(R.evaluate(dict(f, handoff=True))["stage"], "agent_created")
        r = R.evaluate(f)
        self.assertEqual((r["stage"], r["mcp"]["state"], r["mcp_enabled"]), ("agent_configured", "ready", False))
        r = R.evaluate(dict(f, mcp_on=True))
        self.assertEqual((r["stage"], r["agent_connected"]), ("enabled_waiting", False))   # registered is not connected
        self.assertIn("Waiting for Claude Code", r["line"])
        stale = [{"agent": "A", "client": "Claude Code", "last": 1000.0 - R.CONNECTED_WITHIN_S - 5}]
        self.assertFalse(R.evaluate(dict(f, mcp_on=True, connected=stale))["agent_connected"])
        live = [{"agent": "A", "client": "Claude Code", "last": 990.0}]
        r = R.evaluate(dict(f, mcp_on=True, connected=live))
        self.assertEqual((r["stage"], r["line"]), ("connected", "Claude Code connected. Ready."))

    def test_errors_and_suspension_win(self):
        f = {"checks": checks(), "mcp_on": True, "connected": [{"agent": "A", "last": 5}], "now": 6}
        self.assertEqual(R.evaluate(dict(f, suspended=True))["stage"], "suspended")
        self.assertEqual(R.evaluate(dict(f, error_code="PORT_IN_USE"))["stage"], "error")

    def test_report_marks_unverified_and_lists_fixes(self):
        c = checks(**{"net.port": "fail"})
        c[4]["remediation_id"] = "net.port.busy"
        txt = R.report(c, {"net.port.busy": {"steps": ["Pick another port."]}}, ["Claude Code"])
        self.assertIn("[FAIL] port", txt)
        self.assertIn("- Pick another port.", txt)
        self.assertIn("NOT YET VERIFIED", txt)
        self.assertIn("Claude Code", txt)


if __name__ == "__main__":
    unittest.main()
