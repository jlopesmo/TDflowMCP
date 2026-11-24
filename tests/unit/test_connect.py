"""Consent-based client connection (owner D22, ADR-0015, review 19): discovery, the Claude Code configuration plan,
apply and undo, the credential vault and the mutually authenticated headersHelper. Simulated configuration tests:
they never touch a real client's files (temporary directories only)."""
import hashlib
import hmac
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tdflowmcp import connect as C  # noqa: E402

TOKEN = "tdf_ABCDEFGHIJKL." + "s" * 43
PID = "prj_0123456789abcdef"
KEY = "ab" * 32


def env_for(home, platform="win32", which=None, environ=None):
    return C.Env(platform=platform, home=home, environ=environ or {}, which=which or (lambda n: None))


class DiscoveryTests(unittest.TestCase):
    def test_reads_names_only_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".vscode", "extensions", "anthropic.claude-code-2.1.330-win32-x64"))
            os.makedirs(os.path.join(h, ".vscode", "extensions", "openai.chatgpt-26.1.0"))
            asked = []
            e = env_for(h, which=lambda n: asked.append(n) or ("x" if n in ("claude", "code") else None))
            rows = {r["client"]: r for r in C.discover(e)}
            self.assertEqual(rows["claude-code"]["interfaces"], ["CLI", "VS Code extension"])
            self.assertTrue(rows["claude-code"]["automatic"])
            self.assertFalse(rows["codex"]["automatic"])
            self.assertNotIn("cursor", rows)
            self.assertNotIn("vscode-copilot", rows)             # VS Code on PATH is not Copilot (usability review)
            self.assertNotIn(h, json.dumps(rows))            # no paths reported

    def test_copilot_only_from_its_extension_folder(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".vscode", "extensions", "github.copilot-chat-0.30.0"))
            self.assertEqual([r["client"] for r in C.discover(env_for(h))], ["vscode-copilot"])

    def test_linux_is_not_automatic(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".claude"))
            rows = C.discover(env_for(h, platform="linux"))
            self.assertEqual(rows[0]["client"], "claude-code")
            self.assertFalse(rows[0]["automatic"])

    def test_nothing_found(self):
        with tempfile.TemporaryDirectory() as h:
            self.assertEqual(C.discover(env_for(h)), [])


