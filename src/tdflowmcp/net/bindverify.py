"""Bind-verification gate logic (security-model §8.2), pure and side-effect free.

Step 0 (pre-activation): the Web Server DAT must expose ``localaddress``; otherwise the listener is
never activated (S5: TouchDesigner 2025.32440 lacks it and binds 0.0.0.0) -> BIND_UNSUPPORTED.
Steps 1-4: the listener starts VERIFYING; only positive evidence of a loopback-only binding
(127.0.0.1 and/or ::1 for this process) yields ``pass``; non-loopback evidence is ``fail``; other
doubt is ``ambiguous``. A non-pass verdict means *close now*; closure is then confirmed on later
frames (CLOSING -> CLOSED). CLOSED is terminal: only an explicit user action may re-activate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence

LOOPBACK = frozenset({"127.0.0.1", "::1"})
REQUIRED_WEBSERVER_PARS = ("localaddress",)


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Evidence:
    verdict: Verdict
    reasons: tuple = ()
    details: Mapping = field(default_factory=dict)


def capability_check(webserver_par_names: Iterable[str]) -> Evidence:
    """Step 0: may the listener be activated at all on this build?"""
    names = set(webserver_par_names)
    missing = tuple(p for p in REQUIRED_WEBSERVER_PARS if p not in names)
    if missing:
        return Evidence(Verdict.FAIL, tuple(f"webserver_par_missing:{p}" for p in missing), {"missing": missing})
    return Evidence(Verdict.PASS, ("webserver_has_localaddress",))


def verdict(listeners_v4: Optional[Sequence[str]], listeners_v6: Optional[Sequence[str]],
            probes: Optional[Mapping[str, str]] = None) -> Evidence:
    """Decide from *this process's* listening addresses on the port plus non-loopback probes.

    ``listeners_*`` = None means the OS table was unavailable (-> ambiguous).
    ``probes``: non-loopback address -> 'connected' | 'refused' | 'timeout' | 'error:<n>'.
    A probe can only ever fail the verdict (S5: a timeout occurs even with nothing listening).
    """
    probes = dict(probes or {})
    if listeners_v4 is None or listeners_v6 is None:
        return Evidence(Verdict.AMBIGUOUS, ("listener_table_unavailable",), {"probes": probes})
    v4, v6 = list(listeners_v4), list(listeners_v6)
    bad = [a for a in v4 + v6 if a not in LOOPBACK]
    connected = sorted(a for a, r in probes.items() if r == "connected")
    details = {"v4": v4, "v6": v6, "probes": probes}
    if bad or connected:
        reasons = tuple(f"non_loopback_listener:{a}" for a in bad) + tuple(
            f"non_loopback_connect:{a}" for a in connected)
        return Evidence(Verdict.FAIL, reasons, details)
    if not v4 and not v6:
        return Evidence(Verdict.AMBIGUOUS, ("no_listener_for_process_yet",), details)
    return Evidence(Verdict.PASS, ("loopback_only",), details)


class ListenerState(str, Enum):
    OFF = "OFF"
    VERIFYING = "VERIFYING"
    SERVING = "SERVING"
    CLOSING = "CLOSING"        # deactivated; waiting for the OS to release the socket
    CLOSED = "CLOSED"          # terminal until an explicit user action (see error_code)
    STUCK = "STUCK"            # socket did not disappear after close: user must restart TouchDesigner


@dataclass
class BindGate:
    """State machine for one listener. Each method returns the action the TD adapter must perform now."""
    max_attempts: int = 10
    max_verify_seconds: float = 15.0
    max_close_checks: int = 10
    state: ListenerState = ListenerState.OFF
    error_code: str = ""       # 'BIND_UNSUPPORTED' | 'BIND_UNVERIFIED' | 'CLOSE_UNCONFIRMED'
    attempts: int = 0
    close_checks: int = 0
    started_at: Optional[float] = None
    last: Optional[Evidence] = None

    def request_activation(self, capability: Evidence, now: float = 0.0, user_initiated: bool = False) -> str:
        if self.state in (ListenerState.CLOSED, ListenerState.CLOSING, ListenerState.STUCK) and not user_initiated:
            return "remain_closed"
        if self.state in (ListenerState.CLOSING, ListenerState.STUCK):
            return "remain_closed"          # never re-open while an old socket may still exist
        if self.state in (ListenerState.VERIFYING, ListenerState.SERVING):
            return "noop"
        if capability.verdict is not Verdict.PASS:
            self.state, self.error_code, self.last = ListenerState.CLOSED, "BIND_UNSUPPORTED", capability
            return "do_not_activate"
        self.state, self.error_code = ListenerState.VERIFYING, ""
        self.attempts, self.close_checks, self.started_at = 0, 0, now
        return "activate_verifying"

    def _close(self, code: str) -> str:
        self.state, self.error_code, self.close_checks = ListenerState.CLOSING, code, 0
        return "close_now"

    def on_evidence(self, ev: Evidence, now: float = 0.0) -> str:
        """'serve' | 'close_now' | 'recheck_later' | 'noop'. 'close_now' must run in the same callback."""
        if self.state is not ListenerState.VERIFYING:
            return "noop"
        self.attempts += 1
        self.last = ev
        if ev.verdict is Verdict.PASS:
            self.state = ListenerState.SERVING
            return "serve"
        if ev.verdict is Verdict.FAIL:
            return self._close("BIND_UNVERIFIED")
        timed_out = self.started_at is not None and (now - self.started_at) > self.max_verify_seconds
        retryable = ev.reasons == ("no_listener_for_process_yet",)
        if retryable and self.attempts < self.max_attempts and not timed_out:
            return "recheck_later"
        return self._close("BIND_UNVERIFIED")

    def confirm_closed(self, listeners_v4: Optional[Sequence[str]], listeners_v6: Optional[Sequence[str]]) -> str:
        """Called on later frames after 'close_now' (S5: the OS releases the socket asynchronously)."""
        if self.state is not ListenerState.CLOSING:
            return "noop"
        self.close_checks += 1
        if listeners_v4 is not None and listeners_v6 is not None and not listeners_v4 and not listeners_v6:
            self.state = ListenerState.CLOSED
            return "closed"
        if self.close_checks < self.max_close_checks:
            return "recheck_close"
        self.state, self.error_code = ListenerState.STUCK, "CLOSE_UNCONFIRMED"
        return "escalate"

    def on_config_change(self) -> str:
        """Port/localaddress/active changed or the listener restarted while open: re-verify before serving."""
        if self.state is ListenerState.SERVING:
            self.state, self.attempts = ListenerState.VERIFYING, 0
            return "reverify"
        return "noop"

    @property
    def serving(self) -> bool:
        return self.state is ListenerState.SERVING
