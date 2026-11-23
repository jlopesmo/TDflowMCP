"""TDflowMCPExt: the TouchDesigner extension of TDflowMCP.tox (TD adapter, Phases 1A + 1B + audit worker).

Threads (ADR-0006 revision, audit latency gate):
- TouchDesigner's main thread: extension init, parameter callbacks, the per-frame ``OnFrame``, ``Shutdown``.
  It never waits for disk I/O: every audit write is *submitted* to the audit worker and its durable
  confirmation is polled in ``OnFrame`` (``_after``). Actions that need a durable record (pairing approval,
  Enable MCP, resuming agents) take effect only after that confirmation; anything else fails closed.
- HTTP handler threads: the MCP and admin API route handlers (``_route_mcp``, ``_route_api``). They never
  touch TouchDesigner: TD steps run through ``RouteContext.call_main`` (executed by ``pump`` in ``OnFrame``)
  and they wait for audit I/O on the worker (durable intent before executing, durable outcome before
  reporting success).
- The audit worker thread: storage resolution (project-local ``.tdflowmcp/`` or private fallback), the only
  SQLite connections, FIFO writes (hash chain order = submission order).

Only TD-free work runs off the main thread: the HTTP transport, evidence collection, the port-owner probe,
the browser launcher, ``_auth`` (immutable snapshots) and the route handlers above.
"""
import collections
import dataclasses
import hashlib
import json
import os
import socket
import sys
import threading
import time
import types
import webbrowser

import td

_REGISTRY = sys.modules.setdefault("_tdflowmcp_runtime", types.ModuleType("_tdflowmcp_runtime"))
if not hasattr(_REGISTRY, "instances"):
    _REGISTRY.instances = {}
if not hasattr(_REGISTRY, "closing"):
    _REGISTRY.closing = []          # transports of shut-down instances that may still be closing

MAX_ADMIN_BODY = 16 * 1024
STORE_OPEN_TIMEOUT = 10.0
CONFIRM_TIMEOUT = 3.0               # durable confirmation of an administrative action


class StorageNeedsUser(Exception):
    """The storage decision needs an explicit user choice (moved / copied / relocated / legacy data)."""


