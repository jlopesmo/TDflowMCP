"""TDflowMCPExt: the TouchDesigner extension of TDflowMCP.tox (TD adapter, Phases 1A + 1B).

Every method runs on TouchDesigner's main thread (extension init, parameter callbacks, the per-frame
``OnFrame``, ``Shutdown``). Only TD-free work runs elsewhere: the HTTP transport, evidence collection,
the port-owner probe, the browser launcher, and ``_auth`` (reads immutable token/session snapshots).
The HTTP dispatcher (``_dispatch``) and therefore every MCP tool runs only inside ``OnFrame``.

Phase 1B:
- durable, hash-chained SQLite audit in ``Datadir``; every MCP call fails closed without it;
- hashed agent tokens created/revoked only from a paired admin session (approved inside TD);
- ``Enablemcp`` (with preconditions) and ``Suspendagents`` (kill switch, queued requests invalidated);
- 16 tools, deny-by-default policy, no arbitrary Python execution.
"""
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


class TDflowMCPExt:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self._main = threading.main_thread()
        self.manifest = ownerComp.op("loader").module.install_from_comp(ownerComp)
        from tdflowmcp import __version__, admin, compat, controller, engine, identity, policy, protocol, setupapi, \
            store, tellagent, tools
        from tdflowmcp.net import bindverify, evidence, oslisteners
        from tdflowmcp.net.pyhttp import EmbeddedHTTPTransport, Response, error
        from tdflowmcp_td import tdops
        self._m = types.SimpleNamespace(compat=compat, controller=controller, setupapi=setupapi, tellagent=tellagent,
                                        evidence=evidence, oslisteners=oslisteners, bindverify=bindverify,
                                        Transport=EmbeddedHTTPTransport, Response=Response, error=error, admin=admin,
                                        engine=engine, identity=identity, policy=policy, protocol=protocol,
                                        store=store, tools=tools, tdops=tdops)
        self.version = __version__
        old = _REGISTRY.instances.get(ownerComp.id)
        if old is not None and old is not self:
            try:
                old.Shutdown("reinit")
            except Exception:
                pass
        _REGISTRY.instances[ownerComp.id] = self
        self.troubleshooting = json.loads(ownerComp.op("troubleshooting").text)
        self.td_build = str(td.app.build)
        self.checks, self.checks_at = [], 0.0
        self.dispatch_threads = {"main": 0, "other": 0}
        self.frame_errors = 0
        self.last_frame_error = ""
        self.launcher = self._default_launcher          # replaceable by in-TD tests (must stay TD-free)
        self.clipboard_writer = self._default_clipboard  # main thread only
        self.tell_text = ""
        self.note, self._note_until = "", 0.0
        self._static_facts = None
        self._published = {}
        self._shut = False
        # Phase 1B state (main thread). _auth reads only self.identity/self.admin snapshots.
        self.admin = admin.AdminAuth()
        self.store = None
        self.identity = None
        self.engine = None
        self.registry = tools.core_registry()
        self.active_tools = tuple(self.manifest.get("active_tools", ()))
        self.mcp_on = False
        self._mcp_wanted = False
        self.suspended = False
        self._auth_fail_seen, self._last_burst = 0, 0.0
        self.store_error = ""
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
        t = self._m.Transport("127.0.0.1", port, state_fn=state_fn, authenticator=self._auth, mcp_enabled=False,
                              heartbeat_timeout=20.0, job_timeout=5.0, dispatch_grace=5.0)
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

    # ---- durable state (main thread) -----------------------------------------------------------
    def datadir(self):
        override = os.environ.get("TDFLOW_DATADIR")
        if override:
            return override, "(TDFLOW_DATADIR override)"
        key = hashlib.sha256((str(td.project.folder) + "/" + str(td.project.name)).encode("utf-8")).hexdigest()[:16]
        lad = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(lad, "TDflowMCP", key), "%LOCALAPPDATA%\\TDflowMCP\\" + key

    def ensure_store(self):
        """Open the store (and identity/engine) on first need. Returns False if unavailable."""
        S = self._m.store
        if self.store is not None and self.store.available:
            return True
        try:
            self.store = S.Store(self.datadir()[0]).open()
            self.identity = self._m.identity.Identity(self.store)
            self.store_error = ""
        except S.AuditUnavailable as e:
            self.store, self.identity, self.engine = None, None, None
            self.store_error = str(e) or "unavailable"
            return False
        self._build_engine()
        return True

    def _build_engine(self):
        root = str(self.ownerComp.par.Projectroot.eval()).rstrip("/") or "/project1"
        try:
            pol = self._m.policy.Policy(root)
        except ValueError:
            self.engine = None
            return
        self.tdops = self._m.tdops.TDAdapter(self.ownerComp, lambda: pol.root, lambda: self.store)
        self.engine = self._m.engine.Engine(self.store, self.identity, pol, self.registry, self.tdops,
                                            self.active_tools, lambda: self.suspended,
                                            on_audit_failure=self._audit_failed)

    def _audit_failed(self):
        """An executed change could not be recorded: stop all agents until the user acts (fail closed)."""
        self._set_suspended(True)
        if not self.ownerComp.par.Suspendagents.eval():
            self.ownerComp.par.Suspendagents.val = True
        self._set_note("The audit store failed while recording a change, so all agents were suspended.", 60)

    def _audit(self, kind, **fields):
        """Audit an administrative event. Returns False (and the caller must refuse) if unavailable."""
        if not self.ensure_store():
            return False
        try:
            self.store.append(kind, **fields)
            return True
        except self._m.store.AuditUnavailable:
            return False

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
        dd = self.datadir()[0]
        parent = dd
        while parent and not os.path.isdir(parent):
            nxt = os.path.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
        return {
            "td_build": self.td_build, "tox_saved_build": self.manifest.get("built_with_td"),
            "python_version": tuple(sys.version_info[:3]), "modules": sf["modules"],
            "transport": "python-http", "bind_host": "127.0.0.1",
            "exclusive_bind": hasattr(socket, "SO_EXCLUSIVEADDRUSE") if sys.platform == "win32" else True,
            "bind_evidence": ev, "port": ctl.port, "port_listeners_other": others,
            "port_probe_tdflowmcp": "TDflowMCP" in (ctl.portstatus or ""),
            "datadir_writable": bool(parent and os.path.isdir(parent) and os.access(parent, os.W_OK)),
            "instances": sf["instances"], "selftest": sf["selftest"],
        }

    def RunDiagnostics(self, full=True):
        self.checks = self._m.compat.run_checks(self._facts(full))
        self.checks_at = time.monotonic()
        self._refresh_tell()
        self._publish()
        return self.checks

    def _refresh_tell(self):
        ctl = self.ctl
        self.tell_text = self._m.tellagent.build(self._state_value(), ctl.error_code, ctl.port, self.td_build,
                                                 self.checks, self.troubleshooting, self.version)
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
            self.RunDiagnostics()
            other = self._local_port_user(self.ctl.port)
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
        elif n in ("Approvepairing", "Rejectpairing"):
            self._pairing_decision(n == "Approvepairing")
        self._refresh_tell()
        self._publish()

    def OnValueChange(self, par, prev):
        n = par.name
        if n == "Port":
            self.ctl.set_port(int(par.eval()))
        elif n == "Enablemcp":
            self._set_mcp_wanted(bool(par.eval()))
        elif n == "Suspendagents":
            self._set_suspended(bool(par.eval()))
        elif n == "Projectroot" and self.store is not None:
            self._build_engine()
            if self.mcp_on and self.engine is None:
                self._mcp_off("Project Root is not a valid operator path; MCP was turned off.")
        self._refresh_tell()
        self._publish()

    def _pairing_decision(self, approve):
        pend = self.admin.pending_view()
        if not pend or pend["state"] != "pending":
            self._set_note("There is no pending pairing request.")
            return
        if not self._audit("admin", tool="pairing", outcome="approved" if approve else "rejected",
                           detail={"match_code": pend["code"]}):
            self._set_note("Pairing was not approved: the audit store is unavailable.")
            return
        ok = self.admin.approve() if approve else self.admin.reject()
        self._set_note(("Pairing approved for " if approve else "Pairing rejected for ") + pend["code"] + "." if ok
                       else "The pairing request expired.")

    def _set_suspended(self, value):
        if value == self.suspended:
            return
        self.suspended = value
        t = self.ctl.transport
        if t is not None:
            t.suspended = value                 # queued MCP jobs are re-checked at dispatch (pump)
        self._audit("kill_switch", outcome="on" if value else "off")
        self._set_note("All agents suspended." if value else "Agents resumed.")

    def _set_mcp_wanted(self, value):
        self._mcp_wanted = value
        if not value:
            if self.mcp_on:
                self._mcp_off("MCP disabled by the user.")
            return
        CS = self._m.controller.CompState
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
        """Main thread, once the listener is verified (Setup state): check preconditions, then enable."""
        self.RunDiagnostics(full=False)
        if not self.ensure_store():
            return self._refuse_mcp("The audit store is unavailable, so MCP stays disabled (fail closed).")
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
        if not self._audit("mcp", outcome="enabled", detail={"root": self.engine.policy.root,
                                                            "tools": list(self.active_tools)}):
            return self._refuse_mcp("The audit store is unavailable, so MCP stays disabled (fail closed).")
        self.tdops.refresh_protected()
        try:
            self.store.checkpoint()                    # anchor the chain at every enable
        except self._m.store.AuditUnavailable:
            return self._refuse_mcp("The audit store is unavailable, so MCP stays disabled (fail closed).")
        self.ctl.transport.mcp_enabled = True
        self.mcp_on = True
        self._set_note(f"MCP enabled at http://127.0.0.1:{self.ctl.port}/mcp.")

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
            self._audit("mcp", outcome="disabled", detail={"reason": why[:200]})
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
        self.note, self._note_until = text, time.monotonic() + seconds

    def CopyTellAgent(self):
        self._refresh_tell()
        try:
            ok = bool(self.clipboard_writer(self.tell_text))
        except Exception:
            ok = False
        self._set_note("Tell Your Coding Agent text copied to the clipboard." if ok else
                       "Could not copy to the clipboard. Copy the text from the Setup page or from the "
                       "'tellagent' DAT inside the component.")
        return ok

    # ---- per frame (main thread) ----------------------------------------------------------------
    def OnFrame(self):
        if self._shut:
            return
        try:
            CS = self._m.controller.CompState
            # Mirror the kill switch before dispatching anything this frame: parameter callbacks may be
            # deferred (review 6), and a queued request must never run after the switch was flipped.
            want = bool(self.ownerComp.par.Suspendagents.eval())
            if want != self.suspended:
                self._set_suspended(want)
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
            self._aggregate_auth_failures()
            self._publish()
        except Exception as e:  # never break TouchDesigner's frame
            self.frame_errors += 1
            self.last_frame_error = type(e).__name__

    def _aggregate_auth_failures(self):
        """One auth_fail_burst event per 10 s window (security-model §6), never one write per request."""
        t = self.ctl.transport
        if t is None or self.store is None or time.monotonic() - self._last_burst < 10:
            return
        n = t.stats().get("auth_fail", 0)
        if n > self._auth_fail_seen:
            self._audit("auth_fail_burst", detail={"count": n - self._auth_fail_seen})
        self._auth_fail_seen, self._last_burst = n, time.monotonic()

    def _dispatch(self, job):
        """Main thread only (called from transport.pump inside OnFrame)."""
        is_main = threading.current_thread() is self._main
        self.dispatch_threads["main" if is_main else "other"] += 1
        if not is_main:
            return self._m.error(500, "OFF_MAIN_THREAD")
        R, E = self._m.Response, self._m.error
        p, m = job.path, job.method
        if job.route == "mcp":
            return self._dispatch_mcp(job)
        if m == "GET" and p == "/setup/api/status":
            return R(200, self._m.setupapi.dumps(self._build_status()), "application/json")
        if m == "GET" and p == "/setup/api/tellagent":
            self._refresh_tell()
            return R(200, self.tell_text.encode("utf-8"), "text/plain; charset=utf-8")
        if job.route == "pair":
            return self._dispatch_pair(job)
        if job.principal and str(job.principal).startswith("admin:"):
            return self._dispatch_admin(job)
        return E(404, "NOT_FOUND")

    # -- MCP --
    def _dispatch_mcp(self, job):
        R = self._m.Response
        if not self.mcp_on or self.engine is None:
            return self._m.error(503, "MCP_DISABLED")
        tid = str(job.principal or "")[len("agent:"):]
        eng = self.engine
        proto = self._m.protocol

        class Host(proto.ToolHost):
            def list_tools(self_inner):
                return eng.list_tools(tid)

            def call_tool(self_inner, name, arguments, client_info):
                return eng.call(tid, name, arguments, client_info, request_id=str(job.rid))
        status, body, headers = proto.handle(job.body, job.headers, Host())
        return R(status, body or b"", "application/json", tuple(headers.items()))

    # -- pairing --
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
            self._audit("admin", tool="pairing", outcome="requested", detail={"match_code": code})
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

    # -- admin (paired session) --
    def _admin_body(self, job):
        if len(job.body) > MAX_ADMIN_BODY:
            return None
        try:
            v = json.loads(job.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return None
        return v if isinstance(v, dict) else None

    def _dispatch_admin(self, job):
        R, E = self._m.Response, self._m.error
        D = self._m.setupapi.dumps
        p, m = job.path, job.method
        if m == "GET" and p == "/setup/api/agents":
            if not self.ensure_store():
                return E(503, "AUDIT_UNAVAILABLE")
            now = time.monotonic()
            if getattr(self, "_verify_cache", None) is None or now - self._verify_cache[0] > 10:
                self._verify_cache = (now, self.store.verify())    # full re-verify at most every 10 s
            return R(200, D({"agents": self.identity.public_list(), "sessions": self.store.sessions(limit=10),
                             "mcp": self._state_value(), "project_root": str(self.ownerComp.par.Projectroot.eval()),
                             "endpoint": f"http://127.0.0.1:{self.ctl.port}/mcp",
                             "audit": self._verify_cache[1]}), "application/json")
        if m != "POST":
            return E(405, "METHOD_NOT_ALLOWED")
        body = self._admin_body(job)
        if body is None:
            return E(400, "BAD_REQUEST")
        if p == "/setup/api/logout":
            self.admin.logout(str(job.principal)[len("admin:"):])   # ends the session on the server
            self._audit("admin", tool="logout")
            return R(200, D({"ok": True}), "application/json",
                     (self._m.admin.clear_cookie(self._m.admin.SESSION_COOKIE, "/"),))
        if not self.ensure_store():
            return E(503, "AUDIT_UNAVAILABLE")
        if p == "/setup/api/tokens":
            return self._create_token(body)
        if p == "/setup/api/tokens/revoke":
            tid = str(body.get("token_id", ""))[:32]
            if not self._audit("admin", tool="revoke_token", detail={"token_id": tid}):
                return E(503, "AUDIT_UNAVAILABLE")
            ok = self.identity.revoke(tid)
            return R(200 if ok else 404, D({"revoked": ok}), "application/json")
        return E(404, "NOT_FOUND")

    def _create_token(self, body):
        R, E, D = self._m.Response, self._m.error, self._m.setupapi.dumps
        root = str(self.ownerComp.par.Projectroot.eval()).rstrip("/")
        name = str(body.get("display_name", ""))[:64]
        role = str(body.get("role", "builder"))
        scope = str(body.get("scope") or (root + "/**"))
        actions = body.get("actions")
        if role not in ("observer", "builder"):
            return E(400, "ROLE_NOT_ALLOWED")               # 'operator' adds only execute, unavailable in 1B
        if not (scope == root + "/**" or scope.startswith(root + "/")):
            return E(400, "SCOPE_OUTSIDE_PROJECT_ROOT")
        if not self._audit("admin", tool="create_token", detail={"display_name": name, "role": role, "scope": scope,
                                                                "actions": actions}):
            return E(503, "AUDIT_UNAVAILABLE")
        try:
            agent, token = self.identity.create_agent(name, role, [scope])
            if isinstance(actions, list) and actions:
                acts = sorted(set(str(a) for a in actions) & set(self._m.policy.ROLES[role]))
                if not acts:
                    raise ValueError("no allowed actions")
                self.store.conn.execute("UPDATE grants SET actions=? WHERE agent_id=?", (",".join(acts), agent.id))
                self.identity.reload()
                agent = self.identity.agents[agent.id]
        except ValueError as e:
            return E(400, "INVALID_" + "ARGS")
        self._publish()
        return R(200, D({"agent": {"id": agent.id, "display_name": agent.display_name, "role": agent.role,
                                   "scopes": [{"scope": g.scope, "actions": sorted(g.actions)} for g in agent.grants]},
                         "token": token, "shown_once": True,
                         "env_hint": "Store it as the TDFLOW_TOKEN environment variable. It is shown only once."}),
                 "application/json")

    def _build_status(self):
        self._refresh_tell()
        s = self._m.setupapi.status(self.ctl.snapshot(), self.td_build, self.checks, self.troubleshooting,
                                    self.tell_text, time.monotonic() - self.checks_at, self.version)
        s["mcp"] = {"Enabled": "enabled", "Suspended": "suspended (kill switch)"}.get(self._state_value(), "disabled")
        s["state"] = self._state_value()
        s["endpoint"] = f"http://127.0.0.1:{self.ctl.port}/mcp" if self.mcp_on else ""
        pend = self.admin.pending_view()
        s["pairing"] = {"pending": bool(pend and pend["state"] == "pending"), "admin_sessions": self.admin.active_sessions()}
        return s

    def _publish(self):
        p, ctl = self.ownerComp.par, self.ctl
        pend = self.admin.pending_view()
        n_agents = len(self.identity.agents) if self.identity else 0
        vals = {
            "Status": self._state_value() + (f": {ctl.error_code}" if ctl.error_code else ""),
            "Statusdetail": ctl.message + (f" {self.note}" if self.note and time.monotonic() < self._note_until else ""),
            "Setupurl": ctl.url if ctl.state is self._m.controller.CompState.SETUP else "",
            "Portstatus": ctl.portstatus,
            "Compatsummary": self._m.compat.summary(self.checks) if self.checks else "not run",
            "Tellagent": self._m.tellagent.one_line(ctl.state.value, ctl.error_code, self.checks, self.version),
            "Pairingrequest": (f"Pending: {pend['code']} ({pend['wall']})" if pend and pend["state"] == "pending" else ""),
            "Agentsummary": f"{n_agents} agent(s) · {self.admin.active_sessions()} admin session(s)",
            "Datadir": self.datadir()[1],
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
            if self.store is not None:
                try:
                    self.store.checkpoint()
                except Exception:
                    pass
                self.store.close()
            if _REGISTRY.instances.get(self.ownerComp.id) is self:
                _REGISTRY.instances.pop(self.ownerComp.id, None)

    def onDestroyTD(self):
        self.Shutdown("destroyed")

    def Diagnostics(self):
        """Read-only internals for the in-TD test runner and support (no secrets)."""
        t = self.ctl.transport
        return {"state": self._state_value(), "error_code": self.ctl.error_code, "gate": self.ctl.gate.state.value,
                "port": self.ctl.port, "reverifies": self.ctl.reverifies, "launch": self.ctl.launch_status,
                "dispatch_threads": dict(self.dispatch_threads), "frame_errors": self.frame_errors,
                "last_frame_error": self.last_frame_error, "events": list(self.ctl.events)[-20:],
                "transport_stats": t.stats() if t else None, "version": self.version, "mcp_on": self.mcp_on,
                "suspended": self.suspended, "store": bool(self.store and self.store.available),
                "store_error": self.store_error, "source_digest": self.manifest.get("source_digest")}