class HelperTests(unittest.TestCase):
    def test_windows_helper_has_no_secret_and_cmd_safe(self):
        cmd = C.helper_command("win32", 9480, "claude-code", r"D:\Profiles\x\TDflowMCP\connect")
        self.assertIn("TDflowMCP", cmd)
        self.assertNotIn("tdf_A", cmd)
        inner = cmd.split('-Command "', 1)[1]
        self.assertTrue(inner.endswith('"'))
        self.assertNotIn('"', inner[:-1])                    # cmd.exe: one quoted argument
        self.assertNotIn("%", cmd.replace("%SystemRoot%", ""))
        self.assertIn("tdflowmcp-proof/", cmd)               # review 19 (1): the listener must prove the key
        self.assertIn("[Console]::Out.Write", cmd)           # review 19 (2): not through the PowerShell pipeline
        self.assertNotIn("}'}catch{'{}'}", cmd)
        self.assertIn("$env:PSModulePath=$PSHOME", cmd)      # review 19 (3)
        self.assertTrue(C.is_ours(C.claude_entry("win32", 9480, "claude-code", r"C:\v\connect"),
                                  C.claude_entry("win32", 9481, "claude-code", r"C:\v\connect")))

    def test_refuses_unsafe_paths_and_platforms(self):
        for bad in (r"C:\a%PATH%\b", 'C:\\a"b', "relative\\dir", "C:\\O\u2019Brien\\x", "C:\\a!b", "C:\\a^b"):
            with self.assertRaises(C.ConnectError, msg=bad):
                C.helper_command("win32", 9480, "claude-code", bad)
        C.helper_command("win32", 9480, "claude-code", "C:\\O'Brien\\x")      # an ASCII apostrophe is quoted
        with self.assertRaises(C.ConnectError):
            C.helper_command("linux", 9480, "claude-code", "/x")
        with self.assertRaises(C.ConnectError):
            C.helper_command("win32", 9480, "../x", r"C:\v")

    def test_macos_helper_shape(self):
        cmd = C.helper_command("darwin", 9480, "claude-code", "/opt/x/TDflowMCP/connect")
        self.assertIn("/usr/bin/security find-generic-password -s TDflowMCP", cmd)
        self.assertIn("http://127.0.0.1:9480/.well-known", cmd)
        self.assertIn("tdflowmcp-proof/$n", cmd)
        self.assertIn('K="$k"', cmd)                         # the key goes to perl through its environment
        self.assertIn("--noproxy 127.0.0.1", cmd)
        self.assertNotIn("tdf_A", cmd)

    def test_proof_is_hmac_and_validates_input(self):
        n = "0" * 32
        want = hmac.new(bytes.fromhex(KEY), f"tdflowmcp-proof|{PID}|9480|{n}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(C.proof(KEY, PID, 9480, n), want)
        for bad in ((KEY, PID, 9480, "x" * 32), ("zz", PID, 9480, n), (KEY, "prj_x", 9480, n)):
            with self.assertRaises(C.ConnectError):
                C.proof(*bad)

    def test_ownership_is_strict(self):
        """Review 19 (8): a user's own helper that mentions TDflowMCP is not ours."""
        mine = C.claude_entry("win32", 9480, "claude-code", r"C:\v\connect")
        users = {"type": "http", "url": "http://127.0.0.1:9480/mcp",
                 "headersHelper": r"powershell -File C:\tools\TDflowMCP\token.ps1"}
        self.assertFalse(C.is_ours(users, mine))
        self.assertFalse(C.is_ours(dict(mine, headers={"a": "b"}), mine))
        self.assertTrue(C.is_ours(mine, mine))

    def test_safe_url_drops_query_and_user_info(self):
        userinfo = "u:pw" + chr(64)                  # built at runtime (the doc checker's e-mail pattern)
        self.assertEqual(C._safe_url("https://" + userinfo + "host.example/mcp?api_key=SECRET"),
                         "https://host.example/mcp")


class PlanApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = self.tmp.name
        self.env = env_for(self.h)
        self.path = C.claude_config_path(self.env)
        self.bk = os.path.join(self.h, "private", "backups")
        self.entry = C.claude_entry("win32", 9480, "claude-code", os.path.join(self.h, "private", "connect"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, obj, nl=False):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(obj, indent=2, ensure_ascii=False) + ("\n" if nl else ""))

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_create_file(self):
        p = C.plan_claude(self.path, self.entry, self.env)
        self.assertEqual((p.action, p.shown_path), ("create_file", "~/.claude.json"))
        C.apply_claude(p, self.env, self.bk)
        self.assertEqual(self.read(), {"mcpServers": {"tdflow": self.entry}})

    def test_undo_drops_a_key_the_connection_created(self):
        """Seen on a copy of a real Claude Code configuration: it had no mcpServers key. Add then undo must leave the
        file byte-identical."""
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"numStartups": 3, "projects": {}}, indent=2))
        with open(self.path, encoding="utf-8") as f:
            before = f.read()
        r = C.apply_claude(C.plan_claude(self.path, self.entry, self.env), self.env, self.bk)
        self.assertTrue(r["created_key"])
        C.remove_claude(self.path, self.env, self.bk, self.entry, drop_empty_key=r["created_key"])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_add_preserves_everything_else(self):
        cfg = {"numStartups": 7, "tipsHistory": {"a": 1}, "mcpServers": {"blender": {"command": "uvx", "args": ["x"]},
               "zeta": {"type": "http", "url": "https://z", "headers": {"Authorization": "Bearer other"}}},
               "projects": {"F:/p": {"mcpServers": {"local": {"command": "a"}}}}, "name": "José", "f": 1.5}
        self.write(cfg)
        p = C.plan_claude(self.path, self.entry, self.env)
        self.assertEqual(p.action, "add")
        self.assertEqual(p.preserved, ["blender", "zeta"])
        self.assertTrue(p.format_preserved)
        r = C.apply_claude(p, self.env, self.bk)
        want = json.loads(json.dumps(cfg))
        want["mcpServers"]["tdflow"] = self.entry
        self.assertEqual(self.read(), want)
        self.assertTrue(os.path.isfile(os.path.join(self.bk, r["backup"])))
        u = C.remove_claude(self.path, self.env, self.bk, self.entry)
        self.assertEqual(u["action"], "removed")
        self.assertEqual(self.read(), json.loads(json.dumps(cfg)))

    def test_unchanged_and_update(self):
        self.write({"mcpServers": {"tdflow": self.entry}})
        self.assertEqual(C.plan_claude(self.path, self.entry, self.env).action, "unchanged")
        e2 = C.claude_entry("win32", 9481, "claude-code", os.path.join(self.h, "private", "connect"))
        p = C.plan_claude(self.path, e2, self.env)
        self.assertEqual(p.action, "update")
        C.apply_claude(p, self.env, self.bk)
        self.assertEqual(self.read()["mcpServers"]["tdflow"], e2)

    def test_replace_foreign_entry_then_restore_it_from_the_record(self):
        mine = {"type": "http", "url": "http://127.0.0.1:9480/mcp?k=SECRET", "headers": {"Authorization": "Bearer x"}}
        self.write({"mcpServers": {"tdflow": mine, "other": {"command": "a"}}})
        p = C.plan_claude(self.path, self.entry, self.env)
        self.assertEqual(p.action, "replace")
        self.assertEqual(p.previous_url, "http://127.0.0.1:9480/mcp")              # no query shown
        self.assertIn("REPLACES", C.consent_text("Claude Code", ["CLI"], p, "Builder", "/project1/**", True))
        r = C.apply_claude(p, self.env, self.bk)
        self.assertEqual(r["previous_entry"], mine)
        for n in os.listdir(self.bk):                                               # backups pruned meanwhile
            os.remove(os.path.join(self.bk, n))
        u = C.remove_claude(self.path, self.env, self.bk, self.entry, restore_entry=r["previous_entry"])
        self.assertEqual(u["action"], "restored")
        self.assertEqual(self.read()["mcpServers"]["tdflow"], mine)

    def test_concurrent_rewrite_with_same_action_proceeds(self):
        self.write({"mcpServers": {"a": {"command": "x"}}, "n": 1})
        p = C.plan_claude(self.path, self.entry, self.env)
        self.write({"mcpServers": {"a": {"command": "x"}}, "n": 2})       # the client rewrote its file
        C.apply_claude(p, self.env, self.bk)
        got = self.read()
        self.assertEqual(got["n"], 2)                     # the client's newer data is kept
        self.assertEqual(got["mcpServers"]["tdflow"], self.entry)

    def test_concurrent_change_of_the_entry_refuses(self):
        self.write({"mcpServers": {}})
        p = C.plan_claude(self.path, self.entry, self.env)
        self.write({"mcpServers": {"tdflow": {"command": "someone-else"}}})
        with self.assertRaises(C.ConnectError) as cm:
            C.apply_claude(p, self.env, self.bk)
        self.assertEqual(cm.exception.code, "CONFIG_CHANGED")
        self.assertEqual(self.read()["mcpServers"]["tdflow"], {"command": "someone-else"})

    def test_write_is_refused_if_the_file_changes_at_the_last_moment(self):
        """Review 19 (11): the file is re-checked right before it is replaced."""
        self.write({"a": 1})
        with self.assertRaises(C.ConnectError):
            C._atomic_write(self.path, b"{}", expect_sha="0" * 64)
        self.assertEqual(self.read(), {"a": 1})
        self.assertEqual([n for n in os.listdir(self.h) if n.endswith(".tmp")], [])      # temp file removed

    def test_lone_surrogates_fall_back_to_ascii_escapes(self):
        """Review 19 (10): Node's JSON.stringify can write a split emoji."""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"history": "abc\\ud83d", "mcpServers": {}}')
        r = C.apply_claude(C.plan_claude(self.path, self.entry, self.env), self.env, self.bk)
        self.assertEqual(r["action"], "add")
        self.assertEqual(self.read()["history"], "abc\ud83d")

    def test_invalid_config_is_never_touched(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        with self.assertRaises(C.ConnectError):
            C.plan_claude(self.path, self.entry, self.env)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ not json")

    def test_remove_leaves_a_user_edited_entry(self):
        edited = dict(self.entry, headers={"x": "y"})
        self.write({"mcpServers": {"tdflow": edited}})
        self.assertEqual(C.remove_claude(self.path, self.env, self.bk, self.entry)["action"], "not_ours")
        self.assertEqual(self.read()["mcpServers"]["tdflow"], edited)

    def test_linked_config_is_refused(self):
        real = os.path.join(self.h, "dotfiles.json")
        with open(real, "w", encoding="utf-8") as f:
            f.write("{}")
        try:
            os.symlink(real, self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links need extra privileges here")
        with self.assertRaises(C.ConnectError) as cm:
            C.plan_claude(self.path, self.entry, self.env)
        self.assertEqual(cm.exception.code, "CONFIG_IS_LINK")

    def test_backups_are_bounded(self):
        self.write({"mcpServers": {}})
        for i in range(C.KEEP_BACKUPS + 3):
            C._backup(b"{}" + bytes([65 + i]), self.bk, "claude.json", 1_700_000_000 + i)
        self.assertEqual(len(os.listdir(self.bk)), C.KEEP_BACKUPS)

    def test_claude_config_dir_is_respected(self):
        e = env_for(self.h, environ={"CLAUDE_CONFIG_DIR": os.path.join(self.h, "cc")})
        self.assertEqual(C.claude_config_path(e), os.path.join(self.h, "cc", ".claude.json"))
        self.assertEqual(C.display_path(C.claude_config_path(e), e), "%CLAUDE_CONFIG_DIR%/.claude.json")
        self.assertEqual(C.Env.current(self.h).home, self.h)


class VaultTests(unittest.TestCase):
    def test_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            v = C.Vault("win32", d, dpapi=lambda b: b)
            for slot, pid, tok, key in (("claude-code", PID, "nope", KEY), ("../x", PID, TOKEN, KEY),
                                        ("claude-code", "x/..", TOKEN, KEY), ("claude-code", PID, TOKEN, "zz")):
                with self.assertRaises(C.ConnectError):
                    v.put(slot, pid, tok, key)
            self.assertFalse(C.Vault("linux", d).supported)

    def test_macos_secret_goes_through_stdin_only_and_is_verified(self):
        calls = []
        v = C.Vault("darwin", "/x", runner=lambda argv, stdin: calls.append((argv, stdin)) or 0)
        v.put("claude-code", PID, TOKEN, KEY)
        argv, stdin = calls[0]
        self.assertEqual(argv, ["/usr/bin/security", "-i"])
        self.assertIn(TOKEN + "|" + KEY, stdin)
        self.assertEqual(calls[1][0][:2], ["/usr/bin/security", "find-generic-password"])     # read back (review 19)
        self.assertTrue(v.delete("claude-code", PID))
        self.assertTrue(all(TOKEN not in " ".join(a) for a, _ in calls))
        refused = C.Vault("darwin", "/x", runner=lambda argv, stdin: 0 if argv[1] == "-i" else 44)
        with self.assertRaises(C.ConnectError):
            refused.put("claude-code", PID, TOKEN, KEY)

    def test_others_counts_other_projects(self):
        with tempfile.TemporaryDirectory() as d:
            v = C.Vault("win32", d, dpapi=lambda b: b)
            v.put("claude-code", PID, TOKEN, KEY)
            self.assertEqual(v.others("claude-code", PID), 0)
            v.put("claude-code", "prj_ffffffffffffffff", TOKEN, KEY)
            self.assertEqual(v.others("claude-code", PID), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_windows_dpapi_file_is_not_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            v = C.Vault("win32", d)
            v.put("claude-code", PID, TOKEN, KEY)
            with open(os.path.join(d, "claude-code", PID + ".dpapi"), "rb") as f:
                data = f.read()
            for secret in (TOKEN, KEY):
                self.assertNotIn(secret.encode(), data)
                self.assertNotIn(secret.encode("utf-16-le"), data)
            self.assertTrue(v.has("claude-code", PID))
            self.assertTrue(v.delete("claude-code", PID))
            self.assertFalse(v.has("claude-code", PID))


class _Listener(http.server.BaseHTTPRequestHandler):
    """A stand-in for the listener: ``connect`` id, and the proof computed with ``key`` (None: a squatter)."""
    body, key, pid, port, bad_proof = b"{}", None, PID, 0, False

    def do_GET(self):
        cls = type(self)
        if self.path == "/.well-known/tdflowmcp":
            code, out = 200, cls.body
        elif self.path.startswith(C.PROOF_PREFIX) and cls.key:
            pr = C.proof(cls.key, cls.pid, cls.port, self.path[len(C.PROOF_PREFIX):])
            code, out = 200, json.dumps({"proof": ("0" * 64) if cls.bad_proof else pr}).encode()
        else:
            code, out = 404, b"{}"
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@unittest.skipUnless(sys.platform == "win32", "runs the real Windows helper through cmd.exe")
class WindowsHelperEndToEnd(unittest.TestCase):
    """The exact helper command, run as Claude Code runs it on Windows (``cmd.exe /d /s /c "<command>"``), against a
    loopback stand-in for the listener and a real DPAPI vault."""

    def run_helper(self, body, key=KEY, bad_proof=False, env=None):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Listener)
        _Listener.body, _Listener.key, _Listener.bad_proof = body, key, bad_proof
        _Listener.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                C.Vault("win32", d).put("claude-code", PID, TOKEN, KEY)
                cmd = C.helper_command("win32", srv.server_address[1], "claude-code", d)
                r = subprocess.run('cmd.exe /d /s /c "' + cmd + '"', capture_output=True, text=True, timeout=60,
                                   cwd=d, env=env)
                return json.loads(r.stdout.strip() or "null")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_right_project_with_proof_gets_its_token(self):
        self.assertEqual(self.run_helper(json.dumps({"product": "TDflowMCP", "connect": PID}).encode()),
                         {"Authorization": "Bearer " + TOKEN})

    def test_a_squatter_gets_nothing(self):
        """Review 19 (1): a listener that names the project but cannot prove the key gets no token."""
        body = json.dumps({"connect": PID}).encode()
        self.assertEqual(self.run_helper(body, key=None), {})
        self.assertEqual(self.run_helper(body, bad_proof=True), {})
        self.assertEqual(self.run_helper(body, key="cd" * 32), {})

    def test_other_project_or_bad_answer_gets_nothing(self):
        for body in (json.dumps({"connect": "prj_ffffffffffffffff"}).encode(), b"{}",
                     json.dumps({"connect": "..\\..\\x"}).encode(), b"not json"):
            self.assertEqual(self.run_helper(body), {})

    def test_works_when_started_from_powershell_7(self):
        """Review 19 (3): Claude Code started from a PowerShell 7 terminal passes PS7's PSModulePath on."""
        env = dict(os.environ, PSModulePath=os.pathsep.join([r"C:\Program Files\PowerShell\7\Modules",
                                                             os.environ.get("PSModulePath", "")]))
        self.assertEqual(self.run_helper(json.dumps({"connect": PID}).encode(), env=env),
                         {"Authorization": "Bearer " + TOKEN})


if __name__ == "__main__":
    unittest.main()
