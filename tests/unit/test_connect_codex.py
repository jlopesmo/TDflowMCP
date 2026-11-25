"""One-click Codex connection (ADR-0017, Issue #3): discovery, the ``[mcp_servers.tdflow]`` table in
``config.toml`` (planned, applied and undone as text), ownership, fail-closed refusals, and the helper run the way
Codex runs it. Simulated configuration tests: temporary directories only, never a real Codex profile."""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tdflowmcp import connect as C  # noqa: E402

TOKEN = "tdf_ABCDEFGHIJKL." + "s" * 43
PID = "prj_0123456789abcdef"
KEY = "ab" * 32
VAULT = os.path.join(tempfile.gettempdir(), "someone", "TDflowMCP", "connect")      # absolute on every platform

# A configuration shaped like a real one: comments, quoted table names with backslashes, another MCP server, tables
# Codex itself writes, and text that only looks like a table header.
REAL = '''# my Codex settings
model = "gpt-5"   # keep
model_reasoning_effort = "high"
notes = """
[mcp_servers.tdflow]
url = "not a table: this is inside a string"
"""
matrix = [
  [1, 2],
  [3, 4]
]

[features]
web_search = true

[projects.'F:\\dev\\My Project']
trust_level = "trusted"

[mcp_servers.blender]
command = "uvx"
args = ["blender-mcp"]

[mcp_servers.docs]
url = "https://example.invalid/mcp"
bearer_token_env_var = "DOCS_TOKEN"

[apps.connector_1.tools."github.add_comment"]
approval_mode = "approve"
'''


def env_for(home, platform="win32", which=None, environ=None):
    return C.Env(platform=platform, home=home, environ=environ or {}, which=which or (lambda n: None))


def after_header(text, added):
    """Insert a line right after the real ``[mcp_servers.tdflow]`` header (the last one: REAL also has that text
    inside a string)."""
    head, sep, tail = text.rpartition("[mcp_servers.tdflow]\n")
    return head + sep + added + tail


class DiscoveryTests(unittest.TestCase):
    def rows(self, h, **kw):
        return {r["client"]: r for r in C.discover(env_for(h, **kw))}

    def test_cli_and_extension_share_one_codex_row(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".vscode", "extensions", "openai.chatgpt-26.1120.50957-win32-x64"))
            os.makedirs(os.path.join(h, ".cursor", "extensions", "openai.chatgpt-26.1120.50957-win32-x64"))
            r = self.rows(h, which=lambda n: "x" if n == "codex" else None)
            self.assertEqual(r["codex"]["interfaces"], ["CLI", "VS Code extension", "Cursor extension"])
            self.assertTrue(r["codex"]["automatic"])
            self.assertNotIn(h, json.dumps(r))                          # no paths reported
            self.assertNotIn("desktop", json.dumps(r).lower())          # no verified marker: not claimed

    def test_extension_alone_is_enough_when_it_is_recent(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".vscode", "extensions", "openai.chatgpt-26.1117.41037-win32-x64"))
            self.assertTrue(self.rows(h)["codex"]["automatic"])

    def test_an_old_extension_without_the_cli_stays_manual(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".vscode", "extensions", "openai.chatgpt-25.900.1"))
            r = self.rows(h)["codex"]
            self.assertFalse(r["automatic"])
            self.assertIn("older", r["manual_why"])

    def test_only_windows_is_automatic(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".codex"))
            self.assertEqual(self.rows(h)["codex"]["interfaces"], ["configuration folder"])
            self.assertTrue(self.rows(h)["codex"]["automatic"])
            for other in ("darwin", "linux"):
                self.assertFalse(self.rows(h, platform=other)["codex"]["automatic"])
            with self.assertRaises(C.ConnectError) as cm:
                C.codex_entry("darwin", 9480, "codex", "/x")
            self.assertEqual(cm.exception.code, "PLATFORM_UNSUPPORTED")

    def test_claude_code_is_found_independently(self):
        with tempfile.TemporaryDirectory() as h:
            os.makedirs(os.path.join(h, ".claude"))
            os.makedirs(os.path.join(h, ".codex"))
            self.assertEqual(sorted(self.rows(h)), ["claude-code", "codex"])


