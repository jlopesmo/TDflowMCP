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
DIALOG_CHARS = 25              # UX-08: characters per line TouchDesigner's dialog shows (visual review)
VIEW_SECONDS = 120.0           # connect_view is cleared after this (it is saved with the .toe: review 19, finding 14)
RECHECK_SECONDS = 6.0
SLOTS = {"claude-code": "claude-code", "codex": "codex"}     # client id -> vault slot (one credential per client)
SHORT = {"claude-code": "Claude", "codex": "Codex"}           # in status lines of at most 26 characters
STAGE_LINES = {                       # Next Step; every line fits the default parameter width (~60 characters)
    "preparing": "Preparing connection…",
    "choosing": "Choose an agent (dialog)",
    "awaiting": "Approve in the dialog.",
    "awaiting_pulse": "Review, then pulse again.",
    "configuring": "Configuring {client}…",
    "enabling": "Turning on MCP…",
    "waiting": "Start a new {who} chat.",
    "waiting_save": "Save, then start a chat.",
    "waiting_off": "Turn on Enable MCP.",
    "idle": "Connected · idle {minutes} min",
    "no_request": "No request: /mcp reconnect",
    "no_credential": "No key: Repair Connection",
    "port_changed": "Port changed: pulse Repair",
}
# Lines that differ per client (usability review, ADR-0017): /mcp is a Claude Code command; a Codex that sends no key
# right after the key was stored is one that does not run the header helper, which Repair cannot fix.
STAGE_LINES_FOR = {"codex": {"no_request": "No request: new Codex chat", "no_credential": "No key sent: update Codex"}}
FLOW = ("preparing", "choosing", "awaiting", "configuring", "enabling")
FAIL_LINES = {                          # Next Step after a failed step (the reason is in View Connection Details)
    "CONFIG_INVALID": "Error in {who} settings",
    "CONFIG_UNREADABLE": "Can't read {who} settings",
    "CONFIG_TOO_LARGE": "{who} settings too big",
    "CONFIG_UNSUPPORTED": "Can't edit {who} settings",
    "PLATFORM_UNSUPPORTED": "Not on this OS: manual",
    "CONFIG_IS_LINK": "Settings file is a link",
    "CONFIG_CHANGED": "Settings changed: retry",
    "CONFIG_NOT_VALIDATED": "Write not confirmed: retry",
    "VAULT_FAILED": "Key not stored: retry",
    "CONFIG_BUSY": "Settings file busy: retry",
    "*": "Not connected: see Details",
}
NO_REQUEST_AFTER_S = 90.0
# What a failed step means and the one thing to do next (View Connection Details; usability review). {name}: the
# agent, {path}: its settings file without the user name.
FAIL_DETAILS = {
    "CONFIG_INVALID": ("Why: {name}'s settings file ({path}) has an error in it, so TDflowMCP did not touch it. {name} "
                       "normally reports the same error when it starts.\nWhat to do: fix or remove the line {name} "
                       "complains about, then pulse Connect Agent again."),
    "CONFIG_UNSUPPORTED": ("Why: {name}'s settings file ({path}) is valid, but its \"tdflow\" entry or its list of "
                           "servers is written in a form TDflowMCP does not edit (for example on one line).\nWhat to "
                           "do: if you added a \"tdflow\" server to {name} yourself, remove it (for Codex, in a "
                           "terminal: codex mcp remove tdflow), then pulse Connect Agent again."),
    "CONFIG_IS_LINK": ("Why: {name}'s settings file ({path}) is a link to another file, which TDflowMCP never "
                       "replaces.\nWhat to do: connect this agent with the manual steps in View Full Instructions."),
    "CONFIG_CHANGED": ("Why: {name}'s settings file changed while you were reviewing the plan.\nWhat to do: pulse "
                       "Connect Agent again and review the new plan."),
    "CONFIG_BUSY": ("Why: another program had {name}'s settings file open.\nWhat to do: pulse Connect Agent again."),
}


def wrap_dialog(text, width=None):
    """UX-08: wrap each paragraph to ``DIALOG_CHARS``; list items keep a hanging indent."""
    import textwrap
    width = width or DIALOG_CHARS
    out = []
    for line in str(text).split("\n"):
        if len(line) <= width:
            out.append(line)
            continue
        lead = len(line) - len(line.lstrip(" -"))
        out += textwrap.wrap(line, width=width, subsequent_indent=" " * min(lead, 4), break_long_words=True,
                             break_on_hyphens=False)
    return "\n".join(out)


