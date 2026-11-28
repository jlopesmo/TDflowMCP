"""Bounded text records for offline Script review. No TouchDesigner adapter or execution API."""
from __future__ import annotations

import ast
import difflib
import hashlib
import hmac
import json
import secrets
import time
import unicodedata

from tdflowmcp import schema
from tdflowmcp.policy import Agent, Grant, Target

FAMILIES = ("CHOP", "SOP", "TOP")
GRANTS = ("read", "author", "submit")
MAX_BYTES, MAX_LINES, MAX_DRAFTS, MAX_REVISIONS = 12288, 400, 32, 100
MAX_DRAFTS_PER_AGENT = 8
MAX_GRANTS, CALLS_PER_MINUTE = 64, 30
MAX_DIFF_BYTES, MAX_DIFF_LINES = 8192, 80
WARNING = ("Offline review only. Manually placing and activating this source in TouchDesigner can run Python "
           "with host access. Graph Undo cannot reverse effects on files, networks or other host resources.")
PATH = {"type": "string", "maxLength": 256, "pattern": r"^/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*$"}
ID = {"type": "string", "pattern": r"^[0-9a-f]{32}$", "minLength": 32, "maxLength": 32}
HASH = {"type": "string", "pattern": r"^[0-9a-f]{64}$", "minLength": 64, "maxLength": 64}
EXACT = {"id": ID, "revision": {"type": "integer", "minimum": 1, "maximum": MAX_REVISIONS}, "sha256": HASH}
SOURCE = {"type": "string", "minLength": 1, "maxLength": MAX_BYTES}
FAMILY = {"type": "string", "enum": list(FAMILIES)}
AGENT = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"^[A-Za-z0-9_-]+$"}
TOOL_NAMES = {
    "guide": "tdflow_script_draft_guide",
    "list": "tdflow_script_draft_list",
    "get": "tdflow_script_draft_get",
    "create": "tdflow_script_draft_create",
    "revise": "tdflow_script_draft_revise",
    "submit": "tdflow_script_draft_submit",
}
DRAFT_ACCESS = {
    "draft_read": ("read",),
    "draft_author": ("read", "author"),
    "draft_submit": ("read", "submit"),
}


def closed(props, optional=()):
    return {"type": "object", "properties": props, "additionalProperties": False,
            "required": [key for key in props if key not in optional]}


AGENT_SCHEMAS = {
    "guide": closed({"family": FAMILY}, ("family",)),
    "list": closed({}),
    "get": closed({"id": ID}),
    "create": closed({"family": FAMILY, "title": {"type": "string", "minLength": 1, "maxLength": 80},
                      "scope": PATH, "intended_target": PATH, "source": SOURCE}, ("intended_target",)),
    "revise": closed({**EXACT, "source": SOURCE}),
    "submit": closed(EXACT),
}
OWNER_SCHEMAS = {
    "owner_list": closed({}),
    "owner_get": closed({"id": ID}),
    "owner_export": closed(EXACT),
    "discard": closed({**EXACT, "confirmed": {"type": "boolean", "const": True}}),
    "decide": closed({**EXACT, "decision": {"type": "string", "enum": ["approved_offline", "rejected"]}}),
    "grant": closed({"agent": AGENT, "scope": PATH,
                     "minutes": {"type": "integer", "minimum": 1, "maximum": 120},
                     **{permission: {"type": "boolean"} for permission in GRANTS}}),
    "revoke": closed({"agent": AGENT, "scope": PATH}),
}
DDL = """
CREATE TABLE IF NOT EXISTS script_drafts (
  project TEXT NOT NULL, id TEXT NOT NULL, record TEXT NOT NULL, mac TEXT NOT NULL,
  PRIMARY KEY(project, id));
CREATE TABLE IF NOT EXISTS script_draft_grants (
  project TEXT NOT NULL, agent TEXT NOT NULL, scope TEXT NOT NULL, expires INTEGER NOT NULL,
  permissions TEXT NOT NULL, mac TEXT NOT NULL, PRIMARY KEY(project, agent, scope));
"""


class DraftError(Exception):
    def __init__(self, code, message):
        self.result = {"code": code, "message": message}


def fail(code, message):
    raise DraftError(code, message)


def under(path, scope):
    return path == scope or path.startswith(scope + "/")


def _encode(source):
    try:
        return source.encode("utf-8")
    except UnicodeError:
        fail("DRAFT_TEXT", "Source must be UTF-8 text.")