class EntryTests(unittest.TestCase):
    def test_entry_has_no_secret_and_is_safe_for_cmd(self):
        e = C.codex_entry("win32", 9480, "codex", VAULT)
        self.assertEqual(sorted(e), ["http_headers_helper", "url"])
        self.assertEqual(e["url"], "http://127.0.0.1:9480/mcp")
        cmd = e["http_headers_helper"]
        self.assertNotIn("tdf_A", cmd)                                # only the token's pattern, never a token
        inner = cmd.split('-Command "', 1)[1]
        self.assertTrue(inner.endswith('"'))
        self.assertNotIn('"', inner[:-1])                             # cmd.exe: one quoted argument
        self.assertNotIn("%", cmd.replace("%SystemRoot%", ""))
        self.assertIn("tdflowmcp-proof/", cmd)                        # the listener must prove the project key
        self.assertIn("catch{exit 1}", cmd)                           # fail closed: no output, status 1
        self.assertNotIn("Out.Write('{}')", cmd)                      # never "no headers": Codex would connect anyway
        self.assertIn(os.path.join(VAULT, "codex"), cmd)              # its own credential slot

    def test_the_claude_code_helper_did_not_change(self):
        """Existing Claude Code connections are recognised by exact equality: their command must stay as it was."""
        cmd = C.claude_entry("win32", 9480, "claude-code", VAULT)["headersHelper"]
        self.assertTrue(cmd.endswith("catch{[Console]::Out.Write('{}')} # TDflowMCP\""))
        # SHA-256 of the commands as released in v1.0.0 (computed from that source, 2026-11-25)
        mac = C.helper_command("darwin", 9480, "claude-code", "/opt/x/TDflowMCP/connect")
        self.assertEqual(hashlib.sha256(mac.encode()).hexdigest(),
                         "12343198d33273566c0423763eb0735064c24cd6b1ab33310368aecbd036b4d2")
        if sys.platform == "win32":
            win = C.helper_command("win32", 9480, "claude-code", r"C:\v\connect")
            self.assertEqual(hashlib.sha256(win.encode()).hexdigest(),
                             "66078dc9bbcf7cc2f6be676d23bdbc9318d0d5eca3d05c99c7128e85c58dbc3a")
        codex = C.helper_command("win32", 9480, "claude-code", VAULT, fail="exit")
        self.assertEqual(cmd.replace("catch{[Console]::Out.Write('{}')}", "catch{exit 1}"), codex)   # the only difference

    def test_toml_string_round_trips_quotes_and_backslashes(self):
        for vault in (VAULT, os.path.join(tempfile.gettempdir(), "O'Brien", "Tëst", "connect")):
            e = C.codex_entry("win32", 9480, "codex", vault)
            text = "".join(C._codex_block(e, "\n", "tdflow"))
            self.assertEqual(tomllib.loads(text)["mcp_servers"]["tdflow"], e)

    def test_ownership(self):
        mine = C.codex_entry("win32", 9480, "codex", VAULT)
        self.assertTrue(C.codex_is_ours(mine, mine))
        self.assertTrue(C.codex_is_ours(C.codex_entry("win32", 9555, "codex", VAULT), mine))      # port aside
        self.assertTrue(C.codex_is_ours(dict(mine, enabled=False, tool_timeout_sec=90,
                                             tools={"tdflow_whoami": {"approval_mode": "approve"}}), mine))
        users = {"url": "http://127.0.0.1:9480/mcp", "http_headers_helper": r"powershell -File C:\TDflowMCP\t.ps1"}
        self.assertFalse(C.codex_is_ours(users, mine))
        self.assertFalse(C.codex_is_ours(dict(mine, bearer_token_env_var="TDFLOW_TOKEN"), mine))  # it would win
        self.assertFalse(C.codex_is_ours(dict(mine, http_headers={"Authorization": "x"}), mine))
        self.assertFalse(C.codex_is_ours(dict(mine, environment_id="remote"), mine))
        # review 23 (7): an allowlist. A key this version does not know is never assumed to be harmless.
        for unknown in ("headers", "transport", "type", "experimental_bearer_token", "proxy", "command"):
            self.assertFalse(C.codex_is_ours(dict(mine, **{unknown: "x"}), mine), unknown)
        for known in C.CODEX_PREFERENCES:
            self.assertTrue(C.codex_is_ours(dict(mine, **{known: 1}), mine), known)
        self.assertFalse(C.codex_is_ours(dict(mine, url="http://127.0.0.1:9480/other"), mine))
        self.assertFalse(C.codex_is_ours(dict(mine, url="http://10.0.0.5:9480/mcp"), mine))
        self.assertFalse(C.codex_is_ours("x", mine))
        elsewhere = os.path.join(tempfile.gettempdir(), "elsewhere")       # absolute on every platform (CI: Ubuntu)
        self.assertFalse(C.codex_is_ours(C.codex_entry("win32", 9480, "codex", elsewhere), mine))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = self.tmp.name
        self.env = env_for(self.h)
        self.path = C.codex_config_path(self.env)
        os.makedirs(os.path.dirname(self.path))
        self.bk = os.path.join(self.h, "private", "backups")
        self.entry = C.codex_entry("win32", 9480, "codex", os.path.join(self.h, "private", "connect"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, raw=False):
        with open(self.path, "wb") as f:
            f.write(text if raw else text.encode("utf-8"))

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def parsed(self):
        return tomllib.loads(self.read().decode("utf-8-sig"))

    def apply(self, entry=None):
        p = C.plan_codex(self.path, entry or self.entry, self.env)
        return p, C.apply_codex(p, self.env, self.bk)

    def refused(self, code, fn=None):
        before = self.read()
        with self.assertRaises(C.ConnectError) as cm:
            (fn or (lambda: C.plan_codex(self.path, self.entry, self.env)))()
        self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.read(), before)                          # nothing was changed

    def test_create_file_then_undo_removes_it(self):
        p, r = self.apply()
        self.assertEqual((p.action, p.shown_path, r["created_file"]), ("create_file", "~/.codex/config.toml", True))
        self.assertEqual(self.parsed(), {"mcp_servers": {"tdflow": self.entry}})
        self.assertIn(b"Added by TDflowMCP", self.read())
        u = C.remove_codex(self.path, self.env, self.bk, self.entry, created_file=True)
        self.assertEqual(u["action"], "removed")
        self.assertFalse(os.path.exists(self.path))

    def test_a_created_file_is_kept_when_codex_wrote_more_into_it(self):
        self.apply()
        with open(self.path, "ab") as f:
            f.write(b'\n[features]\nx = true\n')
        C.remove_codex(self.path, self.env, self.bk, self.entry, created_file=True)
        self.assertEqual(self.parsed(), {"features": {"x": True}})

    def test_add_keeps_every_other_byte_and_undo_is_exact(self):
        for label, text in (("lf", REAL), ("crlf", REAL.replace("\n", "\r\n"))):
            for bom in (b"", b"\xef\xbb\xbf"):
                with self.subTest(label=label, bom=bool(bom)):
                    original = bom + text.encode("utf-8")
                    self.write(original, raw=True)
                    p, r = self.apply()
                    self.assertEqual((p.action, p.preserved), ("add", ["blender", "docs"]))
                    now = self.read()
                    self.assertTrue(now.startswith(original))               # appended; nothing before it moved
                    eol = b"\r\n" if label == "crlf" else b"\n"
                    self.assertEqual(now.count(b"\r\n") > 0, label == "crlf")
                    self.assertTrue(now[len(original):].startswith(eol + b"[mcp_servers.tdflow]" + eol))
                    want = tomllib.loads(text)
                    want["mcp_servers"]["tdflow"] = self.entry
                    self.assertEqual(self.parsed(), want)
                    self.assertTrue(os.path.isfile(os.path.join(self.bk, r["backup"])))
                    self.assertTrue(C.codex_still_configured(self.path, self.entry))
                    self.assertEqual(C.plan_codex(self.path, self.entry, self.env).action, "unchanged")
                    u = C.remove_codex(self.path, self.env, self.bk, self.entry)
                    self.assertEqual(u["action"], "removed")
                    self.assertEqual(self.read(), original)                  # byte-identical
                    self.assertFalse(C.codex_still_configured(self.path, self.entry))

    def test_a_file_without_a_final_newline_is_restored_exactly(self):
        original = b'model = "gpt-5"\n[features]\nx = true'
        self.write(original, raw=True)
        p, r = self.apply()
        self.assertTrue(r["added_newline"])
        self.assertTrue(self.read().startswith(original + b"\n\n[mcp_servers.tdflow]\n"))
        C.remove_codex(self.path, self.env, self.bk, self.entry, added_newline=True)
        self.assertEqual(self.read(), original)

    def test_update_changes_two_lines_and_keeps_what_codex_or_the_user_added(self):
        self.write(REAL)
        self.apply()
        mine = self.read().decode("utf-8")
        # the user disabled it and Codex stored a per-tool approval; another table was appended afterwards
        mine = after_header(mine, "enabled = false   # mine\n")
        mine += '\n[mcp_servers.tdflow.tools.tdflow_whoami]\napproval_mode = "approve"\n\n[later]\nz = 1\n'
        self.write(mine)
        e2 = C.codex_entry("win32", 9555, "codex", os.path.join(self.h, "private", "connect"))
        p, r = self.apply(e2)
        self.assertEqual(p.action, "update")
        new = self.read().decode("utf-8")
        old_lines, new_lines = mine.splitlines(), new.splitlines()
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [b for a, b in zip(old_lines, new_lines) if a != b]
        self.assertEqual(sorted(x.split(" = ")[0] for x in changed), ["http_headers_helper", "url"])
        t = self.parsed()["mcp_servers"]["tdflow"]
        self.assertEqual((t["url"], t["enabled"], t["tools"]["tdflow_whoami"]["approval_mode"]),
                         ("http://127.0.0.1:9555/mcp", False, "approve"))
        self.assertIn("enabled = false   # mine", new)
        # Disconnect takes the table and its sub-table; the string look-alike and every other table stay
        C.remove_codex(self.path, self.env, self.bk, e2)
        want = tomllib.loads(REAL)
        want["later"] = {"z": 1}
        self.assertEqual(self.parsed(), want)
        self.assertIn('url = "not a table: this is inside a string"', self.read().decode("utf-8"))

    def test_replace_a_manual_entry_then_put_it_back(self):
        manual = ('[mcp_servers.tdflow]\nurl = "http://127.0.0.1:9480/mcp?k=SECRET"\n'
                  'bearer_token_env_var = "TDFLOW_TOKEN"   # from the manual setup\n\n'
                  '[mcp_servers.tdflow.tools.x]\napproval_mode = "approve"\n')
        self.write(REAL + "\n" + manual + "\n[after]\nq = 1\n")
        before = self.parsed()
        p = C.plan_codex(self.path, self.entry, self.env)
        self.assertEqual((p.action, p.previous_url), ("replace", "http://127.0.0.1:9480/mcp"))      # no query shown
        self.assertIn("REPLACES", C.consent_text("Codex", ["CLI"], p, "Builder", "/project1/**", True))
        r = C.apply_codex(p, self.env, self.bk)
        self.assertEqual(self.parsed()["mcp_servers"]["tdflow"], self.entry)       # no bearer_token_env_var left
        self.assertIn("bearer_token_env_var", r["previous_entry"]["toml"])
        for n in os.listdir(self.bk):                                              # backups pruned meanwhile
            os.remove(os.path.join(self.bk, n))
        u = C.remove_codex(self.path, self.env, self.bk, self.entry, restore_entry=r["previous_entry"])
        self.assertEqual(u["action"], "restored")
        self.assertEqual(self.parsed(), before)
        self.assertIn("# from the manual setup", self.read().decode("utf-8"))      # its comment came back too

    def test_layouts_that_are_not_edited_fail_closed(self):
        cases = {
            "inline table": 'mcp_servers = { other = { url = "https://x.invalid" } }\n',
            "dotted keys for tdflow": 'mcp_servers.tdflow.url = "http://127.0.0.1:1/mcp"\n',
            "tdflow only through a sub-table": '[mcp_servers.tdflow.tools.a]\napproval_mode = "approve"\n',
            "array of tables": '[[mcp_servers.tdflow]]\nurl = "x"\n',
            "not a table": 'mcp_servers = 5\n',
            "tdflow is a value": '[mcp_servers]\ntdflow = "x"\n',
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.write(text)
                self.refused("CONFIG_UNSUPPORTED")

    def test_invalid_files_are_never_touched(self):
        self.write("model = \n[broken")
        self.refused("CONFIG_INVALID")
        self.write('[mcp_servers.tdflow]\nurl = "a"\n[mcp_servers.tdflow]\nurl = "b"\n')       # defined twice
        self.refused("CONFIG_INVALID")
        self.write(b"model = \"\xff\xfe\"\n", raw=True)
        self.refused("CONFIG_INVALID")
        self.write(b"x" * (C.MAX_CONFIG_BYTES + 1), raw=True)
        self.refused("CONFIG_TOO_LARGE")

    def test_remove_leaves_an_entry_the_user_changed(self):
        self.write(REAL)
        self.apply()
        text = after_header(self.read().decode("utf-8"), 'bearer_token_env_var = "MINE"\n')
        self.write(text)
        self.assertEqual(C.remove_codex(self.path, self.env, self.bk, self.entry)["action"], "not_ours")
        self.assertEqual(self.read().decode("utf-8"), text)
        self.assertFalse(C.codex_still_configured(self.path, self.entry))
        os.remove(self.path)
        self.assertEqual(C.remove_codex(self.path, self.env, self.bk, self.entry)["action"], "absent")

    def test_concurrent_rewrite_with_the_same_action_proceeds(self):
        self.write(REAL)
        p = C.plan_codex(self.path, self.entry, self.env)
        self.write(REAL + "\n[written_by_codex_meanwhile]\nn = 2\n")
        C.apply_codex(p, self.env, self.bk)
        got = self.parsed()
        self.assertEqual(got["written_by_codex_meanwhile"], {"n": 2})      # the client's newer data is kept
        self.assertEqual(got["mcp_servers"]["tdflow"], self.entry)

    def test_someone_elses_tdflow_appearing_after_the_review_refuses(self):
        self.write(REAL)
        p = C.plan_codex(self.path, self.entry, self.env)
        self.write(REAL + '\n[mcp_servers.tdflow]\ncommand = "someone-else"\n')
        self.refused("CONFIG_CHANGED", lambda: C.apply_codex(p, self.env, self.bk))

    def _racing_writer(self, text):
        """Make another program write ``text`` into the file at the last moment: after TDflowMCP read and checked
        it, right before its atomic replace (once)."""
        real, state = C._atomic_write, {"done": False}

        def hooked(path, data, expect_sha=None, private=False):
            if path == self.path and not state["done"]:
                state["done"] = True
                with open(self.path, "ab") as f:
                    f.write(text.encode("utf-8"))
            return real(path, data, expect_sha=expect_sha, private=private)
        C._atomic_write = hooked
        self.addCleanup(setattr, C, "_atomic_write", real)

    def test_a_retry_never_applies_another_action_than_the_approved_one(self):
        """Review 23 (1): the user approved 'adds' (or an update of our own table). If someone else's tdflow table
        appears while the file is being written, the retry must not quietly turn that into a replace."""
        foreign = '\n[mcp_servers.tdflow]\ncommand = "someone-elses-server"\n'
        self.write(REAL)
        p = C.plan_codex(self.path, self.entry, self.env)
        self.assertEqual(p.action, "add")
        self._racing_writer(foreign)
        with self.assertRaises(C.ConnectError) as cm:
            C.apply_codex(p, self.env, self.bk)
        self.assertEqual(cm.exception.code, "CONFIG_CHANGED")
        self.assertEqual(self.read().decode("utf-8"), REAL + foreign)          # their entry is still there, untouched

    def test_a_retry_after_a_harmless_concurrent_write_still_applies_the_approved_action(self):
        self.write(REAL)
        p = C.plan_codex(self.path, self.entry, self.env)
        self._racing_writer("\n[written_meanwhile]\nn = 1\n")
        r = C.apply_codex(p, self.env, self.bk)
        self.assertEqual(r["action"], "add")
        got = self.parsed()
        self.assertEqual((got["written_meanwhile"], got["mcp_servers"]["tdflow"]), ({"n": 1}, self.entry))

    def test_an_update_is_not_turned_into_a_replace_by_a_racing_writer(self):
        self.write(REAL)
        self.apply()
        e2 = C.codex_entry("win32", 9555, "codex", os.path.join(self.h, "private", "connect"))
        p = C.plan_codex(self.path, e2, self.env)
        self.assertEqual(p.action, "update")
        real = C._atomic_write

        def hooked(path, data, expect_sha=None, private=False):          # the table becomes someone else's
            if path == self.path and b"MINE" not in self.read():
                self.write(after_header(self.read().decode("utf-8"), 'bearer_token_env_var = "MINE"\n'))
            return real(path, data, expect_sha=expect_sha, private=private)
        C._atomic_write = hooked
        self.addCleanup(setattr, C, "_atomic_write", real)
        with self.assertRaises(C.ConnectError) as cm:
            C.apply_codex(p, self.env, self.bk)
        self.assertEqual(cm.exception.code, "CONFIG_CHANGED")
        self.assertEqual(self.parsed()["mcp_servers"]["tdflow"]["bearer_token_env_var"], "MINE")
        self.assertEqual(self.parsed()["mcp_servers"]["tdflow"]["url"], "http://127.0.0.1:9480/mcp")   # not updated

    def test_a_failed_read_back_does_not_undo_what_another_program_wrote(self):
        """Review 23 (4): the file is put back only if it is still exactly what TDflowMCP wrote."""
        self.write(REAL)
        p = C.plan_codex(self.path, self.entry, self.env)
        real = C._atomic_write

        def hooked(path, data, expect_sha=None, private=False):
            out = real(path, data, expect_sha=expect_sha, private=private)
            if path == self.path and expect_sha is not None and b"[other_program]" not in self.read():
                with open(self.path, "ab") as f:                          # right after our replace
                    f.write(b"\n[other_program]\nwrote = true\n")
            return out
        C._atomic_write = hooked
        self.addCleanup(setattr, C, "_atomic_write", real)
        with self.assertRaises(C.ConnectError) as cm:
            C.apply_codex(p, self.env, self.bk)
        self.assertEqual(cm.exception.code, "CONFIG_NOT_VALIDATED")
        self.assertEqual(self.parsed()["other_program"], {"wrote": True})          # their write survived

    def test_only_a_newline_ends_a_line(self):
        """Review 23 (3): U+0085, U+2028 and U+2029 are ordinary characters in TOML comments and strings. Text after
        one of them is not a new line, so a header look-alike there is neither followed nor deleted."""
        for sep in ("\u0085", "\u2028", "\u2029"):
            for text in (f"# note{sep}[mcp_servers.tdflow.tools]\n# more\n[t]\nx = 1\n",
                         f'x = "a{sep}[mcp_servers.tdflow] # "\n[t]\ny = 2\n',
                         f"# note{sep}[[[\n[t]\nz = 3\n"):
                with self.subTest(sep=repr(sep), text=text[:24]):
                    self.write(text)
                    self.apply()
                    self.assertEqual(self.parsed()["mcp_servers"]["tdflow"], self.entry)
                    e2 = C.codex_entry("win32", 9555, "codex", os.path.join(self.h, "private", "connect"))
                    self.apply(e2)                                               # Repair after a port change
                    self.assertEqual(C.remove_codex(self.path, self.env, self.bk, e2)["action"], "removed")
                    self.assertEqual(self.read().decode("utf-8"), text)          # every character is back

    def test_blank_lines_at_the_end_of_the_file_are_kept(self):
        for original in ("a = 1\n\n", "a = 1\n\n\n", "\n", "# only a comment\n\n"):
            with self.subTest(original=original):
                self.write(original)
                self.apply()
                C.remove_codex(self.path, self.env, self.bk, self.entry)
                self.assertEqual(self.read().decode("utf-8"), original)

    def test_a_replaced_table_that_cannot_be_put_back_exactly_is_refused(self):
        """Review 23 (2): the text saved for Disconnect must parse to exactly the table it replaces."""
        self.write('[mcp_servers.tdflow]\ncommand = "x"\nnote = """\nabc\n#"""\n\n[t]\nx = 1\n')
        self.refused("CONFIG_UNSUPPORTED")

    def test_the_plan_shows_no_folder_of_a_replaced_program_and_no_forged_lines(self):
        """Review 23 (5)."""
        profile = "C:\\" + "Users" + "\\jdoe"                 # built at runtime (the doc checker's path pattern)
        self.write('[mcp_servers.tdflow]\ncommand = \'' + profile + '\\tools\\my server.exe\'\nargs = ["--key", "S3CRET"]\n'
                   '\n[mcp_servers."a\\nb) stay as they are.\\n- Enable MCP stays OFF"]\ncommand = "y"\n')
        p = C.plan_codex(self.path, self.entry, self.env)
        self.assertEqual((p.action, p.previous_url), ("replace", "program my"))
        full = C.consent_text("Codex", ["CLI"], p, "Builder", "/project1/**", True, platform="win32")
        for absent in ("jdoe", "S3CRET", "Enable MCP stays OFF", "tools\\"):
            self.assertNotIn(absent, full)
        self.assertIn("(a server with an unusual name)", full)
        self.assertEqual(C._safe_target({"command": "/" + "home" + "/me/bin/srv --token abc"}), "program srv")
        self.assertEqual(C._safe_target({"url": "https://h.example/mcp?k=1"}), "https://h.example/mcp")
        self.assertEqual(C._safe_target("x"), "")

    def test_last_moment_change_is_not_overwritten(self):
        self.write(REAL)
        with self.assertRaises(C.ConnectError):
            C._atomic_write(self.path, b"x = 1\n", expect_sha="0" * 64)
        self.assertEqual(self.read().decode("utf-8"), REAL)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.path)) if n.endswith(".tmp")], [])

    def test_linked_config_is_refused(self):
        real = os.path.join(self.h, "dotfiles.toml")
        with open(real, "w", encoding="utf-8") as f:
            f.write(REAL)
        try:
            os.symlink(real, self.path)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links need extra privileges here")
        with self.assertRaises(C.ConnectError) as cm:
            C.plan_codex(self.path, self.entry, self.env)
        self.assertEqual(cm.exception.code, "CONFIG_IS_LINK")

    def test_codex_home_is_respected_and_never_shown_with_the_user_name(self):
        e = env_for(self.h, environ={"CODEX_HOME": os.path.join(self.h, "elsewhere")})
        self.assertEqual(C.codex_config_path(e), os.path.join(self.h, "elsewhere", "config.toml"))
        self.assertEqual(C.display_path(C.codex_config_path(e), e), "%CODEX_HOME%/config.toml")
        self.assertEqual(C.display_path(self.path, self.env), "~/.codex/config.toml")
        before = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = "X:\\real"
        try:
            self.assertNotIn("CODEX_HOME", C.Env.current(self.h).environ)      # tests never follow the real one
        finally:
            if before is None:
                del os.environ["CODEX_HOME"]
            else:
                os.environ["CODEX_HOME"] = before

    def test_the_consent_text_names_the_file_and_has_no_secret(self):
        self.write(REAL)
        p = C.plan_codex(self.path, self.entry, self.env)
        full = C.consent_text("Codex", ["CLI", "VS Code extension"], p, "Builder", "/project1/fx/**", True, platform="win32")
        for part in ("~/.codex/config.toml, in your user folder", 'adds the server "tdflow"', "blender, docs", "backup",
                     "Windows' protected storage", "never in Codex's settings or the project", "TURNED ON",
                     "Needs a recent Codex", "update Codex and pulse Repair Connection", "not for Codex cloud tasks",
                     "pulse Copy Agent Prompt, start a new Codex chat", "a new chat in the Codex panel",
                     "already open does not have the connection", "Disconnect Agent"):
            self.assertIn(part, full)
        self.assertNotIn(self.h, full)
        self.assertNotIn("http_headers_helper", full)                 # usability review: no jargon in the plan
        short = C.consent_short("Codex", p, "Builder", "/project1/fx/**", True, platform="win32")
        self.assertTrue(all(len(x) <= 25 for x in short.splitlines()), short)
        self.assertIn("~/.codex/config.toml", short)
        self.assertIn("Then: Copy Agent Prompt,\npaste it in a new\nCodex chat.", short)
        self.assertLessEqual(len(short.splitlines()), 18)             # as long as the Claude Code dialog was (UX-08)

    def test_the_short_plan_says_what_each_action_does(self):
        """Usability review: replace names the file, update does not say 'adds', and no backup is claimed when
        nothing is written. Every line fits TouchDesigner's dialog for both clients."""
        mine = "".join(C._codex_block(self.entry, "\n", "tdflow"))
        other = "".join(C._codex_block(C.codex_entry("win32", 9555, "codex", os.path.join(self.h, "private", "connect")),
                                       "\n", "tdflow"))
        cases = {"add": (REAL, 'Adds "tdflow" to\n~/.codex/config.toml\n(other servers kept,\nbackup made).'),
                 "update": (other, 'Updates "tdflow" in\n~/.codex/config.toml\n(nothing else changes,\nbackup made).'),
                 "replace": ('[mcp_servers.tdflow]\ncommand = "x"\n',
                             'REPLACES "tdflow" in\n~/.codex/config.toml.\nDisconnect puts it back.'),
                 "unchanged": (mine, '"tdflow" is already set\nup: no file change.\nKey:')}
        for action, (text, want) in cases.items():
            with self.subTest(action):
                self.write(text)
                p = C.plan_codex(self.path, self.entry, self.env)
                self.assertEqual(p.action, action)
                for client in ("Codex", "Claude Code"):
                    short = C.consent_short(client, p, "Builder", "/project1/fx/**", True, platform="win32")
                    self.assertTrue(all(len(x) <= 25 for x in short.splitlines()), short)
                self.assertIn(want, C.consent_short("Codex", p, "Builder", "/project1/fx/**", True, platform="win32"))
                full = C.consent_text("Codex", ["CLI"], p, "Builder", "/project1/fx/**", False, platform="win32")
                self.assertEqual("backup" in full, action != "unchanged")

    def test_one_interface_for_both_clients(self):
        self.assertEqual(C.client_config_path("codex", self.env), self.path)
        self.assertEqual(C.client_config_path("claude-code", self.env), os.path.join(self.h, ".claude.json"))
        self.assertEqual(C.client_entry("codex", "win32", 9480, "codex", VAULT), C.codex_entry("win32", 9480, "codex", VAULT))
        self.assertIn("headersHelper", C.client_entry("claude-code", "win32", 9480, "claude-code", VAULT))
        self.write(REAL)
        p = C.plan_client("codex", self.path, self.entry, self.env)
        r = C.apply_client(p, self.env, self.bk)
        self.assertTrue(C.client_still_configured("codex", self.path, self.entry))
        self.assertIn("TDflowMCP", C.helper_text(self.entry))
        self.assertEqual(C.remove_client("codex", self.path, self.env, self.bk, self.entry, r)["action"], "removed")
        self.assertEqual(self.read().decode("utf-8"), REAL)


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