class TDflowMCPExt:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self._main = threading.main_thread()
        self.manifest = ownerComp.op("loader").module.install_from_comp(ownerComp)
        from tdflowmcp import SKILL_VERSION, __version__, admin, auditworker, compat, controller, engine, identity, \
            policy, protocol, setupapi, skills, storage, store, tellagent, tools
        from tdflowmcp import memory, memrepo
        from tdflowmcp.net import bindverify, evidence, oslisteners, oswindow
        from tdflowmcp.net.pyhttp import EmbeddedHTTPTransport, MainStepError, Response, error
        from tdflowmcp_td import tdops
        self._m = types.SimpleNamespace(compat=compat, controller=controller, setupapi=setupapi, tellagent=tellagent,
                                        evidence=evidence, oslisteners=oslisteners, bindverify=bindverify,
                                        Transport=EmbeddedHTTPTransport, Response=Response, error=error, admin=admin,
                                        engine=engine, identity=identity, policy=policy, protocol=protocol,
                                        store=store, tools=tools, tdops=tdops, auditworker=auditworker,
                                        storage=storage, MainStepError=MainStepError, skills=skills,
                                        memory=memory, memrepo=memrepo, oswindow=oswindow)
        self.skill_version = SKILL_VERSION
        self.version = __version__
        old = _REGISTRY.instances.get(ownerComp.id)
        if old is not None and old is not self:
            try:
                old.Shutdown("reinit")
            except Exception:
                pass
        _REGISTRY.instances[ownerComp.id] = self
        self.troubleshooting = json.loads(ownerComp.op("troubleshooting").text)
        self.clients = json.loads(ownerComp.op("clients").text)
        self._skill_bundle, self._skill_expected = self._load_skill_bundle()
        self._project_folder = str(td.project.folder or "")
        self.td_build = str(td.app.build)
        self.checks, self.checks_at = [], 0.0
        self.dispatch_threads = {"main": 0, "other": 0}
        self.frame_errors = 0
        self.last_frame_error = ""
        self.launcher = self._default_launcher          # replaceable by in-TD tests (must stay TD-free)
        self.clipboard_writer = self._default_clipboard  # main thread only
        self.tell_text = ""
        self.note, self._note_until, self._note_at = "", 0.0, 0.0
        self._msg, self._msg_at = "", 0.0
        self._static_facts = None
        self._published = {}
        self._shut = False
        # Phase 1B state. Handler threads read only: identity/admin snapshots, engine, store proxy, mirrors.
        self.admin = admin.AdminAuth()
        self.worker = None               # AuditWorker (owns the SQLite connections)
        self.store = None                # StoreProxy for handler threads (waits for durable completion)
        self.identity = None
        self.engine = None
        self.tdops = None
        self._storage_action = None      # the decision itself lives on its worker (see ``storage``)
        self._store_started = 0.0
        self._store_noted = False
        self.registry = tools.core_registry()
        self.active_tools = tuple(self.manifest.get("active_tools", ()))
        self.mcp_on = False
        self._mcp_wanted = False
        self._enabling = False
        self.suspended = False
        self._resuming = None
        self._audit_failed_flag = False  # set by handler threads, acted on in OnFrame
        self._audit_latched = False      # review 7 F3: survives engine rebuilds until a verified store reopen
        self._inflight = 0               # MCP requests being handled (review 7 F2)
        self._inflight_lock = threading.Lock()
        self._reset_wanted = None        # a store reset waiting for in-flight calls to finish
        self._pending = []               # (futures, on_ok, on_fail, deadline)
        self._admin_lock = threading.Lock()
        self._verify_cache = None
        self._auth_fail_seen, self._last_burst = 0, 0.0
        self.store_error = ""
        self.frame_cost = collections.deque(maxlen=2000)   # OnFrame cost (ms), main-thread impact
        self._root_text = self._read_root()
        c = controller
        deps = c.Deps(
            make_transport=self._make_transport,
            start_evidence=lambda port, host: c.Background(evidence.collect, port, os.getpid(), host,
                                                           name="tdflowmcp-verify"),
            own_listeners=lambda port: oslisteners.read_listeners(port, os.getpid()),
            other_listeners=lambda port: oslisteners.other_listeners(port, os.getpid()),
            probe_owner=lambda port: c.Background(evidence.responds_like_tdflowmcp, port, name="tdflowmcp-probe"),
            launch=lambda url: c.Background(self.launcher, url, name="tdflowmcp-browser"),
        )
        self.ctl = c.Controller(deps, port=int(ownerComp.par.Port.eval()))
        if ownerComp.par.Enablemcp.eval():          # never re-enable silently after a reload
            ownerComp.par.Enablemcp.val = False
        self.suspended = bool(ownerComp.par.Suspendagents.eval())
        self._refresh_tell()
        self._publish()

    # ---- dependencies (main thread) ------------------------------------------------------------
    def _make_transport(self, port, state_fn):
        # Spike S9: TouchDesigner runs no frames while its window is minimized. Requests that need the main
        # thread are then refused at once (TDFLOW_NOT_COOKING), and the orphan watchdog keeps the listener
        # while this process's own window is minimized (a TD-free OS query on the serve thread).
        pid, cid = os.getpid(), self.ownerComp.id

        def paused():
            # Review 12: "paused" is only an excuse for a LIVE owner. An instance that was shut down or replaced
            # (reinit) is an orphan whatever the window does, and closes after the heartbeat as before.
            if self._shut or _REGISTRY.instances.get(cid) is not self:
                return False
            return self._m.oswindow.minimized(pid)
        t = self._m.Transport("127.0.0.1", port, state_fn=state_fn, authenticator=self._auth, mcp_enabled=False,
                              heartbeat_timeout=20.0, job_timeout=5.0, dispatch_grace=5.0,
                              not_cooking_after=2.0, paused_fn=paused,
                              route_handlers={"mcp": self._route_mcp, "api": self._route_api})
        t.set_static(self._static_assets())
        t.suspended = self.suspended
        return t

    def _auth(self, kind, headers):
        """Handler thread: pure Python over immutable snapshots (identity tokens, admin sessions)."""
        if kind == "agent_token":
            idn = self.identity
            if idn is None:
                return None
            return self._m.admin.Authenticator(idn, self.admin)(kind, headers)
        return self.admin.check(kind, headers)

    def _static_assets(self):
        assets = {}
        for name, info in self.manifest.get("dats", {}).items():
            if info.get("serve"):
                data = self.ownerComp.op(name).text.replace("\r\n", "\n").encode("utf-8")
                assets[info["serve"]] = (info["content_type"], data)
                if info["serve"] == "/setup/":
                    assets["/setup"] = assets["/setup/"]
        assets["/.well-known/tdflowmcp"] = ("application/json", self._m.setupapi.well_known(self.version))
        return assets

    @staticmethod
    def _default_launcher(url):
        """Runs on a background thread: opens the user's default browser. TD-free by design."""
        try:
            return bool(webbrowser.open(url, new=2))
        except Exception:
            return False

    @staticmethod
    def _default_clipboard(text):
        td.ui.clipboard = text
        return td.ui.clipboard == text

    def _load_skill_bundle(self):
        """Main thread, once: the embedded Skills texts and their build-manifest hashes (verified again
        before every write, on the handler thread)."""
        files, expected = {}, {}
        for name, info in self.manifest.get("dats", {}).items():
            path = str(info.get("path", ""))
            if path.startswith("src/skills/"):
                rel = path[len("src/skills/"):]
                files[rel] = self.ownerComp.op(name).text.replace("\r\n", "\n")
                expected[rel] = info.get("sha256")
        return files, expected

    def _read_root(self):
        return str(self.ownerComp.par.Projectroot.eval()).rstrip("/") or "/project1"

    # ---- durable state: storage + audit worker ---------------------------------------------------
    def _private_root(self):
        override = os.environ.get("TDFLOW_PRIVATE_ROOT")
        if override:
            return override
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TDflowMCP")

    def _storage_inputs(self, action):
        """Main thread: plain strings for the worker thread (it must not touch TouchDesigner)."""
        folder, name = str(td.project.folder or ""), str(td.project.name or "")
        p = self.ownerComp.par
        priv = self._private_root()
        legacy = hashlib.sha256((folder + "/" + name).encode("utf-8")).hexdigest()[:16]   # Phase 1B layout
        return {"toe": os.path.join(folder, name) if folder and name else "",
                "pid": "" if action == "claim" else str(p.Projectid.eval() if hasattr(p, "Projectid") else ""),
                "mode": str(p.Datalocation.eval()) if hasattr(p, "Datalocation") else "project",
                "private_root": priv, "override": os.environ.get("TDFLOW_DATADIR") or "",
                "new_id": self._m.storage.new_project_id(), "legacy": os.path.join(priv, legacy), "action": action}

    @property
    def storage(self):
        """The storage decision of the current worker (made on its thread), or None."""
        return getattr(self.worker, "decision", None)

    @property
    def storage_result(self):
        return getattr(self.worker, "storage_result", None)

    def _prepare_storage(self, w, inp):
        """Audit worker thread, before the store opens. Decides where data lives; never touches TD.
        Results are stored on the worker itself, so a superseded worker can never overwrite them."""
        S, St = self._m.storage, self._m.store.Store
        if inp["override"]:
            pid = inp["pid"] if S.valid_id(inp["pid"]) else inp["new_id"]
            w.decision = S.Decision("OVERRIDE", pid, inp["override"], inp["override"], "TDFLOW_DATADIR override")
            w.datadir = w.private_dir = inp["override"]
            return
        reg = S.Registry(inp["private_root"])
        d = S.resolve(inp["toe"], inp["pid"], inp["private_root"], reg, inp["mode"], new_id=inp["new_id"],
                      recover=inp["action"] != "claim")
        if d.status == "NEW" and inp["action"] != "claim" and \
                os.path.exists(os.path.join(inp["legacy"], S.DB_NAME)):
            d = dataclasses.replace(d, status="LEGACY", usable=False, previous_data_dir=inp["legacy"],
                                    reason="audit data from an earlier TDflowMCP development build was found")
        if not d.usable and inp["action"] == "confirm" and d.status in ("MOVED", "RELOCATE", "LEGACY"):
            if d.status == "LEGACY":
                S.prepare_project_folder(d.data_dir)
                res = S.migrate(d.previous_data_dir, d.data_dir, d.private_dir, St,
                                src_private_dir=d.previous_data_dir, project_id=d.project_id)
            else:
                res = S.confirm_location(d, St)
            w.storage_result = res
            d = dataclasses.replace(d, status="OK", usable=True,
                                    reason="audit log adopted after verification" if res.get("adopted")
                                    else f"verified copy of {res.get('events', 0)} events (the old copy is untouched)")
        w.decision = d
        if not d.usable:
            raise StorageNeedsUser(d.status)
        S.prepare_project_folder(d.data_dir)           # .gitignore exists before the database is written
        w.datadir, w.private_dir = d.data_dir, d.private_dir

    def _after_open(self, w, st, inp):
        """Audit worker thread, right after opening: bind the database to this project, verify it."""
        d = w.decision
        if d.status != "OVERRIDE":
            st.bind_project(d.project_id)
        v = st.verify()
        if d.status != "OVERRIDE":
            self._m.storage.Registry(inp["private_root"]).put(d.project_id, "" if d.status == "UNSAVED" else inp["toe"],
                                                              d.data_dir)
        return {"verify": v}

    def ensure_store(self, start=True, action=None, restart=False):
        """Main thread, never waits. True when the audit store is open and the engine is built; otherwise
        (optionally) starts the audit worker in the background and returns False. A *failed* store is
        restarted only for an explicit user action (``restart``), never automatically."""
        w = self.worker
        if w is not None and w.available:
            if self.identity is None:
                self._on_store_ready()
            return self.identity is not None
        if w is not None and (not w.ready.is_set() or not restart):
            return False                                 # opening, or failed and waiting for the user
        if not start:
            return False
        self._reset_store()
        inp = self._storage_inputs(action)
        self._storage_action = action
        A, box = self._m.auditworker, {}

        def prepare(wk):
            box["w"] = wk
            wk.decision, wk.storage_result = None, None
            self._prepare_storage(wk, inp)
        self.worker = A.AuditWorker("", owner_ident=self._main.ident, prepare=prepare,
                                    after_open=lambda st: self._after_open(box["w"], st, inp)).start()
        self._store_started = time.monotonic()
        self._store_noted = False
        return False

    def _on_store_ready(self):
        """Main thread, once the worker opened the store: build identity (preloaded rows) and the engine."""
        w = self.worker
        self.store = self._m.auditworker.StoreProxy(w)
        self.identity = self._m.identity.Identity(self.store, preload=w.identity_rows)
        self.store_error = ""
        d = self.storage
        p = self.ownerComp.par
        if d is not None and d.status != "OVERRIDE" and hasattr(p, "Projectid") and p.Projectid.eval() != d.project_id:
            p.Projectid.val = d.project_id
            self._set_note("This project now has a TDflowMCP identity. Save the project to keep it.", 10)
        if self._storage_action == "confirm" and self.storage_result:
            self._set_note("Project data confirmed: " + d.reason + ". Save the project.", 30)
        elif self._storage_action == "claim":
            self._set_note("Claimed as a new project with an empty audit log. Save the project; agents need "
                           "new tokens.", 30)
        self._storage_action = None
        self._build_engine()

    def _reset_store(self):
        w = self.worker
        if w is not None:
            w.close()                                   # non-blocking: drains, checkpoints, closes
        self.worker, self.store, self.identity, self.engine = None, None, None, None
        self._verify_cache = None

    def _store_problem(self):
        """Main thread: a user-facing reason why the store is not available ('' while opening)."""
        w = self.worker
        if w is None:
            return "The audit store is not open."
        if not w.ready.is_set():
            return ""
        if w.available:
            return ""
        d = self.storage
        if w.failed == "StorageNeedsUser" and d is not None:
            return (f"Project data needs your confirmation: {d.reason}. On the Advanced page, pulse Confirm "
                    "Move (moved/renamed/relocated) or Claim As New Project (a separate copy).")
        if w.failed == "DATA_BELONGS_TO_ANOTHER_PROJECT":
            return ("The audit database in this project's data folder belongs to another TDflowMCP project. "
                    "Pulse Claim As New Project (nothing is overwritten).")
        return "The audit store is unavailable (" + (w.failed_detail or w.failed)[:120] + "), so MCP stays disabled."

    def _build_engine(self):
        root = self._root_text
        try:
            pol = self._m.policy.Policy(root)
        except ValueError:
            self.engine = None
            return
        self.tdops = self._m.tdops.TDAdapter(self.ownerComp, lambda: pol.root, lambda: None)
        self.engine = self._m.engine.Engine(self.store, self.identity, pol, self.registry, self.tdops,
                                            self.active_tools, lambda: self.suspended,
                                            on_audit_failure=self._flag_audit_failure)
        self.engine.audit_failed = self._audit_latched      # a rebuild never clears an audit failure

    def _flag_audit_failure(self):
        """Any thread (usually a handler thread): picked up by OnFrame (no TD access here)."""
        self._audit_latched = True
        self._audit_failed_flag = True

    def _audit_failed(self):
        """Main thread. An executed change could not be recorded: stop all agents until the user acts."""
        self._suspend_now()
        if not self.ownerComp.par.Suspendagents.eval():
            self.ownerComp.par.Suspendagents.val = True
        self._set_note("The audit store failed while recording a change, so all agents were suspended.", 60)

    # -- confirmation queue (main thread) --
    def _after(self, futures, on_ok=None, on_fail=None, timeout=CONFIRM_TIMEOUT):
        self._pending.append((list(futures), on_ok, on_fail, time.monotonic() + timeout))

    def _poll_pending(self):
        if not self._pending:
            return
        now, keep, todo = time.monotonic(), [], []
        for item in self._pending:
            futs, on_ok, on_fail, deadline = item
            if all(f.done() for f in futs):
                bad = next((f for f in futs if not f.ok()), None)
                if bad is None:
                    todo.append((on_ok, [f.result(0) for f in futs]))
                else:
                    todo.append((on_fail, str(bad.error()) or type(bad.error()).__name__))
            elif now > deadline:
                todo.append((on_fail, "AUDIT_TIMEOUT"))
            else:
                keep.append(item)
        self._pending = keep
        for fn, arg in todo:                     # callbacks may queue more confirmations
            if fn is not None:
                fn(arg)

    def _audit_async(self, kind, **fields):
        """Record an administrative event without waiting. Returns the Future (or None: store not open)."""
        w = self.worker
        if w is None or not w.available:
            return None
        f = w.submit("append", kind, **fields)
        self._after([f], None, lambda code: self._set_note(f"An administrative event could not be recorded ({code}).", 30))
        return f

    # ---- diagnostics (main thread) -------------------------------------------------------------
    def _facts(self, full=True):
        if full or self._static_facts is None:
            mods = {}
            for name in self._m.compat.REQUIRED_MODULES:
                try:
                    m = __import__(name, fromlist=["_"])
                    mods[name] = {"ok": True, "version": str(getattr(m, "__version__", ""))}
                except Exception:
                    mods[name] = {"ok": False, "version": ""}
            try:
                instances = len(td.op("/").findChildren(tags=["TDflowMCP"]))
            except Exception:
                instances = None
            self._static_facts = {"modules": mods, "instances": instances,
                                  "selftest": self._m.compat.security_selftest(self.ctl.port)}
        sf = self._static_facts
        ctl = self.ctl
        CS = self._m.controller.CompState
        ev = self._m.bindverify.redacted(ctl.gate.last) if (ctl.state is CS.SETUP or
                                         (ctl.state is CS.ERROR and ctl.error_code == "BIND_UNVERIFIED")) else None
        others = self._m.oslisteners.other_listeners(ctl.port, os.getpid())
        w, d = self.worker, self.storage
        # No disk I/O here: writability is what the worker found when it opened the store.
        writable = None
        status, reason = (d.status, d.reason) if d is not None else (None, "")
        if w is not None and w.ready.is_set():
            if w.available:
                writable = True
            elif w.failed == "StorageNeedsUser":
                writable = True
            else:
                writable = w.failed not in ("OSError", "PermissionError", "OperationalError")
                status, reason = "BLOCKED", (w.failed_detail or w.failed)[:160]
        return {
            "td_build": self.td_build, "tox_saved_build": self.manifest.get("built_with_td"),
            "python_version": tuple(sys.version_info[:3]), "modules": sf["modules"],
            "transport": "python-http", "bind_host": "127.0.0.1",
            "exclusive_bind": hasattr(socket, "SO_EXCLUSIVEADDRUSE") if sys.platform == "win32" else True,
            "bind_evidence": ev, "port": ctl.port, "port_listeners_other": others,
            "port_probe_tdflowmcp": "TDflowMCP" in (ctl.portstatus or ""),
            "datadir_writable": writable, "storage_status": status, "storage_reason": reason,
            "audit_verify": (w.open_info or {}).get("verify") if (w is not None and w.available) else None,
            "instances": sf["instances"], "selftest": sf["selftest"],
        }

    def RunDiagnostics(self, full=True):
        self.checks = self._m.compat.run_checks(self._facts(full))
        self.checks_at = time.monotonic()
        self._refresh_tell()
        self._publish()
        return self.checks

    def _memory_ops(self):
        return self.tdops or self._m.tdops.TDAdapter(self.ownerComp, lambda: self._root_text, lambda: None)

    def _memory_lines(self):
        """Phase 2A: resume lines from project memory (main thread, no disk I/O; validated fields only). In
        repository mode the counts come from the component's derived index, never from the files."""
        M = self._m.memory
        try:
            ops = self._memory_ops()
            info = ops.memory_info(with_dats=False)
            if info["status"] != "ready":
                return []
            if info["authority"] == "repo":
                return M.tell_counts(*M.derived_counts(info["derived"]))
            status, _, _, entries = ops.memory_entries()
        except Exception:
            return []
        return M.tell_section(M.tasks(entries)) if status == "ready" else []

    def _bg(self, fn):
        """Run TD-free file I/O on a short-lived thread; the Future is polled in OnFrame (``_after``). The main
        thread never waits for the disk."""
        f = self._m.auditworker.Future()

        def run():
            try:
                f.set(fn())
            except Exception as e:
                f.fail(e)
        threading.Thread(target=run, name="tdflowmcp-io", daemon=True).start()
        return f

    def _refresh_tell(self):
        ctl = self.ctl
        self.tell_text = self._m.tellagent.build(self._state_value(), ctl.error_code, ctl.port, self.td_build,
                                                 self.checks, self.troubleshooting, self.version, self.clients,
                                                 memory_lines=self._memory_lines())
        dat = self.ownerComp.op("tellagent")
        if dat is not None and dat.text != self.tell_text:
            dat.text = self.tell_text

    def _state_value(self):
        v = self.ctl.state.value
        if v == "Setup" and self.mcp_on:
            return "Suspended" if self.suspended else "Enabled"
        return v

    # ---- parameter callbacks (main thread) ------------------------------------------------------
    def OnPulse(self, par):
        n = par.name
        if n == "Opensetup":
            self.ctl.apply_pending_port()
            other = self._local_port_user(self.ctl.port)
            if not other:
                self.ensure_store(restart=True)       # opens in the background (pairing needs the audit)
            self.RunDiagnostics()
            if other:
                self.ctl.refuse("PORT_IN_USE", other, "in use by this TouchDesigner process")
            else:
                self.ctl.open_setup()
        elif n == "Stopserver":
            self.ctl.stop("user")
        elif n == "Rundiagnostics":
            self.RunDiagnostics()
        elif n == "Copytellagent":
            self.CopyTellAgent()
        elif n == "Viewinstructions":
            self.ViewInstructions()
        elif n in ("Approvepairing", "Rejectpairing"):
            self._pairing_decision(n == "Approvepairing")
        elif n in ("Confirmmove", "Claimproject"):
            self._storage_choice("confirm" if n == "Confirmmove" else "claim")
        elif n == "Createprojectmemory":
            self._create_memory()
        elif n == "Claimprojectmemory":
            self._claim_memory()
        self._refresh_tell()
        self._publish()

    def OnValueChange(self, par, prev):
        n = par.name
        if n == "Port":
            self.ctl.set_port(int(par.eval()))
        elif n == "Enablemcp":
            self._set_mcp_wanted(bool(par.eval()))
        elif n == "Suspendagents":
            self._apply_switch(bool(par.eval()))
        elif n == "Projectroot":
            self._root_text = self._read_root()
            if self.identity is not None:
                self._build_engine()
                root = td.op(self.engine.policy.root) if self.engine is not None else None
                if self.mcp_on and (root is None or not root.isCOMP):
                    self._mcp_off("Project Root is not an existing component; MCP was turned off.")
        elif n == "Datalocation":
            if self.mcp_on:
                self._mcp_off("Data Location changed, so MCP was turned off. Pulse Confirm Move to move the data.")
            if self.worker is not None:
                self._reset_wanted = (None,)            # after in-flight calls finished (review 7 F2)
        self._refresh_tell()
        self._publish()

    def _storage_choice(self, action):
        if self.mcp_on or self._enabling:
            self._set_note("Turn off Enable MCP before changing where project data lives.", 20)
            return
        if self.worker is not None and not self.worker.ready.is_set():
            self._set_note("Project data is still being checked. Try again in a moment.", 10)
            return
        if self._inflight:
            self._reset_wanted = (action,)            # agents' calls are still finishing: do it afterwards
            self._set_note("Waiting for agent calls to finish…", 10)
            return
        self._reset_store()
        self.ensure_store(action=action)
        self._set_note("Verifying project data…" if action == "confirm" else "Creating a new project identity…", 20)

    # -- project memory (Phase 2A): user actions, audited through the worker (no disk I/O here) --
    def _create_memory(self):
        w = self.worker
        if w is None or not w.available:
            self._set_note("Pulse Open Setup first: creating project memory is recorded in the audit log.", 20)
            return
        try:
            path, mid = self._memory_ops().create_memory(near=self.ownerComp)
        except ValueError as e:
            self._set_note(str(e)[:200], 20)
            return
        self._audit_async("admin", tool="memory_create", outcome="ok", detail={"memory_id": mid, "component": path})
        self._set_note(f"Project memory created at {path}. Save the project to keep it.", 30)

    def _claim_memory(self):
        """The user accepts the tdflowProject's current records as authoritative (after a copy, an older save,
        or edits outside TDflowMCP). Takes effect only once the claim event is durable."""
        w = self.worker
        if w is None or not w.available:
            self._set_note("Pulse Open Setup first: claiming project memory is recorded in the audit log.", 20)
            return
        M, R = self._m.memory, self._m.memrepo
        info = self._memory_ops().memory_info(with_dats=False)
        if info["status"] == "ready" and info["authority"] == "repo":
            # Repository mode: the records are files. They are read on a background thread; the folder must be
            # linked to this project's memory (a Projectid mismatch is never claimable).
            path, mid = info["comp"], info["memory_id"]

            def read():
                repo = R.resolve(info["project_folder"], info["repopath"])
                R.check_link(repo, mid)
                ents = M.read_records(R.read_records(repo))
                return M.claim_records(ents), M.derived_index(ents)

            def got(res):
                recs, idx = res[0]
                self._record_claim(w, mid, path, recs, "repo")
                try:
                    self._memory_ops().memory_derived(path, mid, idx)
                except Exception:
                    pass
            self._after([self._bg(read)], got,
                        lambda code: self._set_note(f"Project memory was not claimed ({code}). Check the linked "
                                                    "folder on the Setup page.", 30), timeout=10.0)
            self._set_note("Reading the project-memory folder…", 10)
            return
        status, path, mid, entries = self._memory_ops().memory_entries()
        if status != "ready":
            self._set_note("No usable project memory to claim (" + status + ").", 20)
            return
        try:
            recs = M.claim_records(entries)
        except M.MemoryError as e:
            self._set_note(e.message[:200], 30)
            return
        self._record_claim(w, mid, path, recs, "embedded")

    def _record_claim(self, w, mid, path, recs, authority):
        f = w.submit("append", "admin", tool="memory_claim", outcome="accepted",
                     detail={"memory_id": mid, "component": path, "records": recs, "authority": authority})
        self._after([f], lambda _: self._set_note(f"Project memory claimed: {len(recs)} record(s) accepted.", 30),
                    lambda code: self._set_note(f"The claim could not be recorded ({code}); memory stays read-only.", 30))

    def _pairing_decision(self, approve):
        pend = self.admin.pending_view()
        if not pend or pend["state"] != "pending":
            self._set_note("There is no pending pairing request.")
            return
        code = pend["code"]
        if not approve:                               # rejecting is always safe: no durable record required
            ok = self.admin.reject()
            self._audit_async("admin", tool="pairing", outcome="rejected", detail={"match_code": code})
            self._set_note(("Pairing rejected for " + code + ".") if ok else "The pairing request expired.")
            return
        if not self.ensure_store():
            prob = self._store_problem()
            self._set_note(("Pairing was not approved: " + prob) if prob else
                           "The audit store is opening. Pulse Approve Pairing again in a moment.", 30)
            return
        f = self.worker.submit("append", "admin", tool="pairing", outcome="approved", detail={"match_code": code})

        def ok(_):
            cur = self.admin.pending_view()
            done = bool(cur and cur["state"] == "pending" and cur["code"] == code and self.admin.approve())
            self._set_note(("Pairing approved for " + code + ".") if done else "The pairing request expired.")

        self._after([f], ok, lambda c: self._set_note(f"Pairing was not approved: the approval could not be "
                                                      f"recorded ({c}).", 30))
        self._set_note("Recording the approval…")

    # -- kill switch --
    def _apply_switch(self, want):
        if want and not self.suspended:
            self._suspend_now()
        elif not want and self.suspended and self._resuming is None:
            self._request_resume()

    def _suspend_now(self):
        self._resuming = None                         # cancels a resume that is waiting for its record
        if not self.suspended:
            self.suspended = True
            t = self.ctl.transport
            if t is not None:
                t.suspended = True                    # queued MCP jobs are re-checked at dispatch (pump)
            self._audit_async("kill_switch", outcome="on")
            self._set_note("All agents suspended.")

    def _request_resume(self):
        w = self.worker
        if self._audit_latched or (self.engine is not None and self.engine.audit_failed) or \
                (w is not None and w.ready.is_set() and not w.available):
            # After an audit failure, agents resume only with MCP off and a freshly opened audit store.
            if self.mcp_on:
                self._mcp_off("Agents resumed after an audit failure. MCP was turned off: turn Enable MCP on "
                              "again to re-check the audit store.")
            self._reset_store()
            self._audit_latched = False                 # the next store is opened and verified afresh
            return self._resume_now()
        if w is None or not w.available:
            return self._resume_now()                # no store open, so MCP cannot be on
        token = object()
        self._resuming = token
        f = w.submit("append", "kill_switch", outcome="off")

        def ok(_):
            if self._resuming is token:
                self._resume_now()

        def fail(code):
            if self._resuming is token:
                self._resuming = None
                self.ownerComp.par.Suspendagents.val = True
                self._set_note(f"Agents stay suspended: resuming could not be recorded ({code}).", 30)
        self._after([f], ok, fail)

    def _resume_now(self):
        self._resuming = None
        self.suspended = False
        t = self.ctl.transport
        if t is not None:
            t.suspended = False
        self._set_note("Agents resumed.")

    # -- Enable MCP --
    def _set_mcp_wanted(self, value):
        self._mcp_wanted = value
        if not value:
            if self.mcp_on:
                self._mcp_off("MCP disabled by the user.")
            return
        CS = self._m.controller.CompState
        self.ensure_store(restart=True)
        if self.ctl.state in (CS.OFF, CS.ERROR):
            self.ctl.apply_pending_port()
            self.RunDiagnostics()
            other = self._local_port_user(self.ctl.port)
            if other:
                self.ctl.refuse("PORT_IN_USE", other, "in use by this TouchDesigner process")
                self._refuse_mcp(other)
                return
            self.ctl.open_setup(launch=False)
        self._set_note("Enabling MCP after the local-only check…")

    def _try_enable(self):
        """Main thread, every frame while wanted and the listener is verified (Setup state)."""
        if self._enabling:
            return
        if not self.ensure_store():
            prob = self._store_problem()
            if prob:
                return self._refuse_mcp(prob)
            if time.monotonic() - self._store_started > STORE_OPEN_TIMEOUT:
                return self._refuse_mcp("The audit store did not open in time, so MCP stays disabled (fail closed).")
            return                                     # still opening
        self.RunDiagnostics(full=False)
        if self.engine is None:
            return self._refuse_mcp("Project Root is not a valid operator path.")
        root = td.op(self.engine.policy.root)
        if root is None or not root.isCOMP:
            return self._refuse_mcp("Project Root does not exist or is not a component.")
        ok, blockers = self._m.compat.mcp_enable_allowed(self.checks, self.identity.has_enabled_agent(),
                                                         self._static_facts.get("selftest", False))
        if not ok:
            return self._refuse_mcp("MCP stays disabled until these checks pass: " + ", ".join(blockers) +
                                    (". Create an agent token on the Setup page first." if "agent.token" in blockers else "."))
        w, eng = self.worker, self.engine
        self._enabling = True
        detail = {"root": eng.policy.root, "tools": list(self.active_tools)}

        def fail(code):
            self._enabling = False
            self._refuse_mcp(f"The audit store could not confirm ({code}), so MCP stays disabled (fail closed).")

        def finish(_):
            self._enabling = False
            CS = self._m.controller.CompState
            t = self.ctl.transport
            if not (self._mcp_wanted and self.ctl.state is CS.SETUP and t is not None and self.engine is eng):
                self._audit_async("mcp", outcome="disabled", detail={"reason": "enable aborted"})
                return
            self.tdops.refresh_protected()
            t.mcp_enabled = True
            self.mcp_on = True
            self._set_note(f"MCP enabled at http://127.0.0.1:{self.ctl.port}/mcp.")

        def verified(res):
            if not res[0].get("ok"):
                return fail("AUDIT_VERIFY_FAILED")
            # Record, then anchor the chain (checkpoint) at every enable; both must be durable first.
            self._after([w.submit("append", "mcp", outcome="enabled", detail=detail), w.submit("checkpoint")],
                        finish, fail)
        self._after([w.submit("verify")], verified, fail, timeout=STORE_OPEN_TIMEOUT)

    def _refuse_mcp(self, why):
        self._mcp_wanted = False
        self.ownerComp.par.Enablemcp.val = False
        self._set_note(why, 30)

    def _mcp_off(self, why):
        t = self.ctl.transport
        if t is not None:
            t.mcp_enabled = False
        was = self.mcp_on
        self.mcp_on, self._mcp_wanted = False, False
        if self.ownerComp.par.Enablemcp.eval():
            self.ownerComp.par.Enablemcp.val = False
        if was:
            self._audit_async("mcp", outcome="disabled", detail={"reason": why[:200]})
        self._set_note(why, 30)

    def _local_port_user(self, port):
        _REGISTRY.closing[:] = [t for t in _REGISTRY.closing if not t.is_closed()]
        for t in _REGISTRY.closing:
            if t.port == port:
                return ("The previous TDflowMCP listener on this port is still closing. Pulse Open Setup "
                        "again in a moment.")
        for key, inst in list(_REGISTRY.instances.items()):
            if inst is self:
                continue
            t = getattr(inst.ctl, "transport", None)
            if t is not None and t.port == port and not t.is_closed():
                return (f"Another TDflowMCP component in this project already uses port {port}. Stop it or "
                        "choose another Port. Only one TDflowMCP component per project is supported.")
        return ""

    def _set_note(self, text, seconds=15.0):
        now = time.monotonic()
        self.note, self._note_until, self._note_at = text, now + seconds, now

    def CopyTellAgent(self):
        self._refresh_tell()
        try:
            ok = bool(self.clipboard_writer(self.tell_text))
        except Exception:
            ok = False
        self._set_note("Agent instructions copied to the clipboard." if ok else
                       "Could not copy to the clipboard. Pulse View Agent Instructions, or open the Setup page.")
        return ok

    def ViewInstructions(self):
        """Open the full Tell Your Coding Agent text (the 'tellagent' DAT) in a TouchDesigner viewer."""
        self._refresh_tell()
        dat = self.ownerComp.op("tellagent")
        try:
            dat.openViewer()
            self._set_note("Agent instructions opened in a viewer.")
            return True
        except Exception:
            self._set_note("Could not open a viewer. The full text is in the 'tellagent' DAT inside the component.")
            return False

    # ---- per frame (main thread) ----------------------------------------------------------------
    def OnFrame(self):
        if self._shut:
            return
        t0 = time.perf_counter()
        try:
            CS = self._m.controller.CompState
            self._poll_pending()
            w = self.worker
            if w is not None and w.ready.is_set():
                if w.available and self.identity is None:
                    self._on_store_ready()
                elif not w.available and not self._store_noted:
                    self._store_noted = True
                    self.store_error = w.failed
                    if self.mcp_on:
                        self._mcp_off("The audit store failed, so MCP was turned off (fail closed).")
                    prob = self._store_problem()
                    if prob:
                        self._set_note(prob, 60)
            if self._audit_failed_flag:
                self._audit_failed_flag = False
                self._audit_failed()
            if self._reset_wanted is not None and not self._inflight:
                (action,) = self._reset_wanted
                self._reset_wanted = None
                self._reset_store()
                if action:
                    self.ensure_store(action=action)
            self._project_folder = str(td.project.folder or "")   # Save As moves project-scope Skills targets
            # Mirror the kill switch before dispatching anything this frame: parameter callbacks may be
            # deferred (review 6), and a queued request must never run after the switch was flipped.
            self._apply_switch(bool(self.ownerComp.par.Suspendagents.eval()))
            before = (self.ctl.state, self.ctl.error_code)
            self.ctl.tick(self._dispatch)
            if self.mcp_on and self.ctl.state is not CS.SETUP:
                self._mcp_off("The listener stopped, so MCP was turned off.")
            if self._mcp_wanted and not self.mcp_on and self.ctl.state is CS.SETUP:
                self._try_enable()
            if self._mcp_wanted and self.ctl.state is CS.ERROR:
                self._refuse_mcp("The listener could not start, so MCP stays disabled.")
            if (self.ctl.state, self.ctl.error_code) != before:
                if self.checks and self.ctl.state.value in ("Setup", "Error", "Off"):
                    self.RunDiagnostics(full=False)
                self._refresh_tell()
            if self.tdops is not None and self.tdops.memory_changed:
                self.tdops.memory_changed = False
                self._refresh_tell()                   # Phase 2A: resume section follows new records
            self._aggregate_auth_failures()
            self._publish()
        except Exception as e:  # never break TouchDesigner's frame
            self.frame_errors += 1
            self.last_frame_error = type(e).__name__
        self.frame_cost.append((time.perf_counter() - t0) * 1000.0)

    def _aggregate_auth_failures(self):
        """One auth_fail_burst event per 10 s window (security-model §6), never one write per request."""
        t = self.ctl.transport
        if t is None or self.worker is None or time.monotonic() - self._last_burst < 10:
            return
        n = t.stats().get("auth_fail", 0)
        if n > self._auth_fail_seen:
            self._audit_async("auth_fail_burst", detail={"count": n - self._auth_fail_seen})
        self._auth_fail_seen, self._last_burst = n, time.monotonic()

    def _dispatch(self, job):
        """Main thread only (called from transport.pump inside OnFrame): routes without a handler."""
        is_main = threading.current_thread() is self._main
        self.dispatch_threads["main" if is_main else "other"] += 1
        if not is_main:
            return self._m.error(500, "OFF_MAIN_THREAD")
        R, E = self._m.Response, self._m.error
        p, m = job.path, job.method
        if m == "GET" and p == "/setup/api/status":
            return R(200, self._m.setupapi.dumps(self._build_status()), "application/json")
        if m == "GET" and p == "/setup/api/tellagent":
            self._refresh_tell()
            return R(200, self.tell_text.encode("utf-8"), "text/plain; charset=utf-8")
        if job.route == "pair":
            return self._dispatch_pair(job)
        return E(404, "NOT_FOUND")

    # -- MCP (handler thread) --
    def _main_step(self, ctx, eng):
        """``main`` for Engine.call: run ``fn`` on TD's main thread via the transport and translate the
        transport's outcome into the engine's 'certainly not run' / 'may have run' exceptions. A step never
        runs once MCP was turned off or the engine was replaced (policy/root change) since the call began."""
        ME, NR, UK = self._m.MainStepError, self._m.engine.MainNotRun, self._m.engine.MainUnknown

        def wrapped(fn):
            def on_main():
                is_main = threading.current_thread() is self._main
                self.dispatch_threads["main" if is_main else "other"] += 1
                if not is_main:
                    raise RuntimeError("OFF_MAIN_THREAD")
                if not self.mcp_on or self.engine is not eng:
                    raise NR("MCP_DISABLED")             # certainly not run
                return fn()
            try:
                return ctx.call_main(on_main)
            except ME as e:
                raise (NR if e.ran is False else UK)(e.code) from None
        return wrapped

    def _route_mcp(self, ctx):
        """Handler thread: never touches TouchDesigner (TD steps go through ctx.call_main)."""
        eng = self.engine
        if not self.mcp_on or eng is None:
            return self._m.error(503, "MCP_DISABLED")
        tid = str(ctx.principal or "")[len("agent:"):]
        proto, main = self._m.protocol, self._main_step(ctx, eng)

        class Host(proto.ToolHost):
            def list_tools(self_inner):
                return eng.list_tools(tid)

            def call_tool(self_inner, name, arguments, client_info):
                return eng.call(tid, name, arguments, client_info, request_id=str(ctx.rid), main=main)
        with self._inflight_lock:
            self._inflight += 1
        try:
            status, body, headers = proto.handle(ctx.body, ctx.headers, Host())
        finally:
            with self._inflight_lock:
                self._inflight -= 1
        return self._m.Response(status, body or b"", "application/json", tuple(headers.items()))

    # -- pairing (main thread) --
    def _dispatch_pair(self, job):
        R, E, A = self._m.Response, self._m.error, self._m.admin
        D = self._m.setupapi.dumps
        pair_hash = str(job.principal)[len("pair:"):]
        if job.method == "POST":
            try:
                secret, code = self.admin.request()
            except PermissionError:
                return E(429, "RATE_LIMITED", [("Retry-After", "60")])
            except LookupError:
                return E(409, "PAIRING_PENDING")
            self.ensure_store()
            self._audit_async("admin", tool="pairing", outcome="requested", detail={"match_code": code})
            self._publish()
            return R(200, D({"state": "pending", "match_code": code,
                             "message": "Compare this code with the component's Pairing Request parameter in "
                                        "TouchDesigner, then pulse Approve Pairing there."}), "application/json",
                     (A.set_cookie(A.PAIR_COOKIE, secret, 120, "/setup/api/pair"),))
        state, creds = self.admin.poll(pair_hash)
        if state == "approved" and creds:
            sid, csrf = creds
            return R(200, D({"state": "approved", "csrf": csrf}), "application/json",
                     (A.set_cookie(A.SESSION_COOKIE, sid, int(self.admin.session_seconds), "/"),
                      A.clear_cookie(A.PAIR_COOKIE, "/setup/api/pair")))
        pend = self.admin.pending_view()
        return R(200, D({"state": state, "match_code": pend["code"] if pend and state == "pending" else None}),
                 "application/json")

    # -- admin (paired session, handler thread) --
    def _admin_body(self, body):
        if len(body) > MAX_ADMIN_BODY:
            return None
        try:
            v = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return None
        return v if isinstance(v, dict) else None

    def _route_api(self, ctx):
        """Handler thread. Admin requests run here (they wait for audit I/O, never for TD); the status and
        tell-agent reads fall back (None) to the main-thread dispatcher."""
        if ctx.path in ("/setup/api/status", "/setup/api/tellagent"):
            return None
        if not (ctx.principal and str(ctx.principal).startswith("admin:")):
            return self._m.error(404, "NOT_FOUND")
        R, E, D = self._m.Response, self._m.error, self._m.setupapi.dumps
        AU = self._m.store.AuditUnavailable
        st, idn = self.store, self.identity
        p, m = ctx.path, ctx.method
        if m == "GET" and p == "/setup/api/skills":
            return self._skills_api(p, m, {}, st)
        if m == "GET" and p == "/setup/api/memory":
            return self._memory_api(ctx, "status", {}, st)
        if m == "GET" and p == "/setup/api/agents":
            if st is None or idn is None or not st.available:
                return E(503, "AUDIT_UNAVAILABLE")
            try:
                now, vc = time.monotonic(), self._verify_cache
                if vc is None or now - vc[0] > 10:
                    vc = self._verify_cache = (now, st.verify())       # full re-verify at most every 10 s
                sessions = st.sessions(limit=10)
            except AU:
                return E(503, "AUDIT_UNAVAILABLE")
            return R(200, D({"agents": idn.public_list(), "sessions": sessions, "mcp": self._state_value(),
                             "project_root": self._root_text, "endpoint": f"http://127.0.0.1:{self.ctl.port}/mcp",
                             "audit": vc[1], "storage": self._storage_public()}), "application/json")
        if m != "POST":
            return E(405, "METHOD_NOT_ALLOWED")
        body = self._admin_body(ctx.body)
        if body is None:
            return E(400, "BAD_REQUEST")
        if p == "/setup/api/logout":
            self.admin.logout(str(ctx.principal)[len("admin:"):])   # ends the session on the server
            if st is not None and st.available:
                try:
                    st.append("admin", tool="logout")
                except AU:
                    pass
            return R(200, D({"ok": True}), "application/json",
                     (self._m.admin.clear_cookie(self._m.admin.SESSION_COOKIE, "/"),))
        if p.startswith("/setup/api/skills/"):
            return self._skills_api(p, m, body, st)
        if p in ("/setup/api/memory/plan", "/setup/api/memory/migrate"):
            return self._memory_api(ctx, p.rsplit("/", 1)[-1], body, st)
        if st is None or idn is None or not st.available:
            return E(503, "AUDIT_UNAVAILABLE")
        with self._admin_lock:
            try:
                if p == "/setup/api/tokens":
                    return self._create_token(body, st, idn)
                if p == "/setup/api/tokens/revoke":
                    tid = str(body.get("token_id", ""))[:32]
                    st.append("admin", tool="revoke_token", detail={"token_id": tid})   # durable first
                    ok = idn.revoke(tid)
                    return R(200 if ok else 404, D({"revoked": ok}), "application/json")
            except AU:
                return E(503, "AUDIT_UNAVAILABLE")
        return E(404, "NOT_FOUND")

    # -- Skills (paired admin session, handler thread; file I/O never on the main thread) --
    def _skills_home(self):
        return os.environ.get("TDFLOW_SKILLS_HOME") or os.path.expanduser("~")    # test seam: disposable home

    def _skills_targets(self):
        K = self._m.skills
        out = []
        for c in self.clients.get("clients", []):
            if not c.get("skills"):
                continue
            for scope in ("user", "project"):
                try:
                    base, target = K.resolve_target(c, scope, self._skills_home(), self._project_folder)
                except K.SkillsError as e:
                    out.append({"client": c["id"], "scope": scope, "error": e.code})
                    continue
                out.append({"client": c["id"], "scope": scope, "base": base, "target": target,
                            "display": c["skills"]["user_dir"] if scope == "user" else
                            "<project folder>/" + c["skills"]["project_dir"]})
        return out

    def _skills_api(self, p, m, body, st):
        """GET: preview (install + uninstall) per registry target, with a digest. POST install/uninstall:
        the exact previewed digest must be confirmed; the intent is audited durably before any write."""
        R, E, D = self._m.Response, self._m.error, self._m.setupapi.dumps
        K, AU = self._m.skills, self._m.store.AuditUnavailable
        try:
            bundle = K.verify_bundle(self._skill_bundle, self._skill_expected)
        except K.SkillsError as e:
            return E(500, e.code)
        targets = self._skills_targets()
        if m == "GET":
            rows = []
            for t in targets:
                if "error" in t:
                    rows.append({k: t[k] for k in ("client", "scope", "error")})
                    continue
                try:
                    pi = K.preview(t["base"], t["target"], bundle, self.skill_version)
                    pu = K.preview(t["base"], t["target"], {}, self.skill_version, "uninstall")
                except K.SkillsError as e:
                    rows.append({"client": t["client"], "scope": t["scope"], "error": e.code})
                    continue
                rows.append({"client": t["client"], "scope": t["scope"], "target": t["display"],
                             "installed_version": pi["installed_version"], "bundled_version": self.skill_version,
                             "mismatch": bool(pi["installed_version"] and pi["installed_version"] != self.skill_version),
                             "install": {"actions": pi["actions"], "conflicts": pi["conflicts"], "digest": pi["digest"]},
                             "uninstall": {"actions": pu["actions"], "digest": pu["digest"]},
                             "in_git_repo": pi["in_git_repo"]})
            return R(200, D({"bundled_version": self.skill_version, "targets": rows}), "application/json")
        op = p.rsplit("/", 1)[-1]
        if m != "POST" or op not in ("install", "uninstall"):
            return E(404, "NOT_FOUND")
        t = next((x for x in targets if x["client"] == str(body.get("client")) and x["scope"] == str(body.get("scope"))
                  and "error" not in x), None)
        if t is None:
            return E(400, "TARGET_NOT_ALLOWED")
        if st is None or not st.available:
            return E(503, "AUDIT_UNAVAILABLE")
        confirm = str(body.get("digest", ""))
        with self._admin_lock:
            try:
                st.append("admin", tool="skills_" + op, detail={"client": t["client"], "scope": t["scope"],
                                                               "version": self.skill_version,
                                                               "files": sorted(bundle), "digest": confirm[:64]})
                if op == "install":
                    res = K.install(t["base"], t["target"], bundle, self.skill_version, confirm,
                                    f"TDflowMCP {self.version}")
                else:
                    res = K.uninstall(t["base"], t["target"], confirm, self.skill_version)
            except AU:
                return E(503, "AUDIT_UNAVAILABLE")
            except K.SkillsError as e:
                try:
                    st.append("admin", tool="skills_" + op, outcome="refused", error_code=e.code)
                except AU:
                    pass
                return R(409, D({"code": e.code, "message": e.message}), "application/json")
            except OSError as e:
                try:
                    st.append("admin", tool="skills_" + op, outcome="error", error_code="WRITE_FAILED")
                except AU:
                    pass
                return R(500, D({"code": "WRITE_FAILED", "message": type(e).__name__}), "application/json")
            try:
                st.append("admin", tool="skills_" + op, outcome="ok", detail=res)
            except AU:
                return R(500, D(dict(res, code="OUTCOME_UNRECORDED", message="The files were written, but the "
                                     "outcome could not be recorded in the audit log.")), "application/json")
        return R(200, D(dict(res, ok=True)), "application/json")

    # -- project memory: status and mode migration (paired admin session, handler thread) --
    def _memory_api(self, ctx, op, body, st):
        """GET status; POST plan (a preview with a digest, nothing written); POST migrate (the exact previewed
        digest must be confirmed; audited before and after). File I/O runs here on the handler thread; each
        TouchDesigner step runs on the main thread through ``ctx.call_main``. Three moves: ``repo`` (embedded →
        folder), ``embedded`` (folder → component) and ``relink`` (repository mode whose link was lost)."""
        R, E, D = self._m.Response, self._m.error, self._m.setupapi.dumps
        M, RP, ME = self._m.memory, self._m.memrepo, self._m.MainStepError
        AU = self._m.store.AuditUnavailable
        J = "application/json"

        def refuse(code, message, status=409):
            return R(status, D({"code": code, "message": message}), J)
        if st is None or not st.available:
            return E(503, "AUDIT_UNAVAILABLE")
        try:
            info = ctx.call_main(lambda: self._memory_ops().memory_info())
        except ME:
            return E(503, "TD_BUSY")
        # "Saved" means the .toe file exists (a never-saved project still reports a folder; review 11).
        saved = os.path.isfile(info.get("project_file") or "")
        pf = info["project_folder"] if saved else ""
        mid, comp = info["memory_id"], info["comp"]
        repo_mode = info["authority"] == "repo"
        # Repository mode without a usable link is still a memory the user can re-link.
        unlinked = info["status"] == "uninitialized" and repo_mode and bool(M.MEMORY_ID.match(str(mid or "")))
        if info["status"] != "ready" and not unlinked:
            if op == "status":
                return R(200, D({"status": info["status"], "reason": info["reason"], "project_saved": saved}), J)
            return refuse("MEMORY_UNAVAILABLE", info["reason"] or "There is no usable project memory.")

        def state_of(dats):
            entries = M.read_records(dats)
            return entries, M.assess(entries, M.ledger(st.memory_events(), mid))

        def stamp(src, dst, n, digest, folder):
            return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "from": src, "to": dst,
                    "memory_id": mid, "records": n, "records_digest": digest, "folder": folder,
                    "tdflowmcp": self.version}

        def failed(note, e):
            """Disk work failed before the component was touched: the trail is closed with an error event."""
            code = e.code if isinstance(e, RP.RepoError) else "WRITE_FAILED"
            try:
                st.append("admin", tool="memory_migrate", outcome="error", error_code=code, detail=note)
            except AU:
                pass

        def switch(note, fn, undo_repo):
            """The main-thread step. None on success, else the refusal (already audited)."""
            try:
                ctx.call_main(fn)
            except Exception as e:
                return self._migrate_failed(st, RP, undo_repo, mid, note, e, refuse)
            return None

        def done(note, repo):
            try:
                RP.log_migration(repo, note)              # informational copy in the folder
            except (RP.RepoError, OSError):
                note = dict(note, folder_log="not written")
            try:
                st.append("admin", tool="memory_migrate", outcome="ok", detail=note)
            except AU:
                return refuse("OUTCOME_UNRECORDED", "Project memory was moved, but the audit log could not record "
                              "it. Check the audit store before agents continue.", 500)
            return None
        files_note = (" Anyone who can read these files (for example a coding agent with file access) can read "
                      "every task record in them, whatever its TDflowMCP scopes.")
        try:
            if op == "status":
                out = {"status": "ready", "component": comp, "memory_id": mid, "authority": info["authority"],
                       "folder": RP.display(pf, info["repopath"]) if repo_mode else "", "project_saved": saved,
                       "where": RP.WHERE}
                try:
                    dats = info["dats"]
                    if repo_mode:
                        repo = RP.resolve(pf, info["repopath"])
                        RP.check_link(repo, mid)
                        dats = RP.read_records(repo)
                    entries, state = state_of(dats)
                    out.update(records=len(entries), tasks=len(M.tasks(entries)), state=state["state"], link="ok")
                    if state["state"] != "ok":
                        out["read_only_reason"] = M.read_only_reason(state)
                except RP.RepoError as e:
                    out.update(link=e.code, link_message=e.message, state="unavailable")
                return R(200, D(out), J)
            to = str(body.get("to", ""))
            confirm = str(body.get("digest", ""))
            if to == "repo":
                if repo_mode:
                    return refuse("ALREADY_REPOSITORY", "Project memory is already in repository mode.")
                rel = RP.to_repopath(pf, str(body.get("folder", ""))[:1024])
                repo, shown = RP.resolve(pf, rel), RP.display(pf, rel)
                entries, state = state_of(info["dats"])
                if state["state"] != "ok":
                    return refuse("MEMORY_NEEDS_CLAIM", M.read_only_reason(state))
                plan = RP.plan_to_repo(repo, mid, info["dats"], shown)
                plan["then"] = ("The component then holds only the link and a derived index: its record DATs are "
                                "removed after the files were verified. Agents need the repository-memory "
                                "permission on their token to add records. Files changed outside TDflowMCP (for "
                                "example by Git) make memory read-only until Claim Project Memory." + files_note)
                if plan.get("already_in_folder"):
                    plan["then"] += (" Records already in the folder are left as they are; any that this project's "
                                     "audit log does not know make memory read-only until Claim Project Memory.")
                if op == "plan":
                    return R(200, D(plan), J)
                if plan["digest"] != confirm:
                    return refuse("PREVIEW_CHANGED", "The folder or the records changed since the preview. Review "
                                  "it again.")
                if plan["conflicts"]:
                    return refuse("CONFLICT", "The folder already holds different records: " +
                                  ", ".join(plan["conflicts"][:5]))
                with RP.LOCK:                             # no agent record can be written in between (review 11)
                    note = stamp("embedded", "repo", plan["records"], plan["records_digest"], rel)
                    st.append("admin", tool="memory_migrate", outcome="started", detail=note)
                    try:
                        RP.to_repo(repo, mid, info["dats"], plan["digest"])
                        ents = M.read_records(RP.read_records(repo))
                    except (RP.RepoError, OSError) as e:
                        failed(note, e)
                        raise
                    line = json.dumps(note, sort_keys=True)
                    bad = switch(note, lambda: self._memory_ops().memory_to_repo(
                        comp, mid, rel, M.derived_index(ents), plan["records_digest"], line), repo)
                    bad = bad or done(note, repo)
                    if bad:
                        return bad
                return R(200, D({"ok": True, "authority": "repo", "records": len(ents), "folder": shown}), J)
            if to == "embedded":
                if not repo_mode:
                    return refuse("ALREADY_EMBEDDED", "Project memory is already embedded in the component.")
                repo, shown = RP.resolve(pf, info["repopath"]), RP.display(pf, info["repopath"])
                plan = RP.plan_to_embedded(repo, mid, info["dats"], shown)      # includes the link check
                files = sorted((n, t) for n, t in RP.read_records(repo) if M.RECORD_NAME.match(n))
                entries, state = state_of(files)
                if state["state"] != "ok":
                    return refuse("MEMORY_NEEDS_CLAIM", M.read_only_reason(state))
                plan["then"] = ("The component's record DATs become exactly these records and it is the authority "
                                "again. The files stay in the folder, marked as a derived export that TDflowMCP no "
                                "longer reads. Save the project to keep the records.")
                if op == "plan":
                    return R(200, D(plan), J)
                with RP.LOCK:
                    # Read once more inside the lock: these exact texts are ledger-checked and written.
                    files = sorted((n, t) for n, t in RP.read_records(repo) if M.RECORD_NAME.match(n))
                    entries, state = state_of(files)
                    if plan["digest"] != confirm or M.dats_digest(files) != plan["records_digest"] or state["state"] != "ok":
                        return refuse("PREVIEW_CHANGED", "The folder or the component changed since the preview. "
                                      "Review it again.")
                    note = stamp("repo", "embedded", len(files), plan["records_digest"], info["repopath"])
                    st.append("admin", tool="memory_migrate", outcome="started", detail=note)
                    line = json.dumps(note, sort_keys=True)
                    bad = switch(note, lambda: self._memory_ops().memory_to_embedded(comp, mid, files, line), None)
                    if bad:
                        return bad
                    try:
                        RP.mark_derived(repo, mid, f"{len(files)} record(s)")
                        note["folder_marked"] = True
                    except (RP.RepoError, OSError):
                        note["folder_marked"] = False    # the component is the authority now; recorded, not hidden
                    bad = done(note, repo)
                    if bad:
                        return bad
                return R(200, D({"ok": True, "authority": "embedded", "records": len(files)}), J)
            if to == "relink":
                if not repo_mode:
                    return refuse("NOT_REPOSITORY", "Project memory is embedded in the component; there is no "
                                  "folder link to repair.")
                rel = RP.to_repopath(pf, str(body.get("folder") or info["repopath"] or "")[:1024])
                repo, shown = RP.resolve(pf, rel), RP.display(pf, rel)
                plan = RP.plan_relink(repo, mid, shown)
                plan["then"] = ("The component points at this folder again and the folder is marked as this "
                                "project's memory. No record is changed. Records this project's audit log does not "
                                "know stay unusable until you pulse Claim Project Memory." + files_note)
                if op == "plan":
                    return R(200, D(plan), J)
                if plan["digest"] != confirm:
                    return refuse("PREVIEW_CHANGED", "The folder changed since the preview. Review it again.")
                with RP.LOCK:
                    note = dict(stamp("repo", "repo", plan["records"], plan["records_digest"], rel), relink=True)
                    st.append("admin", tool="memory_migrate", outcome="started", detail=note)
                    try:
                        RP.relink(repo, mid, plan["digest"])
                        ents = M.read_records(RP.read_records(repo))
                    except (RP.RepoError, OSError) as e:
                        failed(note, e)
                        raise
                    line = json.dumps(note, sort_keys=True)
                    bad = switch(note, lambda: self._memory_ops().memory_relink(
                        comp, mid, rel, M.derived_index(ents), line), None)
                    bad = bad or done(note, repo)
                    if bad:
                        return bad
                return R(200, D({"ok": True, "authority": "repo", "records": len(ents), "folder": shown}), J)
            return refuse("INVALID_" + "ARGS", "Choose a target: repo, embedded or relink.", 400)
        except RP.RepoError as e:
            return refuse(e.code, e.message)
        except AU:
            return E(503, "AUDIT_UNAVAILABLE")
        except OSError as e:
            return refuse("WRITE_FAILED", type(e).__name__, 500)

    def _migrate_failed(self, st, RP, repo, mid, note, err, refuse):
        """The component step did not confirm. If it certainly did not run, a folder that was just filled must
        not look like an authority. If it may have run, nothing more is changed and the user is told to check."""
        ME = self._m.MainStepError
        # A refusal (ValueError: raised before any change) or a step that never ran changed nothing. Anything
        # else may have changed the component part-way, so it is reported as unknown.
        unknown = (isinstance(err, ME) and err.ran is None) or not isinstance(err, (ME, ValueError))
        note = dict(note)
        if repo is not None and not unknown:
            try:
                RP.mark_derived(repo, mid, "a migration into this folder was not completed")
                note["folder_marked"] = True
            except (RP.RepoError, OSError):
                note["folder_marked"] = False
        try:
            st.append("admin", tool="memory_migrate", outcome="unknown" if unknown else "error",
                      error_code="OUTCOME_UNKNOWN" if unknown else "NOT_SWITCHED", detail=note)
        except self._m.store.AuditUnavailable:
            pass
        if unknown:
            return refuse("OUTCOME_UNKNOWN", "TouchDesigner did not confirm the switch in time. Check the Memory "
                          "Authority parameter of the tdflowProject component, then reload this page.", 503)
        msg = str(err) if isinstance(err, ValueError) else "TouchDesigner did not process the request."
        return refuse("NOT_SWITCHED", msg[:200] + " Project memory was left as it was.")

    def _create_token(self, body, st, idn):
        R, E, D = self._m.Response, self._m.error, self._m.setupapi.dumps
        root = self._root_text
        name = str(body.get("display_name", ""))[:64]
        role = str(body.get("role", "builder"))
        scope = str(body.get("scope") or (root + "/**"))
        actions = body.get("actions")
        if role not in ("observer", "builder"):
            return E(400, "ROLE_NOT_ALLOWED")               # 'operator' adds only execute, unavailable in 1B
        if not (scope == root + "/**" or scope.startswith(root + "/")):
            return E(400, "SCOPE_OUTSIDE_PROJECT_ROOT")
        acts = None
        if isinstance(actions, list) and actions:
            acts = sorted(set(str(a) for a in actions) & set(self._m.policy.ROLES[role]))
            if not acts:
                return E(400, "INVALID_" + "ARGS")            # validated before any credential exists
        # Dedicated, owner-issued capabilities (ADR-0013, D18): never implied by a role or a scope.
        caps = body.get("capabilities")
        caps = sorted(set(str(c) for c in caps)) if isinstance(caps, list) else []
        if caps and (not set(caps) <= self._m.policy.CAPABILITIES or "update" not in self._m.policy.ROLES[role]
                     or (acts is not None and "update" not in acts)):
            # Review 11: a token whose actions were narrowed to exclude update never gets a write capability.
            return E(400, "CAPABILITY_NOT_ALLOWED")
        st.append("admin", tool="create_token", detail={"display_name": name, "role": role, "scope": scope,
                                                         "actions": actions, "capabilities": caps})  # durable first
        agent = None
        try:
            agent, token = idn.create_agent(name, role, [scope], caps)
            if acts:
                st.set_grant_actions(agent.id, ",".join(acts))
                idn.reload()
                agent = idn.agents[agent.id]
        except (ValueError, self._m.store.AuditUnavailable) as e:
            if agent is not None:                              # never leave a half-configured credential
                try:
                    idn.set_enabled(agent.id, False)
                except Exception:
                    pass
            if isinstance(e, ValueError):
                return E(400, "INVALID_" + "ARGS")
            raise
        return R(200, D({"agent": {"id": agent.id, "display_name": agent.display_name, "role": agent.role,
                                   "scopes": [{"scope": g.scope, "actions": sorted(g.actions)} for g in agent.grants],
                                   "capabilities": sorted(agent.capabilities)},
                         "token": token, "shown_once": True,
                         "env_hint": "Store it as the TDFLOW_TOKEN environment variable. It is shown only once."}),
                 "application/json")

    def _storage_public(self):
        d = self.storage
        if d is None:
            return {"status": "not opened"}
        return {"status": d.status, "reason": d.reason, "location": self._data_display(), "usable": d.usable,
                "project_id": d.project_id}

    def _data_display(self):
        """Where data lives, without user-profile paths (this value is saved in the .toe)."""
        d = self.storage
        if d is None:
            return "(not opened)"
        if d.status == "OVERRIDE":
            return "(TDFLOW_DATADIR override)"
        dd = os.path.normcase(os.path.abspath(d.data_dir))
        if dd.startswith(os.path.normcase(os.path.abspath(self._private_root()))):
            return "Private storage: %LOCALAPPDATA%\\TDflowMCP\\projects\\" + d.project_id + "\\data"
        return "Project folder: " + os.path.join(".tdflowmcp", os.path.basename(d.data_dir)) + " (git-ignored)"

    def _data_status(self):
        d, w = self.storage, self.worker
        if w is None and d is None:
            return "not opened"
        if w is not None and not w.ready.is_set():
            return "opening…"
        if d is not None and not d.usable:
            return f"Needs confirmation ({d.status.lower()}): {d.reason}. Pulse Confirm Move or Claim As New Project."
        if w is not None and not w.available:
            return "Unavailable: " + (w.failed_detail or w.failed)[:100]
        if d is None:
            return "not opened"
        v = (w.open_info or {}).get("verify") if w is not None else None
        integ = "" if not v else (" · audit verified" if v.get("ok") else " · AUDIT VERIFICATION FAILED")
        why = {"FALLBACK": " (" + d.reason + ")", "UNSAVED": " (unsaved project)"}.get(d.status, "")
        return d.status.capitalize() + why + integ

    def _build_status(self):
        self._refresh_tell()
        s = self._m.setupapi.status(self.ctl.snapshot(), self.td_build, self.checks, self.troubleshooting,
                                    self.tell_text, time.monotonic() - self.checks_at, self.version)
        s["mcp"] = {"Enabled": "enabled", "Suspended": "suspended (kill switch)"}.get(self._state_value(), "disabled")
        s["state"] = self._state_value()
        s["endpoint"] = f"http://127.0.0.1:{self.ctl.port}/mcp" if self.mcp_on else ""
        pend = self.admin.pending_view()
        s["pairing"] = {"pending": bool(pend and pend["state"] == "pending"), "admin_sessions": self.admin.active_sessions()}
        s["storage"] = self._storage_public()
        return s

    def _status_detail(self):
        """One short line (owner UX correction): the latest note while it is fresh, else the state message."""
        now, msg = time.monotonic(), self.ctl.message
        if msg != self._msg:
            self._msg, self._msg_at = msg, now
        fresh = bool(self.note) and now < self._note_until
        # The newer of the two wins; listener errors always win (they explain why nothing works).
        use_note = fresh and self._note_at >= self._msg_at and self.ctl.state is not self._m.controller.CompState.ERROR
        text = " ".join(str(self.note if use_note else msg).split())
        if len(text) > 160:                      # one item, never message + note (full details: Setup page)
            cut = text[:160]
            dot = cut.rfind(". ")
            text = cut[:dot + 1] if dot > 60 else cut.rstrip() + "…"
        return text

    def _publish(self):
        p, ctl = self.ownerComp.par, self.ctl
        pend = self.admin.pending_view()
        n_agents = len(self.identity.agents) if self.identity else 0
        vals = {
            "Status": self._state_value() + (f": {ctl.error_code}" if ctl.error_code else ""),
            "Statusdetail": self._status_detail(),
            "Setupurl": ctl.url if ctl.state is self._m.controller.CompState.SETUP else "",
            "Portstatus": ctl.portstatus,
            "Compatsummary": self._m.compat.summary(self.checks) if self.checks else "not run",
            "Tellagent": self._m.tellagent.one_line(ctl.state.value, ctl.error_code, self.checks, self.version),
            "Pairingrequest": (f"Pending: {pend['code']} ({pend['wall']})" if pend and pend["state"] == "pending" else ""),
            "Agentsummary": f"{n_agents} agent(s) · {self.admin.active_sessions()} admin session(s)",
            "Datadir": self._data_display(),
            "Datastatus": self._data_status(),
        }
        for k, v in vals.items():
            if self._published.get(k) != v and hasattr(p, k):
                getattr(p, k).val = v
                self._published[k] = v

    # ---- lifecycle (main thread) ----------------------------------------------------------------
    def Shutdown(self, reason="exit"):
        """Stop the listener now (project exit, component deleted, extension re-initialised)."""
        if self.mcp_on:
            self._mcp_off("TDflowMCP is shutting down.")
        t = self.ctl.transport
        if t is not None:
            self.ctl.stop(reason)
            if reason in ("exit", "destroyed", "reinit"):
                _REGISTRY.closing.append(t)
        self._shut = reason in ("exit", "destroyed", "reinit")
        if self._shut:
            self.admin.logout_all()
            if self.worker is not None:
                self.worker.close()                   # never waits: the worker drains, checkpoints, closes
            if _REGISTRY.instances.get(self.ownerComp.id) is self:
                _REGISTRY.instances.pop(self.ownerComp.id, None)

    def onDestroyTD(self):
        self.Shutdown("destroyed")

    def datadir(self):
        """(actual path, display) of the audit database folder, or ('', '(not opened)')."""
        d = self.storage
        return (d.data_dir if d is not None else ""), self._data_display()

    def FrameStats(self, reset=False):
        """Main-thread impact: OnFrame cost percentiles in ms (no secrets)."""
        xs = sorted(self.frame_cost)
        if reset:
            self.frame_cost.clear()
        if not xs:
            return {"n": 0}
        q = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]
        return {"n": len(xs), "p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "max": xs[-1],
                "mean": sum(xs) / len(xs)}

    def Diagnostics(self):
        """Read-only internals for the in-TD test runner and support (no secrets)."""
        t, w, d = self.ctl.transport, self.worker, self.storage
        return {"state": self._state_value(), "error_code": self.ctl.error_code, "gate": self.ctl.gate.state.value,
                "port": self.ctl.port, "reverifies": self.ctl.reverifies, "launch": self.ctl.launch_status,
                "dispatch_threads": dict(self.dispatch_threads), "frame_errors": self.frame_errors,
                "last_frame_error": self.last_frame_error, "events": list(self.ctl.events)[-20:],
                "transport_stats": t.stats() if t else None, "version": self.version, "mcp_on": self.mcp_on,
                "suspended": self.suspended, "store": bool(w and w.available), "store_error": self.store_error,
                "worker": None if w is None else {"ready": w.ready.is_set(), "available": w.available,
                                                  "failed": w.failed, "queued": w.queued(), "stats": dict(w.stats)},
                "storage": None if d is None else {"status": d.status, "usable": d.usable, "reason": d.reason},
                "audit_failed": bool(self.engine and self.engine.audit_failed), "frame": self.FrameStats(),
                "pending": len(self._pending), "source_digest": self.manifest.get("source_digest")}
