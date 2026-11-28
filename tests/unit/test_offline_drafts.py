import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tdflowmcp import drafts
from tdflowmcp.engine import Engine
from tdflowmcp.identity import Identity
from tdflowmcp.policy import Policy
from tdflowmcp.store import AuditUnavailable, Store
from tdflowmcp.tools import core_registry


PROJECT = "project-fixture"
SCOPE = "/project1/fx"
PROTECTED = {"/project1/fx/private": "instance", "/project1/memory": "tdflowproject"}
CANARY = "OFFLINE_FIXTURE_STRING"


class MetadataOnly:
    def draft_protection(self):
        return dict(PROTECTED)


class OfflineDraftTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.open_store()
        self.st.put_agent("writer", "Writer", "builder", [(SCOPE + "/**", "read,create,update", "allow")])
        self.st.put_agent("viewer", "Viewer", "observer", [(SCOPE + "/**", "read", "allow")])
        self.st.put_token("writer-key", "writer", "salt", "digest")
        self.st.put_token("viewer-key", "viewer", "salt", "digest")
        self.policy = Policy("/project1")

    def open_store(self):
        self.st = Store(str(self.path / "project"), str(self.path / "private")).open()
        self.addCleanup(self.st.close)
        self.st.bind_project(PROJECT)

    def call(self, action, args=None, who="writer", project=PROJECT, guard=lambda: True):
        audit = {"tool": "offline_" + action}
        if who:
            audit.update(agent_id=who, token_id=who + "-key")
        return self.st.draft_request(project, action, args or {}, audit, self.policy, PROTECTED, guard)

    def owner(self, action, args=None):
        return self.call(action, args, who=None)

    def grant(self, who="writer", **patch_):
        return self.owner("grant", {"agent": who, "scope": SCOPE, "minutes": 10, "read": True, "author": True,
                                    "submit": True, **patch_})

    def create(self, **change):
        return self.call("create", {"family": "TOP", "title": "Test", "scope": SCOPE, "source": "value = 1\n", **change})

    @staticmethod
    def exact(d):
        return {k: d[k] for k in drafts.EXACT}

    def test_discovery_and_dedicated_grants_never_confer_graph_permissions(self):
        self.assertEqual([r["family"] for r in self.call("guide")["families"]], list(drafts.FAMILIES))
        self.assertEqual(self.create()["code"], "DENIED")
        self.grant("writer", author=False)
        self.assertEqual(self.create()["code"], "DENIED")
        self.grant()
        d = self.create(intended_target=SCOPE + "/future_top")
        self.assertEqual(d["state"], "draft")
        verdict = self.policy.evaluate(Identity(self.st).agents["writer"], [
            drafts.Target("create", SCOPE + "/work", op_type="scriptTOP")])
        self.assertEqual((verdict.allow, verdict.rule_id), (False, "G2"))

    def test_scope_and_current_ordinary_read_deny(self):
        self.grant()
        for target in ("/project1/else/top", SCOPE + "/private/top", "/sys/top"):
            self.assertEqual(self.create(intended_target=target)["code"], "DENIED")
        self.assertEqual(self.create(scope=SCOPE + "/../invalid")["code"], "INVALID_ARGS")
        d = self.create(intended_target=SCOPE + "/future_top")
        self.st.grant_add("writer", SCOPE + "/future_top", "read", "deny")
        self.assertEqual(self.call("list")["drafts"], [])
        self.assertEqual(self.call("get", {"id": d["id"]})["code"], "DRAFT_NOT_FOUND")
        self.assertEqual(self.call("list", project="different")["code"], "DRAFT_PROJECT")

    def test_no_running_imports_decorators_or_arguments_and_no_source_in_audit(self):
        self.grant()
        marker = self.path / "unexpected.txt"
        payload = ("from pathlib import Path\n"
                   f"Path({str(marker)!r}).write_text({CANARY!r})\n"
                   "import nonexistent_fixture_package\n"
                   "@unknown()\n"
                   "def callback(default=unknown()):\n    return default\n")
        d = self.create(source=payload + "html = '<img src=x onerror=globalThis.tdflowProbe=1>'\n", title=CANARY)
        r = self.call("get", {"id": d["id"]})
        self.assertTrue(r["syntax_ok"])
        self.assertEqual(r["sha256"], hashlib.sha256(r["source"].encode()).hexdigest())
        self.assertIn("<img src=x", r["source"])
        self.assertFalse(marker.exists())
        self.assertNotIn(CANARY, json.dumps(self.st.events()))
        self.assertNotIn("nonexistent_fixture_package", json.dumps(self.st.events()))

    def test_syntax_limits_and_no_raw_diagnostics(self):
        self.grant()
        d = self.create(source="bad syntax OFFLINE_FIXTURE_STRING\n")
        self.assertFalse(d["syntax_ok"])
        self.assertNotIn(CANARY, json.dumps(d))
        self.assertEqual(self.call("submit", self.exact(d))["code"], "DRAFT_STATE")
        self.assertEqual(self.create(source="å" * drafts.MAX_BYTES)["code"], "DRAFT_LIMIT")
        self.assertEqual(self.create(source="x\n" * drafts.MAX_LINES)["code"], "DRAFT_LIMIT")
        self.assertEqual(self.create(source="'\u202e'")["code"], "DRAFT_TEXT")
        self.assertEqual(self.create(title="\ud800")["code"], "DRAFT_TEXT")

    def test_exact_revision_state_owner_only_and_diffs(self):
        self.grant()
        self.grant("viewer", author=False, submit=False)
        first = self.create()
        revise = {**self.exact(first), "source": "value = 2\n"}
        second = self.call("revise", revise)
        self.assertEqual(self.call("revise", revise)["code"], "DRAFT_CONFLICT")
        both = self.call("get", {"id": first["id"]}, who="viewer")
        self.assertIn("+value = 2\n", both["diff"]["lines"])
        self.assertEqual(self.call("submit", self.exact(second), who="viewer")["code"], "DRAFT_NOT_FOUND")
        self.assertEqual(self.call("decide", {**self.exact(second), "decision": "approved_offline"})["code"], "INVALID_ARGS")
        self.assertEqual(self.call("submit", self.exact(second))["state"], "pending_offline")
        self.assertEqual(self.owner("decide", {**self.exact(first), "decision": "approved_offline"})["code"], "DRAFT_CONFLICT")
        choice = {**self.exact(second), "decision": "approved_offline"}
        self.assertEqual(self.owner("decide", choice)["state"], "approved_offline")
        copy = self.owner("owner_export", self.exact(second))
        self.assertEqual((copy["source"], copy["manifest"]["disposition"]), ("value = 2\n", "approved_offline"))
        self.assertEqual(self.owner("decide", choice)["code"], "DRAFT_STATE")
        third = self.call("revise", {**self.exact(second), "source": "value = 3\n"})
        self.assertEqual((third["state"], third["revision"]), ("draft", 3))
        self.assertEqual(self.owner("owner_export", self.exact(second))["code"], "DRAFT_CONFLICT")

    def test_owner_approval_rechecks_revoked_or_expired_grants_and_permissions_remain_private(self):
        self.grant()
        first = self.create()
        self.call("submit", self.exact(first))
        self.owner("revoke", {"agent": "writer", "scope": SCOPE})
        review = {**self.exact(first), "decision": "approved_offline"}
        self.assertEqual(self.owner("decide", review)["code"], "DENIED")
        self.assertEqual(self.call("get", {"id": first["id"]})["code"], "DRAFT_NOT_FOUND")
        self.grant(minutes=1)
        self.st.revoke_token("writer-key")
        self.assertEqual(self.owner("decide", review)["code"], "DENIED")
        self.assertEqual(self.call("list")["code"], "DENIED")
        self.assertEqual(self.owner("decide", {**review, "decision": "rejected"})["state"], "rejected")

    def test_restart_integrity_and_no_public_records(self):
        self.grant()
        d = self.create(source=CANARY + " = True\n")
        self.st.close()
        self.open_store()
        self.assertEqual(self.call("get", {"id": d["id"]})["sha256"], d["sha256"])
        self.assertNotIn(CANARY.encode(), Path(self.st.db_path).read_bytes())
        self.st._cc().execute("UPDATE script_drafts SET record=? WHERE id=?", ("{}", d["id"]))
        self.assertEqual(self.call("get", {"id": d["id"]})["code"], "DRAFT_CORRUPT")
        self.st._cc().execute("UPDATE script_draft_grants SET permissions='read' WHERE agent='writer'")
        self.assertEqual(self.call("list")["code"], "DRAFT_CORRUPT")

    def test_audit_intent_and_guard_failure_do_not_persist_source(self):
        self.grant()
        with patch.object(self.st, "append", side_effect=AuditUnavailable("fixture failure")):
            with self.assertRaises(AuditUnavailable):
                self.create()
        decisions = iter((True, True, False))
        self.assertEqual(self.call("create", {"family": "TOP", "scope": SCOPE, "title": "x", "source": "x=1"},
                                   guard=lambda: next(decisions))["code"], "DRAFT_PAUSED")
        self.assertEqual(self.call("list")["drafts"], [])

    def test_engine_uses_offline_dispatch_and_audit_outcome_failure_suspends(self):
        self.grant()
        reg = core_registry()
        name = drafts.TOOL_NAMES["create"]
        eng = Engine(self.st, Identity(self.st), self.policy, reg, MetadataOnly(), [name], lambda: False)
        eng.draft_project = PROJECT
        args = {"family": "TOP", "scope": SCOPE, "title": "x", "source": "print('OFFLINE_FIXTURE_STRING')"}
        self.assertTrue(eng.call("writer-key", name, args, in_script=True)["isError"])
        self.assertFalse(eng.call("writer-key", name, args)["isError"])
        saved = self.st.append

        def fail_outcome(kind, **fields):
            if kind == "outcome":
                raise AuditUnavailable("fixture")
            return saved(kind, **fields)

        with patch.object(self.st, "append", side_effect=fail_outcome):
            r = eng.call("writer-key", name, args)
        self.assertEqual(r["structuredContent"]["code"], "AUDIT_UNAVAILABLE")
        self.assertTrue(eng.suspended())
        self.assertNotIn(CANARY, json.dumps(self.st.events()))

    def test_worker_serializes_competing_revisions(self):
        from tdflowmcp.auditworker import AuditWorker, StoreProxy

        self.grant()
        first = self.create()
        private, datadir = self.st.private_dir, self.st.datadir
        self.st.close()
        self.st = StoreProxy(worker := AuditWorker(datadir, private_dir=private).start())
        self.addCleanup(lambda: worker.close().result(5))
        self.assertTrue(worker.ready.wait(5))
        results = []

        def attempt(source):
            results.append(self.call("revise", {**self.exact(first), "source": source}))

        contenders = [threading.Thread(target=attempt, args=(f"value = {n}\n",)) for n in (4, 5)]
        for thread in contenders:
            thread.start()
        for thread in contenders:
            thread.join(5)
        self.assertEqual(sorted(row.get("code", "OK") for row in results), ["DRAFT_CONFLICT", "OK"])

    def test_rate_and_diff_limits(self):
        self.grant()
        a = self.create(source=("\n".join("a" * 100 for _ in range(100))))
        self.call("revise", {**self.exact(a), "source": "\n".join("b" * 100 for _ in range(100))})
        diff = self.call("get", {"id": a["id"]})["diff"]
        self.assertTrue(diff["truncated"])
        self.assertLessEqual(len("".join(diff["lines"]).encode()), drafts.MAX_DIFF_BYTES)
        for _ in range(drafts.CALLS_PER_MINUTE - 3):
            self.assertIn("drafts", self.call("list"))
        self.assertEqual(self.call("list")["code"], "DRAFT_RATE")

    def test_capabilities_from_current_private_grants(self):
        reg = core_registry()
        eng = Engine(self.st, Identity(self.st), self.policy, reg, MetadataOnly(), ["tdflow_capabilities"], lambda: False)
        eng.active = frozenset(t.name for t in reg.all())
        eng.draft_project = PROJECT

        def available():
            out = eng.call("writer-key", "tdflow_capabilities", {})["structuredContent"]
            return {entry["capability"] for entry in out["your_capabilities"]["available"]}

        self.assertNotIn("Create and revise offline Script drafts", available())
        self.grant()
        self.assertIn("Create and revise offline Script drafts", available())
        self.owner("revoke", {"agent": "writer", "scope": SCOPE})
        self.assertNotIn("Create and revise offline Script drafts", available())
