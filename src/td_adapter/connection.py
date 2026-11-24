"""Connect Agent: the consent-based connection workflow (owner D22, ADR-0015).

Drop the .tox -> Check Ready -> Connect Agent -> Approve -> Start Working. Stages, shown as Next Step:
preparing -> awaiting (the user reviews the exact plan) -> configuring -> enabling -> waiting (MCP ready, waiting for the
agent's first authenticated request) -> connected. ``failed`` carries a short reason and a recovery step.

Rules:
- Nothing outside TDflowMCP changes before the user approves the plan in TouchDesigner (a TouchDesigner dialog;
  without one, a second Connect Agent pulse within two minutes). The plan names the agent, the file, the change, what is
  preserved, the permissions, whether MCP will be turned on, and how to undo.
- File and vault I/O runs on short-lived background threads, never on TouchDesigner's main thread. Results are
  applied on the main thread by ``tick``.
- The token goes from the admin backend straight into the OS-protected vault on the background thread. It is never put
  in a parameter, a DAT, the clipboard, a log or the agent's configuration.
- Every step is audited (``client_connect`` / ``client_disconnect``: intent first, then the outcome); a failure after the
  credential was created revokes it again.
- Connected means an authenticated request from that agent arrived; a written file is not a connection.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import sys
import threading
import time
import types

import td

CONSENT_SECONDS = 120.0
VIEW_SECONDS = 120.0           # connect_view is cleared after this (it is saved with the .toe: review 19, finding 14)
RECHECK_SECONDS = 6.0
SLOTS = {"claude-code": "claude-code"}
STAGE_LINES = {                       # Next Step; every line fits the default parameter width (~60 characters)
    "preparing": "Preparing connection…",
    "choosing": "Choose an agent (dialog)",
    "awaiting": "Approve in the dialog.",
    "awaiting_pulse": "Review, then pulse again.",
    "configuring": "Configuring {client}…",
    "enabling": "Turning on MCP…",
    "waiting": "Start a {client} chat.",
    "waiting_save": "Save, then start a chat.",
    "waiting_off": "Turn on Enable MCP.",
    "idle": "Connected · idle {minutes} min",
    "no_request": "No request: /mcp reconnect",
    "no_credential": "No key: Repair Connection",
    "port_changed": "Port changed: pulse Repair",
}
FLOW = ("preparing", "choosing", "awaiting", "configuring", "enabling")
FAIL_LINES = {                          # Next Step after a failed step (the code is in View Connection Details)
    "CONFIG_INVALID": "Claude settings damaged",
    "CONFIG_UNREADABLE": "Can't read Claude settings",
    "CONFIG_TOO_LARGE": "Claude settings too big",
    "CONFIG_IS_LINK": "Settings file is a link",
    "CONFIG_CHANGED": "Settings changed: retry",
    "CONFIG_NOT_VALIDATED": "Write not confirmed: retry",
    "VAULT_FAILED": "Key not stored: retry",
    "CONFIG_BUSY": "Settings file busy: retry",
    "*": "Not connected: see Details",
}
NO_REQUEST_AFTER_S = 90.0


def run_security(argv, stdin_text):
    """macOS only: the fixed ``/usr/bin/security`` Keychain commands (ADR-0015). Secrets go through stdin
    (``security -i``), never argv. The only program the connection workflow starts, and only after the user approved."""
    import subprocess
    if not argv or argv[0] != "/usr/bin/security":
        return -1
    try:
        return subprocess.run(argv, input=stdin_text, capture_output=True, text=True, timeout=15).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


class Connector:
    def __init__(self, ext):
        self.ext = ext
        self.results = queue.Queue()
        self.state = None          # {"stage", "client", "display", ...} for the connection in progress / last one
        self.records = {}          # client id -> connection record (private storage "connections")
        self.discovered = []       # rows from connect.discover (no paths)
        self.dialog = None         # a TouchDesigner dialog is open
        self.dialog_opener = self._open_dialog          # replaceable by tests
        self._recheck = None       # (deadline, record) a later check that the entry is still in place
        self.after_note = None     # a note to show when the flow reaches connected / failed
        self.also_found = []       # display names of manual-setup agents Auto also found

    # ---- helpers ----------------------------------------------------------------------------------------
    @property
    def C(self):
        return self.ext._m.connect

    def env(self):
        return self.C.Env.current(os.environ.get("TDFLOW_CLIENT_HOME", ""))

    def vault_dir(self):
        return os.path.join(self.ext._private_root(), "connect")

    def vault(self):
        return self.C.Vault(sys.platform, self.vault_dir(), runner=run_security)

    def backup_dir(self):
        return os.path.join(self.vault_dir(), "backups")

    def display(self, cid):
        return self.ext._m.tellagent.display(self.ext.clients, cid)

    def _bg(self, name, fn, then):
        def run():
            try:
                res = fn()
                self.results.put((then, True, res))
            except Exception as e:
                code = getattr(e, "code", type(e).__name__)
                msg = getattr(e, "message", "") or "the step failed"
                self.results.put((then, False, (code, msg)))
        threading.Thread(target=run, name="tdflowmcp-connect-" + name, daemon=True).start()

    def _note(self, text, seconds=20):
        self.ext._set_note(text, seconds)

    def _set(self, stage, **kw):
        st = dict(self.state or {})
        st.update(kw, stage=stage, at=time.monotonic(), wall=time.time())
        if stage == "waiting":
            st["since"] = time.time()            # 'connected' needs a request after this point
            st["auth_fail0"] = self._auth_fails()
        self.state = st
        self.ext._ux_at = -1e9
        if stage in ("connected", "failed") and self.after_note:
            note, self.after_note = self.after_note, None
            self._note(note, 15)                 # e.g. "save the project", deferred while the flow ran
            self.ext._note_at = st["at"] + 0.001  # shown first (ties go to the stage line)

    URGENT = ("preparing", "choosing", "awaiting", "configuring", "enabling", "failed")

    def urgent(self):
        """True while a connection step is in progress or failed: Next Step shows it before anything else."""
        return bool(self.state and self.state.get("stage") in self.URGENT)

    def changed_at(self):
        return (self.state or {}).get("at", -1e9)

    def _auth_fails(self):
        t = self.ext.ctl.transport
        try:
            return int(t.stats().get("auth_fail", 0)) if t is not None else 0
        except Exception:
            return 0

    def _api(self, path, body, method="POST"):
        """Background thread: the same admin backend as the dashboard (audited, validated)."""
        ext = self.ext
        ctx = types.SimpleNamespace(path=path, method=method, body=json.dumps(body).encode("utf-8") if body else b"",
                                    principal="admin:touchdesigner", headers={}, rid="connect")
        r = ext._dashboard_api(ctx, ext.store, ext.identity)
        return r.status, json.loads((r.body or b"{}").decode("utf-8") or "{}")

    def pid(self):
        d = self.ext.storage
        return getattr(d, "project_id", "") or ""

    # ---- discovery ----------------------------------------------------------------------------------------
    def discover(self):
        try:
            self.discovered = self.C.discover(self.env())
        except Exception:
            self.discovered = []
        return [r["client"] for r in self.discovered]

    def installed_line(self):
        """Agents page 'Installed Agents': who was found and whether setup is automatic (labels only)."""
        if not self.discovered:
            return "none found"
        short = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "vscode-copilot": "Copilot"}
        auto = [short.get(r["client"], r["client"]) for r in self.discovered if r["automatic"]]
        manual = [short.get(r["client"], r["client"]) for r in self.discovered if not r["automatic"]]
        if auto:
            return (", ".join(auto) + " (auto)" + (f" +{len(manual)} more" if manual else ""))[:60]
        return (", ".join(manual) + " (manual)")[:60]

    def interfaces(self, cid):
        return next((r["interfaces"] for r in self.discovered if r["client"] == cid), [])

    def automatic(self, cid):
        if cid not in SLOTS:
            return False
        row = next((r for r in self.discovered if r["client"] == cid), None)
        return bool(row["automatic"]) if row else sys.platform in ("win32", "darwin")

    def load_records(self, text):
        try:
            v = json.loads(text or "{}")
            self.records = {k: r for k, r in v.items() if isinstance(r, dict)} if isinstance(v, dict) else {}
        except ValueError:
            self.records = {}

    def record_for_agent(self, agent_id):
        return next(((c, r) for c, r in self.records.items() if r.get("agent_id") == agent_id), (None, None))

    def connected_clients(self):
        """Client ids with a connection record whose agent is enabled (set up in this project)."""
        idn = self.ext.identity
        out = []
        for cid, r in self.records.items():
            a = idn.agents.get(r.get("agent_id")) if idn is not None else None
            if a is not None and a.enabled:
                out.append(cid)
        return out

    # ---- readiness facts ------------------------------------------------------------------------------------
    def stage_line(self):
        """Next Step while a connection is in progress or set up (None: the general readiness line applies, e.g.
        'Agent Connected — Ready' while the agent talked in the last two minutes)."""
        st, ext = self.state, self.ext
        stage = (st or {}).get("stage")
        if stage == "awaiting" and not st.get("dialog"):
            return STAGE_LINES["awaiting_pulse"]
        if stage in FLOW:
            return STAGE_LINES[stage].format(client=st.get("display", "the agent"))
        if stage == "failed":
            return st.get("line") or "Connection failed. Pulse Connect Agent to try again."
        ready = self.connected_clients()
        if not ready:
            return None
        cid = ready[0]
        rec, name = self.records[cid], self.display(cid)
        url = (rec.get("entry") or {}).get("url", "")
        if url and url != f"http://127.0.0.1:{ext.ctl.port}/mcp":
            return STAGE_LINES["port_changed"]
        seen, now = ext._seen(), time.time()
        last = max((seen.get(r.get("agent_id"), {}).get("last", 0) for r in self.records.values()), default=0)
        if last and now - last <= 120:
            return None
        if not ext.mcp_on:
            return STAGE_LINES["waiting_off"].format(client=name)
        if last and last >= getattr(ext, "_mcp_on_wall", 0):
            return STAGE_LINES["idle"].format(client=name, minutes=max(2, int((now - last) // 60)))
        if stage == "waiting" and self._auth_fails() > st.get("auth_fail0", 0):
            return STAGE_LINES["no_credential"].format(client=name)     # requests arrive, but without a valid key
        if stage == "waiting" and now - st.get("since", now) > NO_REQUEST_AFTER_S:
            return STAGE_LINES["no_request"].format(client=name)
        if self.after_note:
            return STAGE_LINES["waiting_save"].format(client=name)
        return STAGE_LINES["waiting"].format(client=name)

    # ---- Connect Agent ------------------------------------------------------------------------------------
    def start(self, selection_ids, also_found=()):
        """Main thread (Connect Agent pulse), after the environment check. ``also_found``: other agents Auto found
        that need manual setup (named in the plan, not asked about)."""
        st = self.state
        if st and st.get("stage") == "awaiting" and (st.get("dialog") or
                                                     time.monotonic() - st["at"] < CONSENT_SECONDS):
            if self.dialog is None:                      # no dialog: the second pulse is the approval
                return self.approve()
            self._note("Answer the open dialog")
            return None
        if st and st.get("stage") in ("preparing", "configuring", "enabling"):
            self._note("Still connecting…")
            return None
        self.also_found = [self.display(i) for i in also_found]
        if len(selection_ids) > 1:
            return self._choose(selection_ids)
        return self.begin(selection_ids[0])

    def _choose(self, ids):
        ids = list(ids)[:4]
        # TouchDesigner's dialog buttons are narrow (visual review): short names; the text says which are automatic.
        short = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "vscode-copilot": "Copilot"}
        names = [short.get(i, self.display(i))[:12] for i in ids]
        kinds = "\n".join(f"- {short.get(i, self.display(i))}: {'automatic setup' if self.automatic(i) else 'manual setup'}"
                           for i in ids)
        self._set("choosing", client="", display="")

        def picked(i):
            if i is None:
                self.state = None
                self._note("Nothing was connected.")
                return
            self.begin(ids[i])
        ok = self.dialog_opener("Connect a coding agent",
                                "Several coding agents were found on this computer. Which one do you want to connect "
                                "to this project?\n\n" + kinds + "\n\nYou can connect the others later (each gets its "
                                "own identity and permissions).", names + ["Cancel"], picked)
        if not ok:
            self.state = None
            self._note("Pick one in Coding Agent", 30)

    def begin(self, cid):
        ext = self.ext
        disp = self.display(cid)
        if not self.automatic(cid):
            self.state = None
            ext.native.connect_manual(cid)
            return "manual"
        self._set("preparing", client=cid, display=disp)
        if not ext.ensure_store():
            self._set("preparing", client=cid, display=disp, waiting_store=True)
            return "waiting_store"
        self._prepare()
        return "preparing"

    def _prepare(self):
        """Main thread: gather the inputs, then plan on a background thread (it reads the agent's configuration)."""
        ext, st = self.ext, self.state
        p = ext.ownerComp.par
        cid = st["client"]
        pid = self.pid()
        if not self.C.PROJECT_RE.match(pid):
            return self._fail("NO_PROJECT_ID", "Storage not ready: retry")
        role = str(p.Agentaccess.eval()) if hasattr(p, "Agentaccess") else "builder"
        access = str(p.Agentaccess.menuLabels[p.Agentaccess.menuIndex]) if hasattr(p, "Agentaccess") else role
        scope = (str(p.Allowedscope.eval()).strip() if hasattr(p, "Allowedscope") else "") or (ext._root_text + "/**")
        port, env, vault, vdir = ext.ctl.port, self.env(), self.vault(), self.vault_dir()
        rec = self.records.get(cid)
        idn = ext.identity
        agent = idn.agents.get(rec.get("agent_id")) if (rec and idn is not None) else None
        live_tokens = [t for t in idn.tokens.values() if agent is not None and t.agent_id == agent.id and
                       not t.revoked_at] if idn is not None else []
        slot = SLOTS[cid]
        C = self.C

        def plan():
            entry = C.claude_entry(sys.platform, port, slot, vdir)
            path = C.claude_config_path(env)
            pl = C.plan_claude(path, entry, env)
            has = vault.has(slot, pid)
            return pl, has
        st.update(role=role, access=access, scope=scope, pid=pid, port=port, agent=agent and agent.id,
                  agent_name=agent and agent.display_name, live=len(live_tokens))
        self._bg("plan", plan, self._planned)

    def _planned(self, ok, res):
        if not ok:
            code, msg = res
            return self._fail(code, FAIL_LINES.get(code, FAIL_LINES["*"]))
        pl, has_cred = res
        st = self.state
        ext = self.ext
        intact = st.get("agent") and st.get("live") and has_cred and pl.action == "unchanged"
        enable = not ext.mcp_on
        st["plan"], st["intact"], st["enable"] = pl, bool(intact), enable
        if intact:
            if not enable:
                self._set("waiting", **{})
                return
            text = (f"{st['display']} is already set up for this project (agent '{st['agent_name']}').\n\n"
                    "Enable MCP is TURNED ON so it can connect (this computer only; a live security check runs "
                    "first). Nothing else changes.\n\nTurn it off or Suspend Agents at any time.")
            buttons = ["Enable MCP", "Cancel"]
        else:
            text = self.C.consent_text(st["display"], self.interfaces(st["client"]), pl, st["access"], st["scope"],
                                       enable, reuse_agent=st.get("agent_name") or "", platform=sys.platform,
                                       also_found=self.also_found)
            buttons = ["Approve", "Cancel"]          # narrow buttons (visual review); the text says MCP is turned on
        st["consent"] = text
        self._show_details(text)
        self._set("awaiting")
        ok = self.dialog_opener(f"Connect {st['display']}", text, buttons,
                                lambda i: self.approve() if i == 0 else self.cancel())
        if ok:
            self.state["dialog"] = True              # no expiry while the user is looking at it
        else:
            self._show_details(text, open_viewer=True)
            self._note("Review, then pulse again.", 60)

    def approve(self):
        """Main thread: the user approved exactly the plan shown."""
        st, ext = self.state, self.ext
        self.dialog = None
        self._clear_view()
        if not st or st.get("stage") != "awaiting" or (not st.get("dialog") and
                                                       time.monotonic() - st["at"] > CONSENT_SECONDS):
            self.state = None
            self._note("Expired: pulse Connect Agent")
            return None
        store = ext.store
        if st.get("intact"):
            self._set("configuring")
            cid = st["client"]

            def record():                                # review 19 (17): durable before MCP is turned on
                store.append("admin", tool="client_connect", outcome="enable_only",
                             detail={"client": cid, "via": "touchdesigner"})
                return None

            def recorded(ok, res):
                if not ok:
                    return self._fail(str(res[0]), "Not recorded: retry")
                self._set("enabling")
                self._enable()
            self._bg("record", record, recorded)
            return "enabling"
        self._set("configuring")
        C, pl = self.C, st["plan"]
        env, vault, bdir = self.env(), self.vault(), self.backup_dir()
        cid, slot, pid = st["client"], SLOTS[st["client"]], st["pid"]
        role_label = {"observer": "Read Only", "builder": "Builder", "operator": "Operator"}.get(st["role"], st["role"])
        name = f"{st['display']} ({role_label})"[:64]
        reuse = st.get("agent")
        old = dict(self.records.get(cid) or {})

        def configure():
            store.append("admin", tool="client_connect", outcome="intent",
                         detail={"client": cid, "config": pl.shown_path, "action": pl.action, "reuse_agent": reuse or "",
                                 "role": st["role"], "scope": st["scope"], "via": "touchdesigner"})
            key = ext._connect_key
            if not key:                                    # this project's connect key (the helper's proof)
                key = secrets.token_hex(32)
                store.set_private_setting("connect_key", key)
            ext._connect_key = key
            if reuse:
                s, d = self._api("/api/agents/rotate", {"agent_id": reuse})
            else:
                s, d = self._api("/api/tokens", {"display_name": name, "role": st["role"], "scope": st["scope"]})
            if s != 200 or not d.get("token"):
                raise C.ConnectError(str(d.get("code") or s), "the agent identity could not be created")
            aid = reuse or d["agent"]["id"]
            token = d.pop("token")
            res = None
            try:
                vault.put(slot, pid, token, key)
                token = None
                res = C.apply_claude(pl, env, bdir)
                previous = old.get("previous_entry") if old.get("replaced") else res.get("previous_entry")
                rec = {"client": cid, "agent_id": aid, "slot": slot, "pid": pid, "path": pl.path,
                       "shown_path": pl.shown_path, "entry": pl.entry, "action": res["action"],
                       "backup": res.get("backup", ""),
                       "created_key": bool(res.get("created_key") or old.get("created_key")),
                       "replaced": previous is not None, "previous_entry": previous,
                       "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                recs = dict(self.records)
                recs[cid] = rec
                store.set_private_setting("connections", json.dumps(recs))
                store.append("admin", tool="client_connect", outcome="ok", agent_id=aid,
                             detail={"client": cid, "config": pl.shown_path, "action": res["action"],
                                     "backup": res.get("backup", ""), "credential": "os_protected_store"})
            except Exception as e:
                # Review 19 (5): anything after the credential exists is rolled back: credential, key file, and the
                # entry this run wrote (unless it was already there).
                token = None
                self._rollback(aid, new=not reuse, slot=slot, pid=pid, vault=vault)
                if res is not None and res.get("action") in ("add", "create_file", "replace", "update"):
                    try:
                        C.remove_claude(pl.path, env, bdir, pl.entry, restore_entry=res.get("previous_entry"),
                                        drop_empty_key=bool(res.get("created_key")))
                    except Exception:
                        pass
                try:
                    store.append("admin", tool="client_connect", outcome="failed", agent_id=aid,
                                 error_code=getattr(e, "code", type(e).__name__), detail={"client": cid})
                except Exception:
                    pass
                raise
            return recs, rec
        self._bg("configure", configure, self._configured)
        return "configuring"

    def _rollback(self, aid, new, slot, pid, vault):
        """Background thread: undo a half-finished connection (the credential must not stay valid). Returns the
        steps that failed (review 19: statuses are checked)."""
        failed = []
        try:
            vault.delete(slot, pid)
        except Exception:
            failed.append("credential")
        try:
            if self._api("/api/agents/rotate", {"agent_id": aid, "revoke_only": True})[0] != 200:
                failed.append("revoke")
            if new and self._api("/api/agents/state", {"agent_id": aid, "enabled": False})[0] != 200:
                failed.append("disable")
        except Exception:
            failed.append("revoke")
        return failed

    def _configured(self, ok, res):
        st = self.state
        if not ok:
            code, msg = res
            return self._fail(code, FAIL_LINES.get(code, FAIL_LINES["*"]))
        self.records, rec = res
        st["record"] = rec
        self._recheck = (time.monotonic() + RECHECK_SECONDS, rec)
        if st.get("enable"):
            self._set("enabling")
            self._enable()
        else:
            self._set("waiting")
        self.ext._refresh_tell()

    def _enable(self):
        p = self.ext.ownerComp.par
        if not p.Enablemcp.eval():
            p.Enablemcp = True                       # the user approved this exact step; the usual checks run

    def cancel(self):
        self.dialog = None
        self._clear_view()
        if self.state and self.state.get("stage") in ("awaiting", "choosing"):
            self.state = None
            self.ext._audit_async("admin", tool="client_connect", outcome="cancelled", detail={"via": "touchdesigner"})
            self._note("Cancelled; nothing changed")

    def _fail(self, code, line):
        ext = self.ext
        note_fresh = bool(ext.note) and time.monotonic() < ext._note_until and time.monotonic() - ext._note_at < 2
        self._set("failed", line=line, code=code)
        if note_fresh:                       # a specific reason was just given (e.g. why MCP stayed off): show it first
            ext._note_at = self.state["at"] + 0.001
        ext._refresh_tell()

    # ---- Agents page: disconnect, repair, details -----------------------------------------------------------
    def disconnect(self, agent_id, confirmed=False):
        cid, rec = self.record_for_agent(agent_id)
        if rec is None:
            self._note("Not set up by Connect Agent")
            return None
        if not confirmed:
            text = (f"Disconnect {self.display(cid)} from this project?\n\n"
                    "- Its tokens are revoked and its stored key is deleted (it stays listed, disabled, for the audit "
                    "history).\n"
                    f"- {rec.get('shown_path')}: the \"tdflow\" server is removed"
                    + (" and the one it replaced is put back" if rec.get("replaced") else "")
                    + " (only if it is still TDflowMCP's, and kept while another project still uses it).")
            if self.dialog_opener("Disconnect agent", text, ["Disconnect", "Cancel"],
                                  lambda i: self.disconnect(agent_id, True) if i == 0 else None):
                return "confirm"
            pend = getattr(self, "_disc_pending", None)  # review 19 (16): without a dialog, a second pulse confirms
            if not pend or pend[0] != agent_id or time.monotonic() - pend[1] > CONSENT_SECONDS:
                self._disc_pending = (agent_id, time.monotonic())
                self._show_details(text, open_viewer=True)
                self._note("Pulse again to confirm", 60)
                return "confirm"
        self._disc_pending = None
        self._clear_view()
        C, env, vault, bdir, store, ext = self.C, self.env(), self.vault(), self.backup_dir(), self.ext.store, self.ext

        def run():
            store.append("admin", tool="client_disconnect", outcome="intent", agent_id=agent_id,
                         detail={"client": cid, "config": rec.get("shown_path"), "via": "touchdesigner"})
            # Review 19 (4): cut the agent off first, checking each step; the client file is best effort afterwards.
            problems = []
            if self._api("/api/agents/rotate", {"agent_id": agent_id, "revoke_only": True})[0] != 200:
                problems.append("revoke")
            if self._api("/api/agents/state", {"agent_id": agent_id, "enabled": False})[0] != 200:
                problems.append("disable")
            try:
                vault.delete(rec["slot"], rec["pid"])
            except Exception:
                problems.append("credential")
            others = vault.others(rec["slot"], rec["pid"])
            res = {"action": "kept_for_others", "others": others}
            if not others:
                try:
                    res = C.remove_claude(rec["path"], env, bdir, rec.get("entry") or {},
                                          restore_entry=rec.get("previous_entry") if rec.get("replaced") else None,
                                          drop_empty_key=bool(rec.get("created_key")))
                except Exception as e:
                    problems.append("config")
                    res = {"action": "config_failed", "code": getattr(e, "code", type(e).__name__)}
            recs = {k: v for k, v in self.records.items() if k != cid}
            store.set_private_setting("connections", json.dumps(recs))
            if not recs:
                store.set_private_setting("connect_key", "")
                ext._connect_key = None
            store.append("admin", tool="client_disconnect", outcome="ok" if not problems else "partial",
                         agent_id=agent_id, error_code=",".join(problems) or None,
                         detail={"client": cid, "config_action": res["action"]})
            return recs, res, problems

        def done(ok, r):
            if not ok:
                self._note(f"Disconnect failed ({r[0]})"[:40], 30)
                return
            self.records, res, problems = r
            if self.state and self.state.get("client") == cid:
                self.state = None
            if problems:
                self._note(f"Partly done ({','.join(problems)}): retry"[:40], 60)
                return
            self._note({"restored": "Disconnected; old entry back", "not_ours": "Disconnected; your entry kept",
                        "kept_for_others": "Disconnected (entry shared)"}.get(res["action"], "Disconnected."), 30)
            self.ext._refresh_tell()
        self._bg("disconnect", run, done)
        return "disconnecting"

    def details_text(self):
        """Advanced > View Connection: the technical details (no secrets)."""
        lines = ["TDflowMCP connection details (no secrets)", ""]
        if not self.records:
            lines.append("No agent was connected with Connect Agent in this project.")
        for cid, r in self.records.items():
            lines += [f"{self.display(cid)}: agent {r.get('agent_id')}, connected {r.get('at')}",
                      f"  Configuration: {r.get('shown_path')} (user scope), MCP server 'tdflow'",
                      f"  URL: {(r.get('entry') or {}).get('url')}",
                      "  Credential: OS-protected store ("
                      + ("Windows DPAPI, current user" if sys.platform == "win32" else "macOS Keychain") + ")",
                      "  headersHelper (fixed command, contains no secret; the listener must prove this project's key "
                      "before it releases the token):",
                      "    " + self._redact_path(str((r.get("entry") or {}).get("headersHelper", ""))),
                      f"  Backup of the file before the change: {r.get('backup') or 'none (the file did not exist)'}"
                      " (TDflowMCP private folder)", ""]
        st = self.state
        if st:
            lines += [f"Current step: {st.get('stage')} {st.get('code') or ''}".rstrip()]
            if st.get("consent"):
                lines += ["", "Last plan shown:", st["consent"]]
        lines += ["", "Undo: Agents page > select the agent > Disconnect.",
                  "Manual fallback (any client): Agents page > New Token, then Copy Token; see View Full Instructions."]
        return "\n".join(lines)

    def _redact_path(self, text):
        """Review 19 (14): the private folder is shown in %LOCALAPPDATA% form, never with the user name."""
        root = self.ext._private_root()
        loc = os.environ.get("LOCALAPPDATA") or ""
        if loc and root.lower().startswith(loc.lower()):
            return text.replace(root, "%LOCALAPPDATA%" + root[len(loc):])
        return text.replace(root, "<TDflowMCP private folder>")

    def _clear_view(self):
        d = self.ext.ownerComp.op("connect_view")
        if d is not None and d.text:
            d.text = ""
        self._view_until = None

    def _show_details(self, text, open_viewer=False):
        d = self.ext.ownerComp.op("connect_view")
        self._view_until = time.monotonic() + VIEW_SECONDS      # never kept in the saved project
        if d is not None:
            d.text = text
            if open_viewer:
                try:
                    d.openViewer()
                except Exception:
                    pass

    # ---- TouchDesigner dialog ---------------------------------------------------------------------------
    def _open_dialog(self, title, text, buttons, on_choice):
        """TouchDesigner's own pop-up dialog (TDResources PopDialog). Returns False if it is unavailable."""
        try:
            pop = td.op.TDResources.PopDialog
        except Exception:
            return False

        def cb(info):
            self.dialog = None
            try:
                n = int(info.get("buttonNum") or 0) - 1
            except (TypeError, ValueError, AttributeError):
                n = -1
            on_choice(n if 0 <= n < len(buttons) - 1 else None)      # the last button is always Cancel
        try:
            pop.OpenDefault(text=text, title=title, buttons=list(buttons), callback=cb, escButton=len(buttons),
                            escOnClickAway=False, enterButton=len(buttons))
        except Exception:
            return False
        self.dialog = title
        return True

    # ---- every frame (main thread) ---------------------------------------------------------------------------
    def tick(self):
        vu = getattr(self, "_view_until", None)
        if vu and time.monotonic() > vu:
            self._clear_view()
        while True:
            try:
                then, ok, res = self.results.get_nowait()
            except queue.Empty:
                break
            try:
                then(ok, res)
            except Exception as e:
                self._note(f"Step failed ({type(e).__name__})"[:40])
        st, ext, now = self.state, self.ext, time.monotonic()
        if not st:
            return
        stage = st.get("stage")
        if stage == "preparing" and st.get("waiting_store"):
            if ext.store is not None and ext.store.available and ext.identity is not None:
                st.pop("waiting_store", None)
                self._prepare()
            elif ext.worker is not None and ext.worker.ready.is_set() and not ext.worker.available:
                self._fail("STORE", "Storage issue: Data Status")
        elif stage == "awaiting" and not st.get("dialog") and now - st["at"] > CONSENT_SECONDS:
            self.state = None
            self._note("Expired; nothing changed")
        elif stage == "enabling":
            if ext.mcp_on:
                self._set("waiting")
            elif not ext._mcp_wanted and not ext._enabling and now - st["at"] > 1.0:
                self._fail("ENABLE_REFUSED", "MCP off: see Diagnostics")
        elif stage == "failed":
            # A failure stops showing once things work again: MCP turned on after a refused enable, or the agent talks.
            rec = self.records.get(st.get("client")) or {}
            seen = ext._seen().get(rec.get("agent_id")) or {}
            if (st.get("code") == "ENABLE_REFUSED" and ext.mcp_on) or seen.get("last", 0) > st.get("wall", 0):
                self.state = None
        elif stage == "waiting":
            rec = st.get("record") or self.records.get(st.get("client"), {})
            seen = ext._seen().get(rec.get("agent_id"))
            if seen and seen["last"] >= st.get("since", 0) and time.time() - seen["last"] <= 120:
                self._set("connected")
                ext._audit_async("admin", tool="client_connect", outcome="verified", agent_id=rec.get("agent_id"),
                                 detail={"client": st.get("client"), "client_name": seen.get("client", ""),
                                         "client_version": seen.get("version", "")})
        rc = self._recheck
        if rc and now > rc[0]:
            self._recheck = None
            rec, C = rc[1], self.C

            def check():
                return C.still_configured(rec["path"], rec["entry"])

            def done(ok, res):
                if ok and res is False:
                    self._fail("ENTRY_REMOVED", "Entry removed: Repair")
            self._bg("recheck", check, done)