def installed_text(rows, width=26):
    """The Installed Agents value from discovery rows, within the 26 characters the parameter window shows:
    ``Auto: Claude Code, Codex``; with agents that need manual setup ``Auto: Claude, Codex +1``; none automatic
    ``Manual: Cursor, Copilot``. A Codex extension that is too old for automatic setup says so."""
    long = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "vscode-copilot": "Copilot"}
    brief = dict(long, **{"claude-code": "Claude"})
    auto = [r for r in rows if r.get("automatic")]
    manual = [r for r in rows if not r.get("automatic")]

    def names(rs, table):
        return [table.get(r["client"], r["client"]) + (" (too old)" if r.get("manual_why") else "") for r in rs]
    if auto:
        more = f" +{len(manual)}" if manual else ""
        for table in (long, brief):
            text = "Auto: " + ", ".join(names(auto, table)) + more
            if len(text) <= width:
                return text
        return text[:width]
    for n in range(len(manual), 0, -1):
        rest = f" +{len(manual) - n}" if n < len(manual) else ""
        for table in (long, brief):
            text = "Manual: " + ", ".join(names(manual[:n], table)) + rest
            if len(text) <= width:
                return text
    return text[:width]


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
        self._working = None       # "configure" | "disconnect": one background change of the records at a time
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
        return installed_text(self.discovered)

    def interfaces(self, cid):
        return next((r["interfaces"] for r in self.discovered if r["client"] == cid), [])

    def automatic(self, cid):
        if cid not in SLOTS:
            return False
        row = next((r for r in self.discovered if r["client"] == cid), None)
        return bool(row["automatic"]) if row else sys.platform in self.C.PLATFORMS.get(cid, ())

    def fail_line(self, code):
        who = SHORT.get((self.state or {}).get("client"), "Agent")
        return FAIL_LINES.get(code, FAIL_LINES["*"]).format(who=who)

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
            return STAGE_LINES[stage].format(client=st.get("display", "the agent"),
                                             who=SHORT.get(st.get("client"), "agent"))
        if stage == "failed":
            return st.get("line") or "Connection failed. Pulse Connect Agent to try again."
        ready = self.connected_clients()
        if not ready:
            return None
        # Several clients can be set up (each with its own agent): the line speaks about the one being connected,
        # else the one chosen in Coding Agent, else the first.
        chosen = [i for i in ext.native.selected() if i in ready] if getattr(ext, "native", None) else []
        cid = st["client"] if st and st.get("client") in ready else (chosen[0] if len(chosen) == 1 else ready[0])
        rec, name = self.records[cid], self.display(cid)
        for r in (self.records[i] for i in ready):
            url = (r.get("entry") or {}).get("url", "")
            if url and url != f"http://127.0.0.1:{ext.ctl.port}/mcp":
                return STAGE_LINES["port_changed"]
        seen, now = ext._seen(), time.time()
        # While one client is being connected, only its own agent's requests count (another client's agent may be
        # talking at the same time).
        watched = [rec] if stage == "waiting" else list(self.records.values())
        last = max((seen.get(r.get("agent_id"), {}).get("last", 0) for r in watched), default=0)
        if last and now - last <= 120:
            return None
        def line(key, **kw):
            text = STAGE_LINES_FOR.get(cid, {}).get(key) or STAGE_LINES[key]
            return text.format(client=name, who=SHORT.get(cid, "agent"), **kw)
        if not ext.mcp_on:
            return line("waiting_off")
        if last and last >= getattr(ext, "_mcp_on_wall", 0):
            return line("idle", minutes=max(2, int((now - last) // 60)))
        if stage == "waiting" and self._auth_fails() > st.get("auth_fail0", 0):
            return line("no_credential")                 # requests arrive, but without a valid key
        if stage == "waiting" and now - st.get("since", now) > NO_REQUEST_AFTER_S:
            return line("no_request")
        if self.after_note:
            return line("waiting_save")
        return line("waiting")

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
        # Visual review (ADR-0017): TouchDesigner's dialog buttons show about seven characters ("Claude Code" was cut
        # off on both sides), so the buttons carry short names and the text carries the full ones.
        names = [dict(short, **{"claude-code": "Claude"}).get(i, self.display(i))[:7] for i in ids]
        done = set(self.connected_clients())
        if all(self.automatic(i) for i in ids):
            # ADR-0017 (usability review): several agents with automatic setup. Lines of at most 24 characters; an
            # agent that is already connected is named as such.
            count = {2: "Two"}.get(len(ids), "Several")
            listed = "".join(f"- {short.get(i, self.display(i))}: connected\n" for i in ids if i in done)
            text = (f"{count} agents can be set up automatically. Which one should work in this project?\n\n"
                    + (listed + "\n" if listed else "")
                    + "Nothing changes yet: the plan comes next. You can connect the other later.")
        else:
            kinds = "\n".join(f"- {short.get(i, self.display(i))}: "
                              f"{'connected' if i in done else 'automatic' if self.automatic(i) else 'manual steps'}"
                              for i in ids)
            text = ("Several coding agents were found on this computer. Which one do you want to connect to this "
                    "project?\n\n" + kinds + "\n\nNothing changes yet. You can connect the others later.")
        self._set("choosing", client="", display="")

        def picked(i):
            if i is None:
                self.state = None
                self._note("Nothing was connected.")
                return
            self.begin(ids[i])
        ok = self.dialog_opener("Connect a coding agent", text, names + ["Cancel"], picked)
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
        if agent is not None and not agent.enabled:
            agent = None                           # review 20: a disabled agent is not reused (a new one is created)
        live_tokens = [t for t in idn.tokens.values() if agent is not None and t.agent_id == agent.id and
                       not t.revoked_at] if idn is not None else []
        slot = SLOTS[cid]
        C = self.C

        def plan():
            entry = C.client_entry(cid, sys.platform, port, slot, vdir)
            path = C.client_config_path(cid, env)
            pl = C.plan_client(cid, path, entry, env)
            has = vault.has(slot, pid)
            return pl, has
        st.update(role=role, access=access, scope=scope, pid=pid, port=port, agent=agent and agent.id,
                  agent_name=agent and agent.display_name, live=len(live_tokens))
        self._bg("plan", plan, self._planned)

    def _planned(self, ok, res):
        if not ok:
            code, msg = res
            return self._fail(code, self.fail_line(code))
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
            text = (f"{st['display']} is already set up for this project.\n\nTurns ON Enable MCP (this "
                    f"computer only). Nothing else changes.\n\nThen start a new {st['display']} chat.")
            buttons = ["Enable MCP", "Cancel"]
        else:
            text = self.C.consent_text(st["display"], self.interfaces(st["client"]), pl, st["access"], st["scope"],
                                       enable, reuse_agent=st.get("agent_name") or "", platform=sys.platform,
                                       also_found=self.also_found)
            buttons = ["Approve", "Cancel"]          # narrow buttons (visual review); the text says MCP is turned on
        st["consent"] = text
        self._show_details(text)                   # the full plan, readable in the viewer
        short = text if st.get("intact") else self.C.consent_short(
            st["display"], pl, st["access"], st["scope"], enable, reuse_agent=st.get("agent_name") or "",
            platform=sys.platform, also_found=self.also_found)
        st["consent_short"] = short
        self._set("awaiting")
        ok = self.dialog_opener(f"Connect {st['display']}", short, buttons,
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
        if self._working:
            # Review 23 (10): a Disconnect of another agent is still writing the connection records. The plan stays
            # approved-pending; the next Connect Agent pulse applies it.
            self._note("Busy: pulse again", 20)
            return None
        self._working = "configure"
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
                # Review 20: record the connection as pending before the credential exists anywhere else, so a kill
                # at any later point leaves something Repair Connection and Disconnect Agent can find.
                pending = dict(old, client=cid, agent_id=aid, slot=slot, pid=pid, path=pl.path,
                               shown_path=pl.shown_path, entry=pl.entry, pending=True,
                               at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                if pl.action == "replace" and pl.previous_entry is not None:
                    pending.update(replaced=True, previous_entry=pl.previous_entry)   # review 23 (9): survives a kill
                store.set_private_setting("connections", json.dumps(dict(self.records, **{cid: pending})))
                vault.put(slot, pid, token, key)
                token = None
                res = C.apply_client(pl, env, bdir)
                # Review 23 (9): what Disconnect has to undo comes from the run that put the entry there. A run that
                # replaced puts back what it replaced; a run that added has nothing to put back; a run that only
                # updated or found it unchanged (Repair) keeps the earlier facts.
                fresh = res["action"] in ("add", "create_file", "replace")
                facts = res if fresh else old
                previous = res.get("previous_entry") if res["action"] == "replace" else None if fresh else (
                    old.get("previous_entry") if old.get("replaced") else None)
                rec = {"client": cid, "agent_id": aid, "slot": slot, "pid": pid, "path": pl.path,
                       "shown_path": pl.shown_path, "entry": pl.entry, "action": res["action"],
                       "backup": res.get("backup", "") or old.get("backup", ""),
                       "created_key": bool(facts.get("created_key")),
                       "created_file": bool(facts.get("created_file")),
                       "added_newline": bool(facts.get("added_newline")),
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
                try:                                   # the pending record goes too (the previous one stays)
                    store.set_private_setting("connections", json.dumps(self.records))
                except Exception:
                    pass
                # An entry that was already there and only updated stays (ADR-0015: "the entry that this run
                # wrote"); its key is revoked above, and Repair Connection gives it a new one.
                if res is not None and res.get("action") in ("add", "create_file", "replace"):
                    try:
                        C.remove_client(cid, pl.path, env, bdir, pl.entry, res)
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
        self._working = None
        st = self.state
        if not ok:
            code, msg = res
            return self._fail(code, self.fail_line(code))
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
        if self._working:
            self._note("Busy: pulse again", 20)          # review 23 (10): never while another agent is being set up
            return None
        self._working = "disconnect"
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
                    res = C.remove_client(cid, rec["path"], env, bdir, rec.get("entry") or {}, rec)
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
            self._working = None
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
                      f"  Configuration: {r.get('shown_path')}{' (user scope)' if cid == 'claude-code' else ''}, "
                      "MCP server 'tdflow'",
                      f"  URL: {(r.get('entry') or {}).get('url')}",
                      "  Credential: OS-protected store ("
                      + ("Windows DPAPI, current user" if sys.platform == "win32" else "macOS Keychain") + ")",
                      "  Headers helper (fixed command, contains no secret; the listener must prove this project's key "
                      "before it releases the token):",
                      "    " + self._redact_path(self.C.helper_text(r.get("entry"))),
                      f"  Backup of the file before the change: {r.get('backup') or 'none (the file did not exist)'}"
                      " (TDflowMCP private folder)"]
            if cid == "codex":
                lines += ["  If Codex shows tdflow as failed with HTTP 401 after Repair Connection, this Codex is too old "
                          "to fetch the key (it does not run http_headers_helper). Update Codex (tested: CLI 0.160.0, "
                          "VS Code extension 26.1120), then pulse Repair Connection.",
                          "  Codex cloud tasks and remote sessions cannot use this connection."]
            lines.append("")
        st = self.state
        if st and st.get("stage") == "failed":
            # Usability review: say what happened, that nothing was changed, and the one thing to do next.
            name = st.get("display") or "The agent"
            path = getattr(st.get("plan"), "shown_path", "") or {"codex": "~/.codex/config.toml",
                                                                "claude-code": "~/.claude.json"}.get(st.get("client"), "")
            why = FAIL_DETAILS.get(st.get("code"))
            lines += [f"{name}: not connected. Nothing was changed.",
                      why.format(name=name, path=path) if why else "What to do: pulse Connect Agent to try again.",
                      f"[{st.get('code') or 'failed'}]"]
        elif st:
            lines += [f"Current step: {st.get('stage')}"]
        if st and st.get("consent"):
            lines += ["", "Last plan shown:", st["consent"]]
        lines += ["", "Undo: Agents page > select the agent > Disconnect Agent.",
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
    def close_dialog(self):
        """Shutdown hook (kept for compatibility): TouchDesigner's shared dialog is not modified by TDflowMCP."""
        return None

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
        # Review 20 (4): one TDflowMCP dialog at a time (TouchDesigner has one shared pop-up); the caller then takes
        # its no-dialog path.
        if self.dialog is not None:
            return False
        # UX-08 (rc4, visual review): TouchDesigner's dialog is a fixed 260 px and lays out its text and buttons for
        # that size (widening it clipped the buttons). The text is pre-wrapped to lines that fit, so it breaks where
        # intended instead of mid-phrase.
        try:
            pop.OpenDefault(text=wrap_dialog(text), title=title, buttons=list(buttons), callback=cb,
                            escButton=len(buttons), escOnClickAway=False, enterButton=len(buttons))
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
                return C.client_still_configured(rec.get("client", ""), rec["path"], rec["entry"])

            def done(ok, res):
                if ok and res is False:
                    self._fail("ENTRY_REMOVED", "Entry removed: Repair")
            self._bg("recheck", check, done)
