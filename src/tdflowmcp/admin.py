"""Setup admin pairing and sessions (security-model §2.5). Pure Python, no `td`.

Reverse pairing: the browser asks, TouchDesigner shows a non-secret match code, the user approves
inside TD (``Approvepairing``). Only then does the browser's HttpOnly pairing cookie turn into an
admin session (separate HttpOnly cookie + CSRF token bound to it). Secrets are random; only their
SHA-256 is kept. Mutations happen on the main thread; handler threads read immutable snapshots.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from typing import Dict, Optional, Tuple

PAIR_COOKIE, SESSION_COOKIE = "tdflow_pair", "tdflow_admin"
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"          # no 0/O/1/I/L


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cookie_value(headers, name: str) -> Optional[str]:
    vals = headers.get_all("Cookie") if hasattr(headers, "get_all") else (
        [headers["Cookie"]] if "Cookie" in headers else [])
    found = None
    for raw in vals or []:
        try:
            c = SimpleCookie()
            c.load(raw)
        except CookieError:
            return None
        if name in c:
            if found is not None and found != c[name].value:
                return None                                       # conflicting duplicates: refuse
            found = c[name].value
    return found


class AdminAuth:
    """Pairing state machine + admin sessions."""

    def __init__(self, clock=time.monotonic, pending_seconds: float = 120.0, session_seconds: float = 8 * 3600.0,
                 max_requests_per_minute: int = 3):
        self.clock = clock
        self.pending_seconds, self.session_seconds = pending_seconds, session_seconds
        self.max_per_minute = max_requests_per_minute
        self._recent = []
        self.pending: Optional[Dict] = None                  # {"hash", "code", "created", "state", "wall"}
        self._sessions: Dict[str, Tuple[str, float]] = {}     # sid_hash -> (csrf_hash, expires)   [immutable swap]
        self._ready: Dict[str, Tuple[str, str]] = {}          # pair_hash -> (sid, csrf) to hand out once
        self._ticket: Optional[Tuple[str, float]] = None      # (ticket_hash, expires): one launch ticket at a time
        self.ticket_seconds = 30.0
        self.replay_seconds = 15.0                            # a used ticket coming back this soon means a race
        self.warmup_seconds = 5.0                             # a ticket-born session is read-only this long
        self.max_ticket_failures = 5                          # refused attempts per minute before rate limiting
        self._redeemed: Optional[Tuple[str, float, float]] = None   # (hash, until, wall time of the redemption)
        self._born: Dict[str, float] = {}                     # sid_hash -> redemption time (warm-up)
        self._ticket_fails = []
        self.intercepted_at: Optional[float] = None           # wall time of the raced redemption (revert since then)
        self.last_browser_auth: Optional[str] = None          # wall time of the last ticket redemption
        self._mut = __import__("threading").Lock()

    # ---- main thread ---------------------------------------------------------------------------
    def request(self) -> Tuple[str, str]:
        now = self.clock()
        p = self.pending_view()
        if p and p["state"] == "pending":
            # Never replace a pending request (review 6: a local process could swap it while the user compares
            # codes). The user rejects it in TouchDesigner, or it expires after 2 minutes.
            raise LookupError("PAIRING_PENDING")
        self._recent = [t for t in self._recent if now - t < 60]
        if len(self._recent) >= self.max_per_minute:
            raise PermissionError("RATE_LIMITED")
        self._recent.append(now)
        secret = secrets.token_urlsafe(16)                    # 128-bit request secret, cookie only
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        code = code[:3] + "-" + code[3:]
        self.pending = {"hash": _h(secret), "code": code, "created": now, "state": "pending",
                        "wall": time.strftime("%H:%M:%S")}     # any earlier pending request is replaced
        return secret, code

    def pending_view(self) -> Optional[Dict]:
        p = self.pending
        if p and p["state"] == "pending" and self.clock() - p["created"] > self.pending_seconds:
            p["state"] = "expired"
        return dict(p) if p else None

    def approve(self) -> bool:
        p = self.pending_view()
        if not p or p["state"] != "pending":
            return False
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._mut:
            self._new_session(sid, csrf)
        self._ready[p["hash"]] = (sid, csrf)
        self.pending["state"] = "approved"
        return True

    def _new_session(self, sid: str, csrf: str) -> None:
        """Under ``_mut``. One admin session at a time: older ones end."""
        self._sessions = {_h(sid): (_h(csrf), self.clock() + self.session_seconds)}
        self._born = {}

    # ---- one-click browser authorization (owner UX assignment D21, review 18) ----------------------
    def issue_ticket(self) -> str:
        """Main thread, when TouchDesigner itself opens the Setup page or the dashboard (Open Setup / Open
        Dashboard). A random single-use ticket for the URL fragment: fragments are never sent to the server, so the
        ticket stays out of request logs and Referer headers; the page removes it from the address bar and history
        and redeems it with a POST. It expires after ``ticket_seconds``; issuing a new one voids the old one."""
        t = secrets.token_urlsafe(32)
        with self._mut:
            self._ticket = (_h(t), self.clock() + self.ticket_seconds)
        return t

    def redeem_ticket(self, ticket: str) -> Tuple[str, Optional[Tuple[str, str]]]:
        """Any thread. Returns (status, creds): "ok" with (sid, csrf) for exactly one redemption of a fresh ticket;
        "intercepted" when an already redeemed ticket comes back within ``replay_seconds`` (two parties had it: every
        admin session ends); "rate_limited" after too many failed attempts; "invalid" otherwise. Every attempt
        consumes the outstanding ticket, so it cannot be guessed."""
        now = self.clock()
        with self._mut:
            rec, self._ticket = self._ticket, None             # single use: consumed by any attempt
            if not isinstance(ticket, str) or not 20 <= len(ticket) <= 100:
                return self._ticket_failed(now)
            used = self._redeemed
            if used and used[1] > now and hmac.compare_digest(used[0], _h(ticket)):
                # The same ticket twice, seconds apart: another program read the URL (it is on the browser's command
                # line) and raced the page. End every admin session; the caller audits, suspends agents and reverts.
                self._sessions, self._redeemed, self._born = {}, None, {}
                self.intercepted_at = used[2]
                return "intercepted", None
            if rec is None or rec[1] <= now or not hmac.compare_digest(rec[0], _h(ticket)):
                return self._ticket_failed(now)
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self._new_session(sid, csrf)
            self._born = {_h(sid): now}                        # warm-up: read-only for ``warmup_seconds``
            self._redeemed = (rec[0], now + self.replay_seconds, time.time())
        self.last_browser_auth = time.strftime("%H:%M:%S")
        return "ok", (sid, csrf)

    def _ticket_failed(self, now: float) -> Tuple[str, None]:
        self._ticket_fails = [t for t in self._ticket_fails if now - t < 60]
        self._ticket_fails.append(now)
        return ("rate_limited" if len(self._ticket_fails) > self.max_ticket_failures else "invalid"), None

    def warming(self, principal: str) -> bool:
        """True while a ticket-born session is younger than ``warmup_seconds``: it may read, not change anything. The
        real page redeems within that time, so a session won in a race is ended before it can act (review 18)."""
        born = self._born.get(str(principal or "")[len("admin:"):])
        return born is not None and self.clock() - born < self.warmup_seconds

    def ticket_pending(self) -> bool:
        t = self._ticket
        return bool(t and t[1] > self.clock())

    def reject(self) -> bool:
        p = self.pending_view()
        if not p or p["state"] != "pending":
            return False
        self.pending["state"] = "rejected"
        return True

    def poll(self, pair_hash: str) -> Tuple[str, Optional[Tuple[str, str]]]:
        """State for the browser holding this pairing cookie. 'approved' hands out (sid, csrf) once."""
        p = self.pending_view()
        if not p or not hmac.compare_digest(p["hash"], pair_hash or ""):
            return "unknown", None
        if p["state"] == "approved":
            creds = self._ready.pop(p["hash"], None)
            self.pending = None
            return ("approved", creds) if creds else ("unknown", None)
        return p["state"], None

    def logout(self, sid_hash: str):
        """May run on a handler thread (admin API); guarded against a concurrent approval."""
        with self._mut:
            sessions = dict(self._sessions)
            sessions.pop(sid_hash, None)
            self._sessions = sessions

    def logout_all(self):
        with self._mut:
            self._sessions, self._ready, self.pending, self._ticket, self._born = {}, {}, None, None, {}

    def active_sessions(self) -> int:
        now = self.clock()
        return sum(1 for _, exp in self._sessions.values() if exp > now)

    # ---- any thread: authenticator hooks ---------------------------------------------------------
    def check(self, kind: str, headers) -> Optional[str]:
        if kind == "pair":
            v = cookie_value(headers, PAIR_COOKIE)
            return "pair:" + (_h(v) if v else "")
        if kind not in ("admin_session", "admin_session+csrf"):
            return None
        sid = cookie_value(headers, SESSION_COOKIE)
        if not sid:
            return None
        rec = self._sessions.get(_h(sid))
        if rec is None or rec[1] <= self.clock():
            return None
        if kind == "admin_session+csrf":
            vals = headers.get_all("X-TDflow-CSRF") if hasattr(headers, "get_all") else [headers.get("X-TDflow-CSRF")]
            if not vals or len(vals) != 1 or not vals[0] or not hmac.compare_digest(_h(vals[0]), rec[0]):
                return None
        return "admin:" + _h(sid)


def set_cookie(name: str, value: str, max_age: int, path: str = "/") -> Tuple[str, str]:
    return ("Set-Cookie", f"{name}={value}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Strict")


def clear_cookie(name: str, path: str = "/") -> Tuple[str, str]:
    return ("Set-Cookie", f"{name}=; Path={path}; Max-Age=0; HttpOnly; SameSite=Strict")


class Authenticator:
    """The transport's authenticator: agent bearer tokens (identity) and admin/pairing cookies."""

    def __init__(self, identity, admin: AdminAuth):
        self.identity, self.admin = identity, admin

    def __call__(self, kind: str, headers) -> Optional[str]:
        if kind == "agent_token":
            vals = headers.get_all("Authorization") if hasattr(headers, "get_all") else (
                [headers["Authorization"]] if "Authorization" in headers else [])
            if not vals or len(vals) != 1:
                return None
            scheme, _, cred = vals[0].strip().partition(" ")
            if scheme.lower() != "bearer":
                return None
            tid = self.identity.authenticate(cred.strip())
            return ("agent:" + tid) if tid else None
        return self.admin.check(kind, headers)