def inspect_source(source):
    raw = _encode(source)
    if not raw or len(raw) > MAX_BYTES or source.count("\n") >= MAX_LINES:
        fail("DRAFT_LIMIT", "Source exceeds the offline byte or line limit.")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") and ch not in "\n\t" for ch in source):
        fail("DRAFT_TEXT", "Use LF newlines and visible text. Hidden control characters are refused.")
    result = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "syntax_ok": True}
    try:
        ast.parse(source, filename="<offline draft>", mode="exec")
    except SyntaxError as exc:
        result.update(syntax_ok=False, diagnostic={"line": exc.lineno, "column": exc.offset,
                                                   "message": "Python syntax could not be parsed."})
    except (ValueError, RecursionError, OverflowError):
        result.update(syntax_ok=False, diagnostic={"message": "Source could not be parsed within the offline limits."})
    return result


def _diff(previous, current):
    limit = MAX_DIFF_BYTES
    lines = []
    changes = difflib.unified_diff(previous.splitlines(keepends=True), current.splitlines(keepends=True),
                                   "previous revision", "current revision", n=2)
    for line in changes:
        limit -= len(line.encode("utf-8"))
        if limit < 0 or len(lines) >= MAX_DIFF_LINES:
            return {"lines": lines, "truncated": True}
        lines.append(line)
    return {"lines": lines, "truncated": False}


def _guide(family):
    hints = {"CHOP": "Plan channels and sample counts.", "SOP": "Plan geometry and attributes.",
             "TOP": "Plan the input and output image format."}
    return {"families": [dict(family=f, guidance=hints[f], template="def onCook(scriptOp):\n    return\n")
                         for f in FAMILIES if family is None or family == f],
            "validation": "Bounded syntax parsing only. Runtime behavior is unverified.", "warning": WARNING,
            "limits": {"bytes": MAX_BYTES, "lines": MAX_LINES, "drafts": MAX_DRAFTS,
                       "drafts_per_author": MAX_DRAFTS_PER_AGENT, "revisions": MAX_REVISIONS}}


