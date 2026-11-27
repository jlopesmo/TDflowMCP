"""MCP protocol conformance (2026-07-28 + legacy), tool registry/schemas, and the call engine."""
import json
import os
import shutil
import tempfile
import unittest

from tdflowmcp import protocol, schema
from tdflowmcp.engine import Adapter, Engine, Resolution, ToolFailure
from tdflowmcp.identity import Identity
from tdflowmcp.policy import Policy, Target
from tdflowmcp.protocol import K_CAPS, K_VERSION, MODERN
from tdflowmcp.store import Store
from tdflowmcp.tools import Registry, RegistryError, Tool, core_registry

ALL = [t.name for t in core_registry().all()]


class FakeHost(protocol.ToolHost):
    def __init__(self):
        self.calls = []

    def list_tools(self):
        return [{"name": "tdflow_whoami", "inputSchema": {"type": "object", "additionalProperties": False}}]

    def call_tool(self, name, arguments, client_info, meta=None):
        self.calls.append((name, arguments, client_info))
        return None if name == "nope" else protocol.tool_result({"ok": True})

    def hello(self, client_info):
        self.hellos = getattr(self, "hellos", []) + [client_info]
        if client_info.get("name") == "boom":
            raise RuntimeError("a display-only step must never fail the handshake")


def modern(method, params=None, mid=1, headers=None, version=MODERN, caps=True):
    p = dict(params or {})
    meta = {K_VERSION: version, "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"}}
    if caps:
        meta[K_CAPS] = {}
    p["_meta"] = meta
    h = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if method == "tools/call":
        h["Mcp-Name"] = p.get("name")
    h.update(headers or {})
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": p}).encode(), h


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()

    def run_(self, body, headers):
        st, out, _ = protocol.handle(body, headers, self.host)
        return st, (json.loads(out) if out else None)

    def test_modern_ping_discover_list_call(self):
        for m in ("ping", "server/discover", "tools/list"):
            st, r = self.run_(*modern(m))
            self.assertEqual(st, 200, m)
            self.assertEqual(r["result"]["resultType"], "complete")
            self.assertEqual(r["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "TDflowMCP")
        st, r = self.run_(*modern("server/discover"))
        self.assertIn(MODERN, r["result"]["supportedVersions"])
        st, r = self.run_(*modern("tools/call", {"name": "tdflow_whoami", "arguments": {}}))
        self.assertEqual((st, r["result"]["isError"]), (200, False))
        self.assertEqual(self.host.calls[-1][2], {"name": "t", "version": "1"})

    def test_modern_caching_hints(self):
        # Found by the live Claude Code test: 2026-07-28 clients reject list results without them.
        st, r = self.run_(*modern("tools/list"))
        self.assertEqual((r["result"]["ttlMs"], r["result"]["cacheScope"]), (0, "private"))
        st, r = self.run_(*modern("server/discover"))
        self.assertIsInstance(r["result"]["ttlMs"], int)
        self.assertGreaterEqual(r["result"]["ttlMs"], 0)
        self.assertEqual(r["result"]["cacheScope"], "public")

    def test_modern_header_rules(self):
        body, h = modern("tools/list")
        for bad in ({"Mcp-Method": "tools/call"}, {"MCP-Protocol-Version": "2025-06-18"}):
            st, r = self.run_(body, dict(h, **bad))
            self.assertEqual((st, r["error"]["code"]), (400, -32020), bad)
        h2 = dict(h)
        h2.pop("Mcp-Method")
        self.assertEqual(self.run_(body, h2)[1]["error"]["code"], -32020)
        body, h = modern("tools/call", {"name": "tdflow_whoami", "arguments": {}}, headers={"Mcp-Name": "other"})
        self.assertEqual(self.run_(body, h)[1]["error"]["code"], -32020)

    def test_modern_version_and_meta(self):
        st, r = self.run_(*modern("ping", version="2099-01-01"))
        self.assertEqual((st, r["error"]["code"]), (400, -32022))
        self.assertIn(MODERN, r["error"]["data"]["supported"])
        st, r = self.run_(*modern("ping", caps=False))
        self.assertEqual((st, r["error"]["code"]), (400, -32602))
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        st, r = self.run_(body, {"MCP-Protocol-Version": MODERN, "Mcp-Method": "ping"})
        self.assertEqual((st, r["error"]["code"]), (400, -32602))

    def test_modern_unknown_method_and_tool(self):
        st, r = self.run_(*modern("resources/list"))
        self.assertEqual((st, r["error"]["code"]), (404, -32601))
        st, r = self.run_(*modern("tools/call", {"name": "nope", "arguments": {}}))
        self.assertEqual((st, r["error"]["code"]), (400, -32602))

    def test_legacy_flow(self):
        init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "c"}}}
        st, r = self.run_(json.dumps(init).encode(), {})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(r["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(self.host.hellos, [{"name": "c", "version": ""}])
        # ADR-0017: a legacy client (Codex: codex-mcp-client) is identified at the handshake; text is bounded, other
        # fields are dropped, and a failure in that display-only step never fails the handshake.
        for ci, want in (({"name": "codex-mcp-client", "title": "Codex", "version": "0.171.0-alpha.13.2", "x": 1},
                          {"name": "codex-mcp-client", "version": "0.171.0-alpha.13.2"}),
                         ({"name": "n" * 300, "version": 7}, {"name": "n" * 40, "version": "7"}),
                         ({"name": "boom"}, {"name": "boom", "version": ""})):
            init["params"]["clientInfo"] = ci
            st, r = self.run_(json.dumps(init).encode(), {})
            self.assertEqual((st, r["result"]["protocolVersion"], self.host.hellos[-1]), (200, "2025-06-18", want))
        n = len(self.host.hellos)
        init["params"]["clientInfo"] = "not an object"
        self.assertEqual(self.run_(json.dumps(init).encode(), {})[0], 200)
        self.assertEqual(len(self.host.hellos), n)
        init["params"]["protocolVersion"] = "1999-01-01"
        self.assertEqual(self.run_(json.dumps(init).encode(), {})[1]["result"]["protocolVersion"], "2025-11-25")
        note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        st, out, _ = protocol.handle(note, {}, self.host)
        self.assertEqual((st, out), (202, None))
        lst = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode()
        st, r = self.run_(lst, {"MCP-Protocol-Version": "2025-06-18"})
        self.assertEqual(st, 200)
        self.assertNotIn("resultType", r["result"])
        st, r = self.run_(lst, {})                                      # absent header = 2025-03-26
        self.assertEqual(st, 200)
        st, r = self.run_(lst, {"MCP-Protocol-Version": "1999-01-01"})
        self.assertEqual((st, r["error"]["code"]), (400, -32022))
        st, r = self.run_(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "nope"}).encode(), {})
        self.assertEqual((st, r["error"]["code"]), (200, -32601))

    def test_framing(self):
        self.assertEqual(self.run_(b"{", {})[1]["error"]["code"], -32700)
        self.assertEqual(self.run_(b"[]", {})[1]["error"]["code"], -32600)
        self.assertEqual(self.run_(b'{"jsonrpc":"1.0","id":1,"method":"ping"}', {})[1]["error"]["code"], -32600)
        self.assertEqual(self.run_(b'{"jsonrpc":"2.0","id":true,"method":"ping"}', {})[1]["error"]["code"], -32600)
        self.assertEqual(self.run_(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":[1]}', {})[1]["error"]["code"],
                         -32602)
        self.assertEqual(self.run_(b"\xff\xfe", {})[1]["error"]["code"], -32700)


class RegistryTests(unittest.TestCase):
    def test_registered_tools_with_classification(self):
        self.assertEqual(len(ALL), 33)
        reg = core_registry()
        for t in reg.all():
            d = t.describe()
            self.assertEqual(d["inputSchema"]["type"], "object")
            self.assertFalse(d["inputSchema"]["additionalProperties"])
            self.assertIn("readOnlyHint", d["annotations"])
        self.assertEqual(ALL, sorted(ALL))

    def test_registration_refuses_unclassified_tools(self):
        r = Registry()
        good = dict(name="tdflow_x", title="x", description="x", input_schema={"type": "object",
                    "additionalProperties": False}, actions=("read",), resolver="read_path", handler="h")
        r.register(Tool(**good))
        for bad in (dict(name="tdflow_x"), dict(name="other_y"), dict(name="tdflow_y", resolver="magic"),
                    dict(name="tdflow_y", actions=()), dict(name="tdflow_y", actions=("fly",)),
                    dict(name="tdflow_y", handler=""), dict(name="tdflow_y", input_schema={"type": "object"}),
                    dict(name="tdflow_y", read_only=True, actions=("update",)),
                    dict(name="tdflow_y", input_schema={"type": "object", "additionalProperties": False,
                                                        "oneOf": []})):
            with self.assertRaises((RegistryError, schema.SchemaError), msg=bad):
                r.register(Tool(**dict(good, **bad)))

    def test_schema_validation(self):
        s = core_registry().get("tdflow_set_parameters").input_schema
        self.assertEqual(schema.errors({"path": "/project1/a", "values": {"x": 1.5, "y": "s", "z": True}}, s), [])
        for bad in ({"values": {}}, {"path": "project1", "values": {}}, {"path": "/a", "expressions": {"x": 1}}, {"path": "/a/../b", "values": {}},
                    {"path": "/a", "values": {"x": [1]}}, {"path": "/a", "values": {}, "extra": 1},
                    {"path": "/a", "values": {f"p{i}": 1 for i in range(51)}}):
            self.assertTrue(schema.errors(bad, s), bad)
        c = core_registry().get("tdflow_create_operator").input_schema
        self.assertEqual(schema.errors({"parent": "/project1", "type": "noiseTOP", "name": "n1"}, c), [])
        self.assertTrue(schema.errors({"parent": "/project1", "type": "noiseTOP", "name": "1n"}, c))
        self.assertTrue(schema.errors({"parent": "/project1", "type": "evil", "name": "n"}, c))

    def test_schemas_match_bundled_jsonschema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (it is bundled with TouchDesigner's Python)")
        samples = {"tdflow_set_parameters": [{"path": "/project1/a", "values": {"x": 1}}, {"path": "x"},
                                             {"path": "/a", "values": {"x": [1]}}],
                   "tdflow_create_operator": [{"parent": "/p", "type": "noiseTOP", "name": "a"},
                                              {"parent": "/p", "type": "noiseTOP", "name": "a", "q": 1}],
                   "tdflow_set_dat_text": [{"path": "/p/t", "text": "x"}, {"path": "/p/t"}]}
        for t in core_registry().all():
            jsonschema.Draft202012Validator.check_schema(t.input_schema)
            v = jsonschema.Draft202012Validator(t.input_schema)
            for inst in samples.get(t.name, [{}]):
                self.assertEqual(v.is_valid(inst), not schema.errors(inst, t.input_schema), (t.name, inst))


class FakeAdapter(Adapter):
    def __init__(self):
        self.executed, self.undone = [], []
        self.fail = None

    def resolve(self, tool, args):
        if tool.resolver == "undo":
            return Resolution([])
        if str(args.get("path", "")).endswith("/missing"):
            raise ToolFailure("NOT_FOUND", "no such operator")
        acts = {"set_params": ["update"], "create": ["create"], "delete": ["delete"], "dat_text": ["update"]}.get(
            tool.resolver, ["read"])
        path = args.get("path") or (args.get("parent", "") + "/" + args.get("name", ""))
        return Resolution([Target(a, path, change="param" if a == "update" else "", param="x") for a in acts])

    def execute(self, tool, args, res, undo_label, readable):
        if self.fail:
            raise self.fail
        self.executed.append((tool.name, undo_label))
        return {"done": tool.name, "_audit": {"x": 1}}

    def undo(self, record, snapshot=None):
        self.undone.append(record["seq"])
        return {"undone": True, "verified": True}

    def project_summary(self):
        return {}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tdflow-eng-")
        self.store = Store(os.path.join(self.dir, "d")).open()
        self.idn = Identity(self.store)
        self.agent, tok = self.idn.create_agent("A", "builder", ["/project1/fx/**"])
        self.tid = self.idn.authenticate(tok)
        self.ad = FakeAdapter()
        self.susp = [False]
        self.eng = Engine(self.store, self.idn, Policy("/project1"), core_registry(), self.ad, ALL,
                          lambda: self.susp[0])

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def kinds(self):
        return [e["kind"] for e in reversed(self.store.events(limit=100))]

    def test_allowed_mutation_audits_intent_then_outcome(self):
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertFalse(r["isError"])
        sc = r["structuredContent"]
        self.assertTrue(sc["reversible"])
        self.assertTrue(sc["undo_label"].startswith("tdflow:"))
        self.assertEqual(self.kinds(), ["intent", "outcome"])
        self.assertEqual(self.ad.executed[0][1], sc["undo_label"])
        ev = self.store.events(limit=1)[0]
        self.assertEqual(ev["agent_id"], self.agent.id)
        self.assertIsNotNone(ev["session_id"])
        self.assertTrue(self.store.verify()["ok"])

    def test_denied_calls_are_audited_and_not_executed(self):
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/out1", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "DENIED")
        r = self.eng.call(self.tid, "tdflow_delete_operator", {"path": "/sys/x"})
        self.assertEqual(r["structuredContent"]["rule_id"], "H1")
        self.assertEqual(self.ad.executed, [])
        self.assertEqual(self.kinds(), ["deny", "deny"])

    def test_fail_closed_without_audit(self):
        self.store.close()
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "AUDIT_UNAVAILABLE")
        r = self.eng.call(self.tid, "tdflow_get_operator", {"path": "/project1/fx/a"})
        self.assertEqual(r["structuredContent"]["code"], "AUDIT_UNAVAILABLE")
        self.assertEqual(self.ad.executed, [])
        self.store = Store(self.store.datadir).open()

    def test_kill_switch_and_revocation_at_dispatch(self):
        self.susp[0] = True
        r = self.eng.call(self.tid, "tdflow_get_operator", {"path": "/project1/fx/a"})
        self.assertEqual(r["structuredContent"]["code"], "SUSPENDED")
        self.assertEqual(self.eng.list_tools(self.tid), [])
        self.susp[0] = False
        self.idn.revoke(self.tid)
        r = self.eng.call(self.tid, "tdflow_get_operator", {"path": "/project1/fx/a"})
        self.assertEqual(r["structuredContent"]["code"], "DENIED")
        self.assertEqual(self.ad.executed, [])

    def test_invalid_args_not_found_and_unknown_or_inactive_tool(self):
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "INVALID_ARGS")
        r = self.eng.call(self.tid, "tdflow_get_operator", {"path": "/project1/fx/missing"})
        self.assertEqual(r["structuredContent"]["code"], "NOT_FOUND")
        r = self.eng.call(self.tid, "tdflow_get_operator", {"path": "/project1/out1/missing"})
        self.assertEqual(r["structuredContent"]["code"], "DENIED")              # no existence leak outside scope
        self.assertIsNone(self.eng.call(self.tid, "tdflow_nope", {}))
        eng2 = Engine(self.store, self.idn, Policy("/project1"), core_registry(), self.ad, ["tdflow_whoami"],
                      lambda: False)
        self.assertIsNone(eng2.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {}}))
        self.assertEqual([t["name"] for t in eng2.list_tools(self.tid)], ["tdflow_whoami"])

    def test_legacy_handshake_identifies_the_client_only_for_a_valid_token(self):
        """ADR-0017: Codex speaks the legacy era, where tool calls carry no clientInfo. Its handshake does."""
        self.eng.hello(self.tid, {"name": "codex-mcp-client", "version": "0.171.0-alpha.13.2"})
        seen = self.eng.seen[self.agent.id]
        self.assertEqual((seen["client"], seen["version"]), ("codex-mcp-client", "0.171.0-alpha.13.2"))
        self.eng.call(self.tid, "tdflow_whoami", {})                      # a legacy call keeps what the handshake said
        self.assertEqual(self.eng.seen[self.agent.id]["client"], "codex-mcp-client")
        before = dict(self.eng.seen)
        self.eng.hello("not-a-token", {"name": "x", "version": "1"})
        self.eng.hello(self.tid, "not a dict")
        self.assertEqual(set(self.eng.seen), set(before))
        self.assertEqual(self.eng.seen[self.agent.id]["client"], "codex-mcp-client")
        # review 23: neither a disabled agent nor a revoked token can set what is shown or count as seen
        other, tok2 = self.idn.create_agent("B", "observer", ["/project1/fx/**"])
        tid2 = self.idn.authenticate(tok2)
        self.idn.set_enabled(other.id, False)
        self.eng.hello(tid2, {"name": "claude-code", "version": "9"})
        self.assertNotIn(other.id, self.eng.seen)
        self.idn.set_enabled(other.id, True)
        self.idn.revoke(tid2)
        self.eng.hello(tid2, {"name": "claude-code", "version": "9"})
        self.assertNotIn(other.id, self.eng.seen)

    def test_execution_failure_never_leaks(self):
        self.ad.fail = RuntimeError("secret internals")
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret internals", json.dumps(r))
        self.assertEqual(self.store.events(limit=1)[0]["outcome"], "error")

    def test_undo_own_last_change_once(self):
        self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        r = self.eng.call(self.tid, "tdflow_undo", {})
        self.assertTrue(r["structuredContent"]["undone"])
        self.assertTrue(r["structuredContent"]["verified"])
        r = self.eng.call(self.tid, "tdflow_undo", {})
        self.assertEqual(r["structuredContent"]["code"], "NOTHING_TO_UNDO")
        other, tok2 = self.idn.create_agent("B", "builder", ["/project1/fx/**"])
        r = self.eng.call(self.idn.authenticate(tok2), "tdflow_undo", {})
        self.assertEqual(r["structuredContent"]["code"], "NOTHING_TO_UNDO")     # never another agent's change

    def test_undo_conflict_reported(self):
        self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})

        def conflict(rec, snap=None):
            raise ToolFailure("UNDO_CONFLICT", "not on top")
        self.ad.undo = conflict
        r = self.eng.call(self.tid, "tdflow_undo", {})
        self.assertEqual(r["structuredContent"]["code"], "UNDO_CONFLICT")

    def test_mutation_identity_must_match_after_audit_intent(self):
        identifiers = iter((1, 2))
        original = self.ad.resolve

        def resolve(tool, args):
            result = original(tool, args)
            result.context["mutation_identity"] = next(identifiers)
            return result

        self.ad.resolve = resolve
        response = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(response["structuredContent"]["code"], "PRECONDITION_CHANGED")
        self.assertEqual(self.ad.executed, [])
        self.assertEqual(self.kinds(), ["intent", "outcome"])

    def test_undo_only_matches_the_expected_audited_change(self):
        edit = {"path": "/project1/fx/a", "values": {"x": 1}}
        first = self.eng.call(self.tid, "tdflow_set_parameters", edit)["structuredContent"]["audit_seq"]
        second = self.eng.call(self.tid, "tdflow_set_parameters", edit)["structuredContent"]["audit_seq"]
        stale = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": first})["structuredContent"]
        self.assertEqual(stale["code"], "UNDO_CONFLICT")
        self.assertEqual(self.ad.undone, [])
        restored = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": second})["structuredContent"]
        self.assertTrue(restored["verified"])
        self.assertEqual(restored["undoes"], second)
        receipt, intent = self.store.events(limit=2)
        self.assertEqual(json.loads(receipt["detail"])["intent_seq"], intent["seq"])
        self.assertEqual(json.loads(intent["detail"])["undoes"], second)
        replay = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": second})["structuredContent"]
        self.assertEqual(replay["code"], "UNDO_CONFLICT")
        self.assertEqual(self.ad.undone, [second])
        self.assertTrue(self.store.verify()["ok"])

    def test_undo_keeps_selection_locked_until_outcome(self):
        change = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        seq = change["structuredContent"]["audit_seq"]
        real_append = self.store.append
        competing = []

        def append(kind, **fields):
            if kind == "outcome" and fields.get("tool") == "tdflow_undo":
                competing.append(self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq}))
            return real_append(kind, **fields)

        self.store.append = append
        resumed = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq})["structuredContent"]
        self.assertTrue(resumed["verified"])
        self.assertEqual(competing[0]["structuredContent"]["code"], "UNDO_BUSY")
        self.assertEqual(self.ad.undone, [seq])

    def test_undo_snapshot_refuses_another_runtime(self):
        snap_path = "/project1/fx/notes"
        self.ad.execute = lambda *args: {"_audit": {"kind": "dat_text", "path": snap_path}}
        seq = self.eng.call(self.tid, "tdflow_set_dat_text", {"path": snap_path, "text": "one"})
        restarted = Engine(self.store, self.idn, Policy("/project1"), core_registry(), self.ad, ALL,
                           lambda: False)
        result = restarted.call(self.tid, "tdflow_undo",
                                {"expected_audit_seq": seq["structuredContent"]["audit_seq"]})["structuredContent"]
        self.assertEqual(result["code"], "UNDO_CONFLICT")
        self.assertEqual(self.ad.undone, [])

    def test_undo_reauthorizes_parameter_update_after_grant_change(self):
        from dataclasses import replace

        path = "/project1/fx/a"
        self.ad.execute = lambda *args: {"_audit": {"kind": "set_params", "path": path,
                                                 "prev": {"x": {"mode": "CONSTANT", "value": 0}}}}
        change = self.eng.call(self.tid, "tdflow_set_parameters", {"path": path, "values": {"x": 1}})
        seq = change["structuredContent"]["audit_seq"]
        current = replace(self.agent, grants=tuple(replace(g, param_denylist=("x",)) for g in self.agent.grants))
        self.idn.agents[self.agent.id] = current
        denied = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq})["structuredContent"]
        self.assertEqual(denied["rule_id"], "G3")
        self.assertEqual(self.ad.undone, [])
        self.idn.agents[self.agent.id] = self.agent
        restored = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq})["structuredContent"]
        self.assertTrue(restored["verified"])

    def test_undo_replaces_generic_parameter_targets_at_policy_limit(self):
        path = "/project1/fx/a"
        values = {f"x{i}": 1 for i in range(50)}
        self.ad.resolve = lambda tool, args: Resolution([
            Target("update", path, change="param", param=name) for name in args["values"]
        ])
        self.ad.execute = lambda *args: {"_audit": {"kind": "set_params", "path": path,
                                                 "prev": {name: {"mode": "CONSTANT"} for name in values}}}
        changed = self.eng.call(self.tid, "tdflow_set_parameters", {"path": path, "values": values})
        self.assertFalse(changed["isError"])
        result = self.eng.call(self.tid, "tdflow_undo",
                               {"expected_audit_seq": changed["structuredContent"]["audit_seq"]})
        self.assertTrue(result["structuredContent"]["verified"])

    def test_undo_reauthorizes_full_checkpoint_effects(self):
        from dataclasses import replace

        origin = "/project1/fx/original"
        source = origin + "/source"
        checkpoint = "/project1/fx/checkpoint"
        reads = [Target("read", f"{checkpoint}/node{i}") for i in range(50)]
        effects = [Target("delete", origin, change="delete"), Target("update", checkpoint, change="rename"),
                   Target("update", source, change="param", param="amp")]
        self.ad.resolve = lambda tool, args: Resolution(reads + effects)
        self.ad.execute = lambda *args: {"_audit": {"kind": "promote", "inert": True,
            "checkpoint_undo_effects": [[t.action, t.path, t.change, t.param] for t in effects]}}
        result = self.eng.call(self.tid, "tdflow_experiment_promote", {"path": checkpoint})
        self.assertFalse(result["isError"])
        seq = result["structuredContent"]["audit_seq"]
        self.idn.agents[self.agent.id] = replace(self.agent, grants=tuple(
            replace(grant, param_denylist=("amp",)) for grant in self.agent.grants))
        denied = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq})["structuredContent"]
        self.assertEqual(denied["rule_id"], "G3")
        self.assertEqual(self.ad.undone, [])
        self.idn.agents[self.agent.id] = self.agent
        restored = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq})["structuredContent"]
        self.assertTrue(restored["verified"])

    def test_undo_refuses_checkpoint_without_effect_record(self):
        checkpoint = "/project1/fx/checkpoint"
        self.ad.resolve = lambda tool, args: Resolution([Target("update", checkpoint)])
        self.ad.execute = lambda *args: {"_audit": {"kind": "promote", "inert": True}}
        result = self.eng.call(self.tid, "tdflow_experiment_promote", {"path": checkpoint})
        seq = result["structuredContent"]["audit_seq"]
        response = self.eng.call(self.tid, "tdflow_undo", {"expected_audit_seq": seq})["structuredContent"]
        self.assertEqual(response["code"], "UNDO_FAILED")
        self.assertEqual(self.ad.undone, [])

    def test_unverified_undo_is_an_error(self):
        self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.ad.undo = lambda rec, snap=None: {"undone": True, "verified": False}
        r = self.eng.call(self.tid, "tdflow_undo", {})
        self.assertEqual(r["structuredContent"]["code"], "UNDO_UNVERIFIED")
        self.assertTrue(r["isError"])
        self.assertTrue(self.eng.suspended())
        self.assertEqual(self.store.events(limit=1)[0]["outcome"], "unknown")

    def test_adapter_can_mark_change_not_reversible(self):
        self.ad.execute = lambda tool, args, res, label, readable: {"done": 1, "_audit": {"reversible": "no"}}
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertFalse(r["structuredContent"]["reversible"])
        self.assertIsNone(r["structuredContent"]["undo_label"])
        self.assertEqual(self.eng.call(self.tid, "tdflow_undo", {})["structuredContent"]["code"], "NOTHING_TO_UNDO")

    def test_outcome_write_failure_suspends_agents(self):
        hits = []
        self.eng.on_audit_failure = lambda: hits.append(1)
        real = self.store.append

        def flaky(kind, **kw):
            if kind == "outcome":
                from tdflowmcp.store import AuditUnavailable
                raise AuditUnavailable("x")
            return real(kind, **kw)
        self.store.append = flaky
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "OUTCOME_UNRECORDED")     # never reported as success
        self.assertTrue(r["isError"])
        self.assertEqual(hits, [1])
        self.store.append = real
        self.assertTrue(self.eng.suspended())
        self.assertEqual(self.eng.call(self.tid, "tdflow_whoami", {})["structuredContent"]["code"], "SUSPENDED")

    def test_kill_switch_between_plan_and_execute(self):
        calls = []

        def main(fn):
            calls.append(1)
            if len(calls) == 2:                     # the execute step: switch flipped during the intent wait
                self.susp[0] = True
            return fn()
        self.eng.main = main
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "SUSPENDED")
        self.assertEqual(self.ad.executed, [])
        kinds = self.kinds()
        self.assertEqual(kinds, ["intent", "outcome"])                        # accounted for, not executed

    def test_project_changed_between_plan_and_execute(self):
        n = [0]
        real = self.ad.resolve

        def resolve(tool, args):
            n[0] += 1
            r = real(tool, args)
            if n[0] == 2:
                r.targets[0] = r.targets[0].__class__("update", "/project1/fx/other", change="param", param="x")
            return r
        self.ad.resolve = resolve
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "PRECONDITION_CHANGED")
        self.assertEqual(self.ad.executed, [])

    def test_compare_rechecks_target_identity_before_sampling(self):
        args = {"left": "/project1/fx/a", "right": "/project1/fx/b"}
        ids = iter(((1, 2), (1, 3)))
        self.ad.resolve = lambda _tool, paths: Resolution(
            [Target("read", paths["left"]), Target("read", paths["right"])],
            {"read_identity": next(ids)})
        r = self.eng.call(self.tid, "tdflow_compare_tops", args)
        self.assertEqual(r["structuredContent"]["code"], "PRECONDITION_CHANGED")
        self.assertEqual(self.ad.executed, [])

    def test_compare_revocation_and_audit_fault_prevent_sampling(self):
        from tdflowmcp.store import AuditUnavailable

        args = {"left": "/project1/fx/a", "right": "/project1/fx/b"}
        self.ad.resolve = lambda _tool, paths: Resolution(
            [Target("read", paths["left"]), Target("read", paths["right"])],
            {"read_identity": (1, 2)})
        append = self.store.append

        def unavailable(*_args, **_kwargs):
            raise AuditUnavailable("test")

        self.store.append = unavailable
        self.assertEqual(self.eng.call(self.tid, "tdflow_compare_tops", args)["structuredContent"]["code"],
                         "AUDIT_UNAVAILABLE")
        self.store.append = append
        steps = iter((False, True))

        def dispatch(fn):
            if next(steps):
                self.idn.revoke(self.tid)
            return fn()

        self.eng.main = dispatch
        self.assertEqual(self.eng.call(self.tid, "tdflow_compare_tops", args)["structuredContent"]["code"], "DENIED")
        self.assertEqual(self.ad.executed, [])

    def test_main_step_not_run_or_unknown(self):
        from tdflowmcp.engine import MainNotRun, MainUnknown
        state = {"n": 0}

        def main(fn):
            state["n"] += 1
            if state["n"] == 2:
                raise MainNotRun("TDFLOW_TIMEOUT")
            return fn()
        self.eng.main = main
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "TDFLOW_TIMEOUT")
        self.assertFalse(self.eng.suspended())                               # never ran: nothing unaccounted
        state["n"] = 0

        def main2(fn):
            state["n"] += 1
            if state["n"] == 2:
                raise MainUnknown("TDFLOW_TIMEOUT_AFTER_DISPATCH")
            return fn()
        self.eng.main = main2
        r = self.eng.call(self.tid, "tdflow_set_parameters", {"path": "/project1/fx/a", "values": {"x": 1}})
        self.assertEqual(r["structuredContent"]["code"], "OUTCOME_UNKNOWN")
        self.assertTrue(self.eng.suspended())                                # may have run: suspend agents

    def test_dat_snapshot_is_durable_before_execute(self):
        seen = {}
        real = self.ad.resolve

        def resolve(tool, args):
            r = real(tool, args)
            if tool.resolver == "dat_text":
                r.context["snapshot"] = ("/project1/fx/t", 7, "old", "a" * 64, "b" * 64)
            return r

        def execute(tool, args, res, label, readable):
            seen["snap"] = self.store.get_snapshot(res.context["snapshot_id"])
            return {"done": 1}
        self.ad.resolve, self.ad.execute = resolve, execute
        r = self.eng.call(self.tid, "tdflow_set_dat_text", {"path": "/project1/fx/t", "text": "new"})
        self.assertFalse(r["isError"])
        self.assertEqual(seen["snap"]["before_text"], "old")

    def test_session_note_is_redacted(self):
        tok = "tdf_" + "A" * 12 + "." + "b" * 43
        self.eng.call(self.tid, "tdflow_session_note", {"text": "my token " + tok})
        self.assertNotIn(tok, json.dumps(self.store.events(limit=5)))

    def test_identity_tools_and_notes(self):
        r = self.eng.call(self.tid, "tdflow_whoami", {})
        self.assertEqual(r["structuredContent"]["role"], "builder")
        self.eng.call(self.tid, "tdflow_session_note", {"text": "hello"})
        r = self.eng.call(self.tid, "tdflow_session_list", {})
        s = r["structuredContent"]["sessions"]
        self.assertEqual(s[0]["notes"], 1)


if __name__ == "__main__":
    unittest.main()
