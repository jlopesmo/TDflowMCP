"""TDflowMCPExt: the TouchDesigner extension of TDflowMCP.tox (TD adapter, Phase 1A).

Thin glue between TouchDesigner and the pure core (``tdflowmcp.controller`` and friends):
- every method here runs on TouchDesigner's main thread (extension init, parameter callbacks, the
  per-frame ``OnFrame`` from ./frame_exec, ``Shutdown`` from onExit / onDestroyTD);
- the only code handed to other threads is TD-free: the HTTP transport, evidence collection, the
  port-owner probe and the browser launcher (``webbrowser``), each by the core's ``Background``;
- the HTTP dispatcher (``_dispatch``) is called only by ``transport.pump`` inside ``OnFrame``.

Phase 1A scope: Setup Assistant (read-only) and diagnostics. MCP stays disabled (503 MCP_DISABLED).
No agent configuration, installs, permission edits or code execution exist.
"""
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


class TDflowMCPExt:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self._main = threading.main_thread()
        self.manifest = ownerComp.op("loader").module.install_from_comp(ownerComp)
        from tdflowmcp import __version__, compat, controller, setupapi, tellagent  # embedded modules
        from tdflowmcp.net import bindverify, evidence, oslisteners
        from tdflowmcp.net.pyhttp import EmbeddedHTTPTransport, Response, error
        self._m = types.SimpleNamespace(compat=compat, controller=controller, setupapi=setupapi,
                                        tellagent=tellagent, evidence=evidence, oslisteners=oslisteners, bindverify=bindverify,
                                        Transport=EmbeddedHTTPTransport, Response=Response, error=error)
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
        self.note, self._note_until = "", 0.0       # transient result (e.g. clipboard), shown after the message
        self._static_facts = None                    # cached module/instance facts (review 5: no per-state hitch)
        self._published = {}
        self._shut = False
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
        self._refresh_tell()
        self._publish()

    # ---- dependencies (main thread) ------------------------------------------------------------
    def _make_transport(self, port, state_fn):
        t = self._m.Transport("127.0.0.1", port, state_fn=state_fn, mcp_enabled=False,
                              heartbeat_timeout=20.0, job_timeout=2.0, dispatch_grace=2.0)
        t.set_static(self._static_assets())
        return t

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
            self._static_facts = {"modules": mods, "instances": instances}
        mods, instances = self._static_facts["modules"], self._static_facts["instances"]
        ctl = self.ctl
        CS = self._m.controller.CompState
        ev = self._m.bindverify.redacted(ctl.gate.last) if (ctl.state is CS.SETUP or
                                         (ctl.state is CS.ERROR and ctl.error_code == "BIND_UNVERIFIED")) else None
        others = self._m.oslisteners.other_listeners(ctl.port, os.getpid())
        lad = os.environ.get("LOCALAPPDATA")
        return {
            "td_build": self.td_build, "tox_saved_build": self.manifest.get("built_with_td"),
            "python_version": tuple(sys.version_info[:3]), "modules": mods,
            "transport": "python-http", "bind_host": "127.0.0.1",
            "exclusive_bind": hasattr(socket, "SO_EXCLUSIVEADDRUSE") if sys.platform == "win32" else True,
            "bind_evidence": ev, "port": ctl.port, "port_listeners_other": others,
            "port_probe_tdflowmcp": "TDflowMCP" in (ctl.portstatus or ""),
            "datadir_writable": bool(lad and os.path.isdir(lad) and os.access(lad, os.W_OK)),
            "instances": instances,
        }

    def RunDiagnostics(self, full=True):
        self.checks = self._m.compat.run_checks(self._facts(full))
        self.checks_at = time.monotonic()
        self._refresh_tell()
        self._publish()
        return self.checks

    def _refresh_tell(self):
        ctl = self.ctl
        self.tell_text = self._m.tellagent.build(ctl.state.value, ctl.error_code, ctl.port, self.td_build,
                                                 self.checks, self.troubleshooting, self.version)
        dat = self.ownerComp.op("tellagent")
        if dat is not None and dat.text != self.tell_text:
            dat.text = self.tell_text

    # ---- parameter callbacks (main thread) ------------------------------------------------------
    def OnPulse(self, par):
        n = par.name
        if n == "Opensetup":
            self.ctl.apply_pending_port()           # diagnose the port that will actually be used
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
        self._refresh_tell()
        self._publish()

    def _local_port_user(self, port):
        """Another TDflowMCP instance in this process holding (or still closing) the port."""
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

    def OnValueChange(self, par, prev):
        if par.name == "Port":
            self.ctl.set_port(int(par.eval()))
            self._publish()

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
            before = (self.ctl.state, self.ctl.error_code)
            self.ctl.tick(self._dispatch)
            if (self.ctl.state, self.ctl.error_code) != before:
                if self.checks and self.ctl.state.value in ("Setup", "Error", "Off"):
                    self.RunDiagnostics(full=False)          # keep net.bind / net.port current
                self._refresh_tell()
            self._publish()
        except Exception as e:  # never break TouchDesigner's frame
            self.frame_errors += 1
            self.last_frame_error = type(e).__name__

    def _dispatch(self, job):
        """Main thread only (called from transport.pump inside OnFrame)."""
        is_main = threading.current_thread() is self._main
        self.dispatch_threads["main" if is_main else "other"] += 1
        if not is_main:
            return self._m.error(500, "OFF_MAIN_THREAD")
        R = self._m.Response
        if job.method == "GET" and job.path == "/setup/api/status":
            return R(200, self._m.setupapi.dumps(self._build_status()), "application/json")
        if job.method == "GET" and job.path == "/setup/api/tellagent":
            self._refresh_tell()
            return R(200, self.tell_text.encode("utf-8"), "text/plain; charset=utf-8")
        return self._m.error(404, "NOT_FOUND")

    def _build_status(self):
        self._refresh_tell()
        return self._m.setupapi.status(self.ctl.snapshot(), self.td_build, self.checks, self.troubleshooting,
                                       self.tell_text, time.monotonic() - self.checks_at, self.version)

    def _publish(self):
        p, ctl = self.ownerComp.par, self.ctl
        vals = {
            "Status": ctl.status_line(),
            "Statusdetail": ctl.message + (f" {self.note}" if self.note and time.monotonic() < self._note_until else ""),
            "Setupurl": ctl.url if ctl.state is self._m.controller.CompState.SETUP else "",
            "Portstatus": ctl.portstatus,
            "Compatsummary": self._m.compat.summary(self.checks) if self.checks else "not run",
            "Tellagent": self._m.tellagent.one_line(ctl.state.value, ctl.error_code, self.checks, self.version),
        }
        for k, v in vals.items():
            if self._published.get(k) != v:
                getattr(p, k).val = v
                self._published[k] = v

    # ---- lifecycle (main thread) ----------------------------------------------------------------
    def Shutdown(self, reason="exit"):
        """Stop the listener now (project exit, component deleted, extension re-initialised)."""
        t = self.ctl.transport
        if t is not None:
            self.ctl.stop(reason)
            if reason in ("exit", "destroyed", "reinit"):
                _REGISTRY.closing.append(t)       # lets a successor report "still closing" honestly
        self._shut = reason in ("exit", "destroyed", "reinit")
        if _REGISTRY.instances.get(self.ownerComp.id) is self and self._shut:
            _REGISTRY.instances.pop(self.ownerComp.id, None)

    def onDestroyTD(self):
        self.Shutdown("destroyed")

    def Diagnostics(self):
        """Read-only internals for the in-TD test runner and support (no secrets)."""
        t = self.ctl.transport
        return {"state": self.ctl.state.value, "error_code": self.ctl.error_code, "gate": self.ctl.gate.state.value,
                "port": self.ctl.port, "reverifies": self.ctl.reverifies, "launch": self.ctl.launch_status,
                "dispatch_threads": dict(self.dispatch_threads), "frame_errors": self.frame_errors,
                "last_frame_error": self.last_frame_error, "events": list(self.ctl.events)[-20:],
                "transport_stats": t.stats() if t else None, "version": self.version,
                "source_digest": self.manifest.get("source_digest")}
