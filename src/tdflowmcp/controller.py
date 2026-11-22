"""Component controller (setup-assistant §1): Off · Setup · Error, plus reserved Enabled/Suspended.

Pure Python, no `td`. The TD adapter calls every method on TouchDesigner's main thread:
``open_setup`` / ``stop`` / ``set_port`` from parameter callbacks, ``tick`` once per frame. TD-free
dependencies are injected (transport factory, evidence collector, OS table readers, browser
launcher, clock), so the whole state machine is unit-tested without TouchDesigner.

Rules enforced here:
- never listens before a user action (``open_setup``);
- pre-activation: exclusive 127.0.0.1 capability + no other process on the port (else Error);
- VERIFYING until positive evidence; failure closes at once; CLOSED needs a new user action;
- the browser is launched only after SERVING;
- periodic re-verification while serving; idle auto-stop; orphan/health checks;
- the port is never changed automatically; a Port change while active stops the listener;
- Enabled/Suspended exist only as reserved names: MCP stays disabled in Phase 1A.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from tdflowmcp.net import bindverify as bv
from tdflowmcp.net.pyhttp import TransportError

HOST = "127.0.0.1"


class CompState(str, Enum):
    OFF = "Off"
    STARTING = "Starting"      # listener exists, gate VERIFYING (503 to every request)
    SETUP = "Setup"            # verified loopback-only; Setup Assistant served; MCP disabled
    STOPPING = "Stopping"      # closing; waiting for the OS table to confirm
    ERROR = "Error"
    ENABLED = "Enabled"        # reserved for Phase 1B (never entered in 1A)
    SUSPENDED = "Suspended"    # reserved for Phase 1B (never entered in 1A)


class Background:
    """Run ``fn(*args)`` on a daemon thread (pure work only: never pass anything that touches TD)."""

    def __init__(self, fn: Callable, *args, name: str = "tdflowmcp-bg"):
        self._result: Any = None
        self._error: Optional[BaseException] = None
        self._t = threading.Thread(target=self._run, args=(fn, args), name=name, daemon=True)
        self._t.start()

    def _run(self, fn, args):
        try:
            self._result = fn(*args)
        except BaseException as e:  # reported to the main thread, never raised there
            self._error = e

    def done(self) -> bool:
        return not self._t.is_alive()

    def result(self):
        return self._result if self._error is None else None

    @property
    def error(self):
        return self._error


@dataclass
class Deps:
    make_transport: Callable[[int, Callable[[], bv.ListenerState]], Any]
    start_evidence: Callable[[int, Optional[str]], Any]      # -> object with done()/result()
    own_listeners: Callable[[int], Optional[tuple]]           # (v4, v6) of this process, or None
    other_listeners: Callable[[int], Optional[list]]          # rows of other processes, or None
    probe_owner: Callable[[int], Any]                         # -> Background(responds_like_tdflowmcp)
    launch: Callable[[str], Any]                              # -> object with done()/result() -> bool
    now: Callable[[], float] = time.monotonic


class Controller:
    reverify_seconds = 10.0
    idle_seconds = 30 * 60.0
    evidence_timeout = 5.0             # review 5: a hung evidence check counts as ambiguous

    def __init__(self, deps: Deps, port: int = 9480):
        self.d = deps
        self.port = int(port)
        self.pending_port: Optional[int] = None
        self.gate = bv.BindGate()
        self.state = CompState.OFF
        self.error_code = ""
        self.message = "Off. Pulse Open Setup to start the local Setup Assistant."
        self.portstatus = "not checked"
        self.transport = None
        self._ev = None
        self._ev_started = 0.0
        self._last_verify = 0.0
        self._launch_wanted = False
        self._launch = None
        self.launch_status = ""           # '', 'launched', 'failed: …'
        self._owner_probe = None
        self.reverifies = 0
        self.events: deque = deque(maxlen=100)
        self._close_reason = ""

    # ---- helpers ------------------------------------------------------------------------------
    def _log(self, kind: str, **kw):
        self.events.append(dict(t=round(self.d.now(), 3), kind=kind, **kw))

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/setup/"

    @property
    def active(self) -> bool:
        return self.state in (CompState.STARTING, CompState.SETUP, CompState.STOPPING)

    def _start_ev(self, host):
        self._ev, self._ev_started = self.d.start_evidence(self.port, host), self.d.now()

    def _ev_outcome(self, now):
        """None while the evidence check is still running within its time limit; else the evidence."""
        if self._ev.done():
            return self._ev.result() or bv.Evidence(bv.Verdict.AMBIGUOUS, ("evidence_error",))
        if now - self._ev_started > self.evidence_timeout:
            return bv.Evidence(bv.Verdict.AMBIGUOUS, ("evidence_timeout",))
        return None

    def apply_pending_port(self):
        if self.pending_port is not None and not self.active:
            self.port, self.pending_port = self.pending_port, None

    def refuse(self, code: str, message: str, portstatus: str = ""):
        """Adapter-detected refusal before activation (e.g. another TDflowMCP component in this process)."""
        if self.active:
            return
        if portstatus:
            self.portstatus = portstatus
        self._error(code, message)

    def _error(self, code: str, message: str):
        self.state, self.error_code, self.message = CompState.ERROR, code, message
        self._log("error", code=code)

    # ---- user actions (main thread) -------------------------------------------------------------
    def open_setup(self, launch: bool = True) -> str:
        if self.state is CompState.SETUP:
            self._launch_wanted = self._launch_wanted or launch      # re-open the page
            return "launch"
        if self.state is CompState.STARTING:
            self._launch_wanted = self._launch_wanted or launch
            return "pending"
        if self.state is CompState.STOPPING:
            self.message = "Still stopping the previous listener. Pulse Open Setup again in a moment."
            return "busy"
        if self.gate.state is bv.ListenerState.STUCK:
            self.message = ("The previous listener socket did not close. Restart TouchDesigner before "
                            "starting the Setup Assistant again.")
            return "stuck"
        if self.pending_port is not None:
            self.port, self.pending_port = self.pending_port, None
        others = self.d.other_listeners(self.port)
        act = self.gate.request_activation(bv.embedded_capability(HOST), self.d.now(), user_initiated=True)
        if act != "activate_verifying":
            self._error(self.gate.error_code or "BIND_UNSUPPORTED", "The listener cannot be started on this build.")
            return "refused"
        if others is None:
            self.gate.on_bind_error("BIND_UNVERIFIED")
            self.portstatus = "unknown (OS listener table unavailable)"
            self._error("BIND_UNVERIFIED", "Port ownership could not be verified, so no listener was started.")
            return "refused"
        if others:
            self.gate.on_bind_error("PORT_IN_USE")
            self.portstatus = "in use by another process"
            self._owner_probe = self.d.probe_owner(self.port)
            self._error("PORT_IN_USE", f"Port {self.port} is already in use. Close the other program or choose "
                        "another Port, then pulse Open Setup. TDflowMCP never changes the port by itself.")
            return "refused"
        t = self.d.make_transport(self.port, lambda: self.gate.state)
        try:
            bound = t.start()
        except TransportError as e:
            self.gate.on_bind_error(e.code)
            self.portstatus = "in use by another process" if e.code == "PORT_IN_USE" else "bind failed"
            self._error(e.code, f"The listener could not bind 127.0.0.1:{self.port} ({e.code}).")
            return "refused"
        self.transport = t
        self.portstatus = "this component (verifying)"
        self.state, self.error_code = CompState.STARTING, ""
        self.message = "Verifying that the listener is reachable from this computer only…"
        self._launch_wanted = launch
        self.launch_status = ""
        self._start_ev(bound[0])
        self._log("starting", port=self.port)
        return "starting"

    def stop(self, reason: str = "user") -> str:
        if self.state not in (CompState.STARTING, CompState.SETUP):
            if self.state is CompState.ERROR and reason == "user" and self.transport is None:
                self.state, self.error_code = CompState.OFF, ""
                self.message = "Off. Pulse Open Setup to start the local Setup Assistant."
            return "noop"
        self.gate.user_close()
        self.transport.begin_close()
        self._close_reason = reason
        self.state = CompState.STOPPING
        self._launch_wanted = False
        self.message = "Stopping the listener…"
        self._log("stopping", reason=reason)
        return "stopping"

    def set_port(self, port: int) -> str:
        port = int(port)
        if not (1024 <= port <= 65535):
            self.message = f"Port {port} is not allowed (use 1024–65535)."
            return "rejected"
        if port == self.port and self.pending_port is None:
            return "noop"
        if self.active:
            self.pending_port = port
            if self.state is not CompState.STOPPING:
                self.stop("port_changed")
            return "stopping"
        self.port, self.pending_port = port, None
        self.portstatus = "not checked"
        if self.state is CompState.ERROR and self.error_code == "PORT_IN_USE":
            self.state, self.error_code = CompState.OFF, ""
        self.message = f"Port set to {port}. Pulse Open Setup to start."
        return "set"

    # ---- per frame (main thread) --------------------------------------------------------------
    def tick(self, dispatch: Callable) -> None:
        now = self.d.now()
        t = self.transport
        if t is not None and self.state in (CompState.STARTING, CompState.SETUP):
            t.pump(dispatch)
        if self._owner_probe is not None and self._owner_probe.done():
            v = self._owner_probe.result()
            if v and self.state is CompState.ERROR and self.error_code == "PORT_IN_USE":
                self.portstatus = f"in use (responds like TDflowMCP v{v}; unverified)"
            self._owner_probe = None
        if self._launch is not None and self._launch.done():
            ok = self._launch.result()
            self.launch_status = "launched" if ok else "failed"
            if self.state is CompState.SETUP:
                self.message = (f"Setup Assistant opened in your browser: {self.url} (this computer only)." if ok else
                                f"Setup is running, but the browser could not be opened automatically. "
                                f"Open {self.url} yourself.")
            self._launch = None
        if self.state is CompState.SETUP and self._launch_wanted and self._launch is None:
            self._start_launch()                            # Open Setup pulsed again while serving
        if self.state is CompState.STARTING:
            self._tick_verifying(now)
        elif self.state is CompState.SETUP:
            self._tick_serving(now)
        elif self.state is CompState.STOPPING:
            self._tick_stopping()

    def _tick_verifying(self, now):
        ev = self._ev_outcome(now)
        if ev is None:
            return
        act = self.gate.on_evidence(ev, now=now)
        self._log("evidence", verdict=ev.verdict.value, reasons=list(bv.redacted(ev).reasons), action=act)
        if act == "serve":
            self._ev = None                                         # startup evidence is consumed
            self.state = CompState.SETUP
            self.portstatus = "this component"
            self._last_verify = now
            self.message = f"Setup Assistant running at {self.url} (this computer only)."
            if self._launch_wanted:
                self._start_launch()
        elif act == "recheck_later":
            self._start_ev((self.transport.sockname() or (None,))[0])
        else:                                                       # close_now, in this frame
            self.transport.begin_close()
            self._close_reason = "verify_failed"
            self.state = CompState.STOPPING
            self.message = "The listener could not be verified as local-only and was closed."

    def _tick_serving(self, now):
        t = self.transport
        if not t.healthy():
            self.gate.on_transport_failure()
            t.begin_close()
            self._close_reason = t.close_reason or "transport_failed"
            self.state = CompState.STOPPING
            self.message = "The listener stopped unexpectedly and is being closed."
            return
        if self._ev is None and now - self._last_verify >= self.reverify_seconds:
            self._start_ev((t.sockname() or (None,))[0])
        elif self._ev is not None and self._ev_outcome(now) is not None:
            ev = self._ev_outcome(now)
            self._ev, self._last_verify = None, now
            self.reverifies += 1
            if self.gate.on_reverify(ev) == "close_now":
                t.begin_close()
                self._close_reason = "reverify_failed"
                self.state = CompState.STOPPING
                self.message = "Re-verification failed; the listener was closed."
                self._log("reverify_failed", reasons=list(bv.redacted(ev).reasons))
                return
        if now - t.last_activity > self.idle_seconds:
            self.stop("idle")
            self.message = "Stopped after a long idle period."

    def _tick_stopping(self):
        t = self.transport
        if t is not None and not t.is_closed():
            return
        tab = self.d.own_listeners(self.port)
        v4, v6 = tab if tab is not None else (None, None)
        res = self.gate.confirm_closed(v4, v6, now=self.d.now())
        if res == "recheck_close":
            return
        self._log("closed", result=res, reason=self._close_reason, gate=self.gate.state.value)
        self.transport, self._ev = None, None
        if self.pending_port is not None:                 # review 5: apply on every close outcome
            self.port, self.pending_port = self.pending_port, None
        if res == "escalate":
            self._error("CLOSE_UNCONFIRMED", "The listener socket did not close. Restart TouchDesigner.")
        elif self.gate.error_code:
            code = self.gate.error_code
            msg = {"BIND_UNVERIFIED": "The listener could not be verified as local-only and was closed. "
                                      "Pulse Open Setup to try once more.",
                   "PORT_IN_USE": f"Another process started listening on port {self.port}; the listener was closed.",
                   "TRANSPORT_FAILED": "The listener stopped unexpectedly. Pulse Open Setup to start it again."}
            self._error(code, msg.get(code, "The listener was closed."))
        else:
            self.state, self.error_code = CompState.OFF, ""
            if self._close_reason == "port_changed":
                self.message = f"Port changed to {self.port}. Pulse Open Setup to start on the new port."
            elif self._close_reason == "idle":
                self.message = "Stopped after a long idle period. Pulse Open Setup to start again."
            else:
                self.message = "Off. Pulse Open Setup to start the local Setup Assistant."
        self.portstatus = "not checked" if self.state is CompState.OFF else self.portstatus

    def _start_launch(self):
        self._launch_wanted = False
        self.launch_status = "launching"
        self._launch = self.d.launch(self.url)

    # ---- status -------------------------------------------------------------------------------
    def status_line(self) -> str:
        return self.state.value + (f": {self.error_code}" if self.error_code else "")

    def snapshot(self) -> dict:
        t = self.transport
        return {"state": self.state.value, "error_code": self.error_code, "message": self.message,
                "port": self.port, "pending_port": self.pending_port,
                "url": self.url if self.state is CompState.SETUP else "",
                "portstatus": self.portstatus, "gate": self.gate.state.value, "reverifies": self.reverifies,
                "launch": self.launch_status, "mcp": "disabled (Phase 1A)",
                "transport": {k: v for k, v in (t.stats() if t else {}).items()
                              if k in ("dispatched", "busy", "timeouts", "active_connections", "queued",
                                       "auth_fail", "conn_rejected", "dispatch_errors")}}