class Workspace:
    """Accessed only by the Store worker. The credential database is local and private."""

    def __init__(self, db, project, key, policy, protected, agent_id, token_id):
        self.db, self.project, self.key, self.policy = db, project, key, policy
        self.protected, self.agent_id, self.token_id = protected, agent_id, token_id
        self.now = int(time.time())
        self.grants = []
        self.grant_expiry = None
        self.agent = None

    def authenticate(self):
        if self.agent_id is None:
            return
        row = self.db.execute("SELECT a.role, a.enabled FROM agents a JOIN tokens t ON t.agent_id=a.id "
                              "WHERE a.id=? AND t.token_id=? AND t.revoked_at IS NULL",
                              (self.agent_id, self.token_id)).fetchone()
        if row is None or not row[1]:
            fail("DENIED", "The agent or token is no longer active.")
        scopes = self.db.execute("SELECT scope,actions,effect FROM grants WHERE agent_id=?", (self.agent_id,))
        grants = tuple(Grant(scope, frozenset(actions.split(",")), effect) for scope, actions, effect in scopes)
        self.agent = Agent(self.agent_id, "", row[0], grants=grants)
        for scope, expires, permissions, seal in self.db.execute(
                "SELECT scope,expires,permissions,mac FROM script_draft_grants WHERE project=? AND agent=?",
                (self.project, self.agent_id)):
            self._check_grant(self.agent_id, scope, expires, permissions, seal)
            if expires > self.now:
                self.grants.append((scope, permissions))
                self.grant_expiry = min(self.grant_expiry or expires, expires)

    def _target(self, path):
        kind = next((kind for protected, kind in self.protected.items() if under(path, protected)), "")
        return Target("read", path, protected=kind)

    def _permitted(self, scope, target, *permissions):
        targets = [self._target(scope)]
        if target is not None:
            if not under(target, scope):
                return False
            targets.append(self._target(target))
        if any(self.policy.hard_deny(item) for item in targets):
            return False
        if self.agent_id is None:
            return True
        return (all(any(under(scope, granted) and permission in actions.split(",")
                        for granted, actions in self.grants) for permission in permissions)
                and self.policy.evaluate(self.agent, targets).allow)

    def _signed(self, id_, raw):
        return hmac.new(self.key, (self.project + ":" + id_ + ":" + raw).encode("utf-8"), hashlib.sha256).hexdigest()

    def _decoded(self, id_, raw, mac):
        if not hmac.compare_digest(self._signed(id_, raw), mac):
            fail("DRAFT_CORRUPT", "The private draft record could not be verified.")
        return json.loads(raw)

    def _get(self, id_):
        row = self.db.execute("SELECT record,mac FROM script_drafts WHERE project=? AND id=?", (self.project, id_)).fetchone()
        if row is None:
            fail("DRAFT_NOT_FOUND", "No accessible draft has that identifier.")
        return self._decoded(id_, *row)

    def _save(self, d):
        raw = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
        self.db.execute("INSERT INTO script_drafts VALUES(?,?,?,?) "
                        "ON CONFLICT(project,id) DO UPDATE SET record=excluded.record,mac=excluded.mac",
                        (self.project, d["id"], raw, self._signed(d["id"], raw)))

    def _view(self, d, source=False):
        result = {k: v for k, v in d.items() if k not in ("source", "previous", "submission_token")}
        if source:
            result.update(source=d["source"], diff=_diff(d["previous"], d["source"]) if d["previous"] is not None
                          else {"lines": [], "truncated": False})
        return result

    def _exact(self, args, d):
        if args["revision"] != d["revision"] or args["sha256"] != d["sha256"]:
            fail("DRAFT_CONFLICT", "The revision or hash changed. Read the draft again.")

    def _records(self):
        for id_, raw, mac in self.db.execute("SELECT id,record,mac FROM script_drafts WHERE project=? LIMIT ?",
                                            (self.project, MAX_DRAFTS + 1)):
            yield self._decoded(id_, raw, mac)

    def _list(self):
        items = [self._view(d) for d in self._records() if self._permitted(d["scope"], d["intended_target"], "read")]
        return sorted(items, key=lambda d: (d["updated_at"], d["id"]), reverse=True)

    def _owner_grants(self):
        cols = ("agent", "scope", "expires", "permissions")
        result = []
        for agent, scope, expires, permissions, seal in self.db.execute(
                "SELECT agent,scope,expires,permissions,mac FROM script_draft_grants WHERE project=? "
                "ORDER BY agent,scope LIMIT ?", (self.project, MAX_GRANTS + 1)):
            self._check_grant(agent, scope, expires, permissions, seal)
            if expires > self.now:
                result.append(dict(zip(cols, (agent, scope, expires, permissions))))
        return result

    def _seal_grant(self, agent, scope, expires, permissions):
        return self._signed("grant", json.dumps([agent, scope, expires, permissions]))

    def _check_grant(self, agent, scope, expires, permissions, seal):
        if not hmac.compare_digest(self._seal_grant(agent, scope, expires, permissions), seal):
            fail("DRAFT_CORRUPT", "The private draft grant could not be verified.")

    def grant_subject(self, action, args):
        if self.agent_id is not None or action not in ("grant", "revoke") or schema.errors(args, OWNER_SCHEMAS[action]):
            return None
        agent, scope = args["agent"], args["scope"]
        if self._permitted(scope, None) and self.db.execute("SELECT 1 FROM agents WHERE id=?", (agent,)).fetchone():
            return {"agent": agent, "scope": scope}
        return None

    def _grant(self, args):
        agent, scope = args["agent"], args["scope"]
        if not self._permitted(scope, None):
            fail("DENIED", "This scope is protected or outside the project.")
        enabled = self.db.execute("SELECT enabled FROM agents WHERE id=?", (agent,)).fetchone()
        if enabled != (1,):
            fail("DENIED", "Choose an enabled agent.")
        actions = [permission for permission in GRANTS if args[permission]]
        if not actions:
            fail("INVALID_ARGS", "Choose at least one draft permission.")
        existing = self._owner_grants()
        if len(existing) >= MAX_GRANTS and not any((row["agent"], row["scope"]) == (agent, scope) for row in existing):
            fail("DRAFT_LIMIT", "The project has reached the draft grant limit.")
        self.db.execute("DELETE FROM script_draft_grants WHERE project=? AND expires<=?", (self.project, self.now))
        expires, permissions = self.now + args["minutes"] * 60, ",".join(actions)
        seal = self._seal_grant(agent, scope, expires, permissions)
        self.db.execute("INSERT INTO script_draft_grants VALUES(?,?,?,?,?,?) ON CONFLICT(project,agent,scope) "
                        "DO UPDATE SET expires=excluded.expires,permissions=excluded.permissions,mac=excluded.mac",
                        (self.project, agent, scope, expires, permissions, seal))
        return {"changed": True, "grant": {"agent": agent, "scope": scope, "permissions": actions, "expires": expires}}

    def _revoke(self, args):
        agent, scope = args["agent"], args["scope"]
        row = self.db.execute("SELECT expires,permissions,mac FROM script_draft_grants WHERE project=? AND agent=? AND scope=?",
                              (self.project, agent, scope)).fetchone()
        if row is None:
            return {"changed": False}
        expires, permissions, seal = row
        self._check_grant(agent, scope, expires, permissions, seal)
        self.db.execute("DELETE FROM script_draft_grants WHERE project=? AND agent=? AND scope=?",
                        (self.project, agent, scope))
        return {"changed": True, "grant": {"agent": agent, "scope": scope,
                                           "permissions": permissions.split(","), "expires": expires}}

    def run(self, action, args):
        contract = OWNER_SCHEMAS if self.agent_id is None else AGENT_SCHEMAS
        if action not in contract or schema.errors(args, contract[action]):
            fail("INVALID_ARGS", "Invalid offline workspace request.")
        self.authenticate()
        if action == "guide":
            return _guide(args.get("family"))
        if action in ("list", "owner_list"):
            data = {"drafts": self._list()}
            if action == "owner_list":
                data.update(grants=self._owner_grants(), warning=WARNING)
            return data
        if action == "grant":
            return self._grant(args)
        if action == "revoke":
            return self._revoke(args)
        if action == "create":
            scope, target = args["scope"], args.get("intended_target")
            if not self._permitted(scope, target, "read", "author"):
                fail("DENIED", "The draft scope or intended target is inaccessible.")
            records = list(self._records())
            if len(records) >= MAX_DRAFTS:
                fail("DRAFT_LIMIT", "The project has reached the draft count limit.")
            if sum(d["agent"] == self.agent_id for d in records) >= MAX_DRAFTS_PER_AGENT:
                fail("DRAFT_LIMIT", "This author has reached the draft count limit.")
            title = args["title"].strip()
            if not title or any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in title):
                fail("DRAFT_TEXT", "Use a visible title on one line.")
            _encode(title)
            d = {"id": secrets.token_hex(16), "agent": self.agent_id, "scope": scope, "intended_target": target,
                 "family": args["family"], "title": title, "created_at": self.now, "updated_at": self.now,
                 "revision": 1, "state": "draft", "reviewed_at": None, "previous": None, "source": args["source"],
                 **inspect_source(args["source"])}
            self._save(d)
            return self._view(d)
        d = self._get(args["id"])
        permission = {"revise": "author", "submit": "submit"}.get(action, "read")
        if not self._permitted(d["scope"], d["intended_target"], "read", permission):
            fail("DRAFT_NOT_FOUND", "No accessible draft has that identifier.")
        if action in ("get", "owner_get"):
            return self._view(d, source=True)
        if self.agent_id is not None and self.agent_id != d["agent"]:
            fail("DENIED", "Only the original author can revise or submit this draft.")
        self._exact(args, d)
        if action == "discard":
            self.db.execute("DELETE FROM script_drafts WHERE project=? AND id=?", (self.project, d["id"]))
            return {"id": d["id"], "revision": d["revision"], "state": "discarded"}
        if action == "owner_export":
            if d["state"] != "approved_offline":
                fail("DRAFT_STATE", "Only the currently reviewed revision can be copied.")
            keys = ("id", "revision", "sha256", "family", "scope", "intended_target", "bytes")
            return {**self._view(d, source=True),
                    "manifest": {"schema": "tdflowmcp.offline-draft/1", "project": self.project,
                                 **{k: d[k] for k in keys}, "disposition": "approved_offline", "warning": WARNING}}
        if action == "revise":
            if d["revision"] >= MAX_REVISIONS:
                fail("DRAFT_LIMIT", "This draft has reached the revision limit.")
            facts = inspect_source(args["source"])
            if facts["sha256"] == d["sha256"]:
                fail("DRAFT_CONFLICT", "The source is unchanged.")
            d.pop("diagnostic", None)
            d.update(facts, revision=d["revision"] + 1, previous=d["source"], source=args["source"],
                     state="draft", reviewed_at=None, submission_token=None)
        elif action == "submit":
            if d["state"] != "draft" or not d["syntax_ok"]:
                fail("DRAFT_STATE", "Submit a current draft with valid offline syntax.")
            d.update(state="pending_offline", submission_token=self.token_id)
        else:
            if d["state"] != "pending_offline":
                fail("DRAFT_STATE", "Only this pending revision can be reviewed.")
            if args["decision"] == "approved_offline":
                author = Workspace(self.db, self.project, self.key, self.policy, self.protected,
                                   d["agent"], d.get("submission_token"))
                author.authenticate()
                if not author._permitted(d["scope"], d["intended_target"], "read", "submit"):
                    fail("DENIED", "The submitting agent's draft permission is no longer active.")
                self.grant_expiry = author.grant_expiry
            d.update(state=args["decision"], reviewed_at=self.now)
        d["updated_at"] = self.now
        self._save(d)
        return self._view(d)
