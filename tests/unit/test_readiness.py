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
        self.assertEqual(R.system_line(c), "Ready · 6 ok · 4 pending")
        self.assertEqual(R.counts(c), {"pass": 6, "warn": 0, "fail": 0, "unknown": 4})   # pending never passed

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
        self.assertEqual(r["line"], "Waiting for the agent…")
        stale = [{"agent": "A", "client": "Claude Code", "last": 1000.0 - R.CONNECTED_WITHIN_S - 5}]
        self.assertFalse(R.evaluate(dict(f, mcp_on=True, connected=stale))["agent_connected"])
        live = [{"agent": "A", "client": "Claude Code", "last": 990.0}]
        r = R.evaluate(dict(f, mcp_on=True, connected=live))
        self.assertEqual((r["stage"], r["line"]), ("connected", "Agent Connected — Ready"))

    def test_connect_stages_show_until_a_real_request(self):
        c = checks(**{"fs.datadir": "pass", "audit.integrity": "pass"})
        f = {"checks": c, "store_open": True, "agents_enabled": 1, "now": 1000.0, "mcp_on": True,
             "connect_line": "Start a Claude Code chat."}
        r = R.evaluate(f)
        self.assertEqual((r["stage"], r["agent_connected"]), ("connecting", False))   # configured is not connected
        r = R.evaluate(dict(f, connected=[{"agent": "A", "client": "Claude Code", "last": 999.0}]))
        self.assertEqual((r["stage"], r["line"]), ("connected", "Agent Connected — Ready"))
        self.assertLessEqual(len(r["line"]), R.MAX_LINE)

    def test_a_reopened_project_is_not_reported_as_empty(self):
        """rc4 (UX-09, the owner's rc3 restart finding): storage stays closed after reopening, but existing agents are
        'not loaded', never 'no agents'; Next Step says to turn on Enable MCP, not to connect a new agent."""
        c = checks()
        r = R.evaluate({"checks": c, "store_open": False, "agents_enabled": 0, "not_loaded": True})
        self.assertEqual((r["stage"], r["line"]), ("set_up_off", "Turn on Enable MCP."))
        r = R.evaluate({"checks": c, "store_open": False, "agents_enabled": 0})
        self.assertEqual(r["line"], "Ready: pulse Connect Agent")             # a project never connected
        r = R.evaluate({"checks": c, "not_loaded": True, "suspended": True})
        self.assertEqual(r["stage"], "suspended")                              # the kill switch still wins
        r = R.evaluate({"checks": c, "store_open": False, "storage_needs_user": True, "not_loaded": False})
        self.assertEqual((r["stage"], r["line"]), ("storage_confirm", "Data: confirm (Advanced)"))   # review 20 (2)

    def test_every_status_line_fits_the_default_parameter_window(self):
        """UX-02 / D22 visual review: the default 416-px parameter window shows about 26 characters of a value."""
        import sys
        from tests.unit import SRC
        sys.path.insert(0, str(SRC))
        c = checks(**{"fs.datadir": "pass", "audit.integrity": "pass"})
        facts = [{"checks": []}, {"checks": c, "error_code": "PORT_IN_USE"}, {"checks": c, "suspended": True},
                 {"checks": checks(**{"net.port": "fail", "td.build": "fail"})},
                 {"checks": c, "agents_enabled": 0}, {"checks": c, "agents_enabled": 1, "handoff": True},
                 {"checks": c, "agents_enabled": 1}, {"checks": c, "agents_enabled": 1, "mcp_on": True},
                 {"checks": c, "agents_enabled": 1, "mcp_on": True, "handoff": True},
                 {"checks": c, "mcp_on": True, "now": 10, "connected": [{"agent": "A", "last": 9}] * 3}]
        for f in facts:
            line = R.evaluate(f)["line"]
            self.assertLessEqual(len(line), R.MAX_LINE, line)
        for cs in (c, checks(**{"net.port": "fail", "td.build": "warn"}), []):
            self.assertLessEqual(len(R.system_line(cs)), R.MAX_LINE, R.system_line(cs))
        src = (SRC / "td_adapter" / "connection.py").read_text(encoding="utf-8")
        ns = {}
        start = src.index("STAGE_LINES = {")
        end = src.index("NO_REQUEST_AFTER_S")
        exec(src[start:end], ns)                                   # the literal tables only
        for k, v in list(ns["STAGE_LINES"].items()) + list(ns["FAIL_LINES"].items()):
            for client, who in (("Claude Code", "Claude"), ("Codex", "Codex")):       # every automatic client
                line = v.format(client=client, minutes=59, who=who)
                self.assertLessEqual(len(line), R.MAX_LINE, (k, line))
        for cid, table in ns["STAGE_LINES_FOR"].items():           # ADR-0017: lines that differ per client
            for k, v in table.items():
                self.assertIn(k, ns["STAGE_LINES"])
                self.assertLessEqual(len(v), R.MAX_LINE, (cid, k, v))
        self.assertNotIn("/mcp", " ".join(ns["STAGE_LINES_FOR"]["codex"].values()))     # a Claude Code command

    def test_installed_agents_line_fits_with_any_mix_of_agents(self):
        """ADR-0017 (usability review): with Codex automatic, 'Claude Code, Codex (auto) +2 more' no longer fitted."""
        import ast
        from tests.unit import SRC
        src = (SRC / "td_adapter" / "connection.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "installed_text")
        ns = {}
        exec(compile(ast.Module([fn], []), "installed_text", "exec"), ns)       # a pure function; no `td` needed
        text = ns["installed_text"]

        def rows(auto=(), manual=(), old=()):
            return ([{"client": c, "automatic": True} for c in auto] + [{"client": c, "automatic": False} for c in manual]
                    + [{"client": c, "automatic": False, "manual_why": "x"} for c in old])
        cases = {
            "Auto: Claude Code, Codex": rows(("claude-code", "codex")),
            "Auto: Claude, Codex +2": rows(("claude-code", "codex"), ("cursor", "vscode-copilot")),
            "Auto: Claude Code +1": rows(("claude-code",), ("cursor",)),
            "Auto: Codex": rows(("codex",)),
            "Manual: Cursor, Copilot": rows(manual=("cursor", "vscode-copilot")),
            "Manual: Codex (too old)": rows(old=("codex",)),
            "Auto: Claude Code +1": rows(("claude-code",), old=("codex",)),
        }
        for want, r in cases.items():
            self.assertEqual(text(r), want)
        import itertools
        ids = ("claude-code", "codex", "cursor", "vscode-copilot")
        for n in range(1, 5):
            for combo in itertools.combinations(ids, n):
                for k in range(len(combo) + 1):
                    for old in ((), ("codex",)):
                        r = rows(combo[:k], [c for c in combo[k:] if c not in old], [c for c in old if c in combo[k:]])
                        self.assertLessEqual(len(text(r)), R.MAX_LINE, text(r))

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