# The environment Codex gives the helper (observed 2026-11-25, CLI 0.160.0 and the extension's engine): these names.
CODEX_ENV = ("APPDATA", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT", "PROGRAMDATA",
             "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROMPT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP",
             "USERDOMAIN", "USERNAME", "USERPROFILE", "WINDIR")


@unittest.skipUnless(sys.platform == "win32", "runs the real Windows helper through cmd.exe")
class CodexHelperEndToEnd(unittest.TestCase):
    """The exact helper command, run as Codex runs it on Windows (``cmd.exe /Q /D /C "<command>"`` with a reduced
    environment), against a loopback stand-in for the listener and a real DPAPI vault."""

    def run_helper(self, body, key=KEY, bad_proof=False, stored=True):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Listener)
        _Listener.body, _Listener.key, _Listener.bad_proof = body, key, bad_proof
        _Listener.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {k: v for k, v in os.environ.items() if k.upper() in CODEX_ENV}
        try:
            with tempfile.TemporaryDirectory() as d:
                if stored:
                    C.Vault("win32", d).put("codex", PID, TOKEN, KEY)
                cmd = C.codex_entry("win32", srv.server_address[1], "codex", d)["http_headers_helper"]
                r = subprocess.run('cmd.exe /Q /D /C "' + cmd + '"', capture_output=True, text=True, timeout=60,
                                   cwd=d, env=env)
                return r.returncode, r.stdout, r.stderr
        finally:
            srv.shutdown()
            srv.server_close()

    def test_right_project_with_proof_gets_its_token(self):
        rc, out, err = self.run_helper(json.dumps({"product": "TDflowMCP", "connect": PID}).encode())
        self.assertEqual((rc, json.loads(out)), (0, {"Authorization": "Bearer " + TOKEN}))
        self.assertNotIn(TOKEN, err)

    def test_every_failure_exits_non_zero_and_prints_nothing(self):
        """Codex sends no request when the helper fails (observed); an empty object would make it connect anyway."""
        ok = json.dumps({"connect": PID}).encode()
        cases = {"squatter without the key": dict(body=ok, key=None),
                 "wrong proof": dict(body=ok, bad_proof=True),
                 "another key": dict(body=ok, key="cd" * 32),
                 "another project": dict(body=json.dumps({"connect": "prj_ffffffffffffffff"}).encode()),
                 "no project": dict(body=b"{}"),
                 "path-like project": dict(body=json.dumps({"connect": "..\\..\\x"}).encode()),
                 "not json": dict(body=b"not json"),
                 "no stored credential": dict(body=ok, stored=False)}
        for label, kw in cases.items():
            with self.subTest(label):
                rc, out, err = self.run_helper(**kw)
                self.assertEqual((rc, out), (1, ""))
                self.assertNotIn("tdf_", out + err)


if __name__ == "__main__":
    unittest.main()
