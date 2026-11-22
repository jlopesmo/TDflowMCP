"""Embedded loopback HTTP transport: stdlib only, TouchDesigner-independent (ADR-0002, rev. 2026-11-22).

Threading contract (invariant P-10):
- Handler threads parse HTTP, run the pure request gate (``tdflowmcp.gate.decide``), authenticate,
  serve an immutable static snapshot, and enqueue everything else on a bounded queue. They never
  call ``dispatch`` and never touch TouchDesigner.
- The owner thread (TouchDesigner's main thread: whoever constructed the transport) calls
  ``pump(dispatch)`` once per frame. ``dispatch`` runs only there, so every TD operator access stays
  on the main thread. ``pump``, ``start``, ``begin_close``, ``set_static`` and the ``suspended``
  setter raise ``RuntimeError`` on any other thread.

Network contract:
- Binds only to the literal ``127.0.0.1`` (no wildcard, no host names). On Windows the socket uses
  ``SO_EXCLUSIVEADDRUSE`` and never ``SO_REUSEADDR``, so no other socket can co-bind the port.
- Serving still requires the bind-verification gate (security-model §8.2): until the owner's
  ``state_fn`` reports ``SERVING`` every request gets ``503 TDFLOW_VERIFYING`` from the gate.
- Responses never carry CORS headers. Every connection is closed after one response.
"""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import queue
import re
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from tdflowmcp.gate import Decision, GateConfig, decide
from tdflowmcp.net.bindverify import ListenerState

KIND = "python-http"
BIND_HOSTS = frozenset({"127.0.0.1"})
SECURITY_HEADERS: Tuple[Tuple[str, str], ...] = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Content-Security-Policy",
     "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
)
# Headers a dispatcher may never set: CORS (invariant), and framing headers owned by the transport.
_FORBIDDEN_RESPONSE = ("access-control-", "content-length", "transfer-encoding", "connection", "server",
                       "content-type", "date", "x-tdflow-request-id") + tuple(k.lower() for k, _ in SECURITY_HEADERS)
_TOKEN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
# Request headers handed to the main-thread dispatcher (never Authorization or Cookie).
FORWARDED_HEADERS = ("content-type", "accept", "user-agent", "mcp-protocol-version", "mcp-method", "mcp-name",
                     "x-tdflow-request")
_EADDRINUSE = {98, 48, 10048, 10013}  # Linux, macOS, WSAEADDRINUSE, WSAEACCES (exclusive owner)
_BUSY_BODY = b'{"error":"TDFLOW_BUSY"}\n'
_BUSY_RAW = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: "
             + str(len(_BUSY_BODY)).encode() + b"\r\nRetry-After: 1\r\nConnection: close\r\n\r\n" + _BUSY_BODY)
_LINGER_MAX, _LINGER_SECONDS = 64, 0.5
# Gate outcomes that still take precedence over MCP_DISABLED (review 5): not serving, bad Host, bad path.
_MCP_PRECEDENCE = frozenset({"TDFLOW_VERIFYING", "BAD_HOST", "BAD_PATH"})


class TransportError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    headers: Tuple[Tuple[str, str], ...] = ()

    @classmethod
    def json(cls, status: int, obj, headers: Iterable[Tuple[str, str]] = ()) -> "Response":
        return cls(status, (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"),
                   "application/json", tuple(headers))


def error(status: int, code: str, headers: Iterable[Tuple[str, str]] = ()) -> Response:
    return Response.json(status, {"error": code}, headers)


def _code_of(resp: Response) -> str:
    if resp.status < 400:
        return "OK"
    try:
        obj = json.loads(resp.body)
        return str(obj["error"])[:64] if isinstance(obj, dict) and "error" in obj else str(resp.status)
    except (ValueError, TypeError):
        return str(resp.status)


def _safe_value(v: str) -> bool:
    if not isinstance(v, str) or "\r" in v or "\n" in v or "\0" in v:
        return False
    try:
        v.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def _clean_headers(pairs: Iterable[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], int]:
    out, dropped = [], 0
    for k, v in pairs:
        k, v = str(k), str(v)
        low = k.lower()
        if (not _TOKEN.match(k) or not _safe_value(v)
                or any(low.startswith(f) for f in _FORBIDDEN_RESPONSE)):
            dropped += 1
            continue
        out.append((k, v))
    return out, dropped


# Job lifecycle: PENDING -> CLAIMED (by pump) -> DONE, or PENDING -> CANCELLED (timeout/close; never runs).
PENDING, CLAIMED, CANCELLED, DONE = "pending", "claimed", "cancelled", "done"


class Job:
    """One queued request. Fields are read-only for the dispatcher; it returns a ``Response``."""
    __slots__ = ("rid", "method", "path", "route", "principal", "headers", "body", "created",
                 "lock", "state", "done", "response")

    def __init__(self, rid, method, path, route, principal, headers, body):
        self.rid, self.method, self.path, self.route = rid, method, path, route
        self.principal, self.headers, self.body = principal, headers, body
        self.created = time.monotonic()
        self.lock = threading.Lock()
        self.state = PENDING
        self.done = threading.Event()
        self.response: Optional[Response] = None


Authenticator = Callable[[str, object], Optional[str]]  # (decision.next, headers) -> principal | None


def deny_all(kind: str, headers) -> Optional[str]:
    """Default authenticator: every authenticated route is refused (deny by default)."""
    return None


class TokenAuthenticator:
    """Bearer-token check for ``agent_token`` routes. Stores SHA-256 digests only; constant-time compare.
    Admin sessions are not handled here (Phase 1B), so admin routes stay denied."""

    def __init__(self):
        self._digests: List[Tuple[bytes, str]] = []

    def add(self, token: str, principal: str) -> None:
        if not token or len(token) < 32:
            raise ValueError("token too short")
        self._digests.append((hashlib.sha256(token.encode("utf-8")).digest(), principal))

    def __call__(self, kind: str, headers) -> Optional[str]:
        if kind != "agent_token":
            return None
        values = headers.get_all("Authorization") if hasattr(headers, "get_all") else (
            [headers["Authorization"]] if "Authorization" in headers else [])
        if not values or len(values) != 1:
            return None
        scheme, _, cred = values[0].strip().partition(" ")
        if scheme.lower() != "bearer" or not cred.strip():   # scheme is case-insensitive (RFC 7235)
            return None
        got = hashlib.sha256(cred.strip().encode("utf-8")).digest()
        found = None
        for digest, principal in self._digests:       # no early exit
            if hmac.compare_digest(digest, got):
                found = principal
        return found


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    allow_reuse_port = False
    request_queue_size = 64          # S6: a 48-way burst overflowed a backlog of 16 (refused connects)

    def __init__(self, addr, owner: "EmbeddedHTTPTransport"):
        self.owner = owner
        super().__init__(addr, _Handler)

    def server_bind(self):
        # Never SO_REUSEADDR (on Windows it lets another socket co-bind the port); exclusive instead.
        if sys.platform == "win32":
            if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):    # review 5: never bind non-exclusively
                raise OSError(0, "SO_EXCLUSIVEADDRUSE unavailable")
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]  # no getfqdn/DNS

    def verify_request(self, request, client_address):
        return isinstance(client_address, tuple) and str(client_address[0]).startswith("127.")

    def process_request(self, request, client_address):
        o = self.owner
        if o._closing or not o._slots.acquire(blocking=False):
            o._bump("conn_rejected")
            o._reject(request)          # 503 without a thread; closed later by service_actions
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            o._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        o = self.owner
        o._track(request, True)
        try:
            super().process_request_thread(request, client_address)
        finally:
            o._track(request, False)
            o._slots.release()

    def service_actions(self):
        self.owner._reap()
        self.owner._drain_rejected()
        self.owner._watchdog()

    def handle_error(self, request, client_address):
        self.owner._bump("handler_errors")          # no traceback to the TD console


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TDflowMCP"
    sys_version = ""

    def setup(self):
        self.timeout = self.server.owner.io_timeout
        super().setup()

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def send_error(self, code, message=None, explain=None):
        # Called by the stdlib parser for malformed requests (400, 414, 431, 501, 505).
        # Outside SERVING the answer is the gate's 503, never a parser detail (review 4, L).
        self.close_connection = True
        o = self.server.owner
        o._bump("malformed")
        if o.effective_state() is not ListenerState.SERVING:
            code, label = 503, "TDFLOW_VERIFYING"
        else:
            label = f"HTTP_{code}"
        self._reply(error(code, label), o._next_id(), "", None, label, time.perf_counter())

    def handle_expect_100(self):
        # Never emit '100 Continue' before the gate has run (review 4, L); clients then just send the body.
        return True

    def do_GET(self):
        o = self.server.owner
        t0 = time.perf_counter()
        rid = o._next_id()
        try:
            self._handle(rid, t0)
        except Exception as e:  # any unexpected error still yields a response and an audit record
            o._bump("handler_exceptions")
            o.last_handler_error = type(e).__name__
            self.close_connection = True
            try:
                self._reply(error(500, "INTERNAL_ERROR"), rid, "", None, "INTERNAL_ERROR", t0)
            except Exception:
                pass

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_GET

    def _read_body(self, n: int) -> Optional[bytes]:
        try:
            data = self.rfile.read(n) if n else b""
        except (OSError, ValueError):
            return None
        return data if len(data) == n else None

    def _handle(self, rid: int, t0: float):
        o = self.server.owner
        self.close_connection = True
        dec: Decision = decide(self.command, self.path, self.headers, o.effective_state(), o.cfg)
        principal = None
        if dec.route == "mcp" and not o._mcp_enabled and dec.code not in _MCP_PRECEDENCE \
                and not dec.code.startswith("DUPLICATE_HEADER"):
            # Phase 1A: every /mcp request (any method, origin or body) gets MCP_DISABLED once Host,
            # path and VERIFYING have been checked. Nothing is authenticated, read or dispatched.
            return self._reply(error(503, "MCP_DISABLED"), rid, dec.route, dec.path, "MCP_DISABLED", t0)
        if not dec.allow:
            extra = [("Location", dec.location)] if dec.location else []
            if dec.status == 503:
                extra.append(("Retry-After", "1"))
            return self._reply(error(dec.status, dec.code, extra), rid, dec.route, dec.path, dec.code, t0)
        # Authenticate from headers BEFORE reading any body (review 4, L).
        if dec.next:
            principal = o.authenticator(dec.next, self.headers)
            if principal is None:
                o._bump("auth_fail")
                return self._reply(error(401, "UNAUTHENTICATED"), rid, dec.route, dec.path, "UNAUTHENTICATED", t0)
        if dec.route == "mcp" and o._suspended:
            return self._reply(error(503, "SUSPENDED"), rid, dec.route, dec.path, "SUSPENDED", t0, principal)
        length = self.headers.get("Content-Length")     # the gate guarantees ASCII digits, <= cap
        body = self._read_body(int(length)) if length is not None else b""
        if body is None:
            return self._reply(error(400, "INCOMPLETE_BODY"), rid, dec.route, dec.path, "INCOMPLETE_BODY", t0)
        if dec.route in ("static", "well_known"):     # immutable snapshot; never needs the main thread
            asset = o._static.get(dec.path)
            resp = Response(200, asset[1], asset[0]) if asset else error(404, "NOT_FOUND")
            return self._reply(resp, rid, dec.route, dec.path, "OK" if asset else "NOT_FOUND", t0)
        fwd = {h: self.headers.get(h) for h in FORWARDED_HEADERS if self.headers.get(h) is not None}
        job = Job(rid, self.command, dec.path, dec.route, principal, fwd, body)
        resp = o._submit(job)
        self._reply(resp, rid, dec.route, dec.path, _code_of(resp), t0, principal)

    def _reply(self, resp: Response, rid: int, route: str, path: Optional[str], code: str, t0: float,
               principal: Optional[str] = None):
        o = self.server.owner
        headers, dropped = _clean_headers(resp.headers)
        if dropped:
            o._bump("response_headers_dropped", dropped)
        if not _safe_value(resp.content_type):        # review 4, M: content type is a header value too
            o._bump("response_headers_dropped")
            resp = error(500, "INTERNAL_ERROR")
            code = "BAD_CONTENT_TYPE"
        if self.request_version == "HTTP/0.9":     # always answer with a real status line
            self.request_version = "HTTP/1.1"
        try:
            self.send_response_only(resp.status)
            self.send_header("Date", self.date_time_string())
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            self.send_header("Connection", "close")
            self.send_header("X-TDflow-Request-Id", str(rid))
            for k, v in SECURITY_HEADERS:
                self.send_header(k, v)
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and resp.body:
                self.wfile.write(resp.body)
            self.wfile.flush()
        except OSError:
            o._bump("client_gone")
        except Exception as e:                       # never lose the audit record
            o._bump("reply_errors")
            o.last_handler_error = type(e).__name__
        method = self.command if self.command in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD") \
            else "OTHER"
        o._audit({"rid": rid, "t": round(time.time(), 3), "method": method, "path": path, "route": route,
                  "status": resp.status, "code": code, "ms": round((time.perf_counter() - t0) * 1000, 2),
                  "principal": principal})


class EmbeddedHTTPTransport:
    """Loopback-only HTTP transport whose application work runs on the owner (main) thread via ``pump``.

    Interchangeable with a future Web Server DAT transport: both expose start / begin_close /
    is_closed / pump / set_static / suspended / stats / drain_audit, and both put every request
    through the same gate and authenticator before any dispatch.

    ``state_fn`` and ``authenticator`` run on handler threads: they must be pure Python and must never
    read TouchDesigner (mirror TD parameters such as ``Suspendagents`` into plain attributes on the
    main thread each frame).
    """
    kind = KIND

    def __init__(self, host: str = "127.0.0.1", port: int = 9480, *,
                 state_fn: Callable[[], ListenerState],
                 cfg: Optional[GateConfig] = None,
                 authenticator: Authenticator = deny_all,
                 queue_max: int = 32, max_connections: int = 16,
                 io_timeout: float = 5.0, max_connection_seconds: float = 30.0,
                 job_timeout: float = 5.0, dispatch_grace: float = 5.0, audit_max: int = 2048,
                 unauth_queue_max: int = 4, mcp_enabled: bool = False,
                 heartbeat_timeout: Optional[float] = None):
        if host not in BIND_HOSTS:
            raise TransportError("BIND_REFUSED", "only the literal 127.0.0.1 may be bound")
        if not (0 <= int(port) <= 65535):
            raise TransportError("BAD_PORT")
        self.host, self.port = host, int(port)
        self.cfg = cfg or GateConfig(port=self.port)
        self._state_fn = state_fn
        self.authenticator = authenticator
        self.io_timeout = io_timeout
        self.max_connection_seconds = max_connection_seconds
        self.job_timeout, self.dispatch_grace = job_timeout, dispatch_grace
        self._owner = threading.get_ident()
        self._q: "queue.Queue[Job]" = queue.Queue(maxsize=queue_max)
        self._slots = threading.BoundedSemaphore(max_connections)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._conns: Dict[socket.socket, float] = {}
        self._rejected: deque = deque()          # (socket, deadline): serve thread only
        self._stats: Dict[str, int] = {}
        self._audit_ring: deque = deque(maxlen=audit_max)
        self._static: Mapping[str, Tuple[str, bytes]] = {}
        self._suspended = False
        self._closing = False
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self._closer: Optional[threading.Thread] = None
        self.last_dispatch_error = ""
        self.last_handler_error = ""
        self.unauth_queue_max = unauth_queue_max
        self._unauth_pending = 0
        self._mcp_enabled = bool(mcp_enabled)    # Phase 1A components pass False: /mcp -> 503 MCP_DISABLED
        # Orphan watchdog: if the owner stops pumping for this long (component deleted, extension lost),
        # the serve thread closes the listener by itself. None disables it (unit tests).
        self.heartbeat_timeout = heartbeat_timeout
        self._last_pump = time.monotonic()
        self.last_activity = time.monotonic()    # last request of any kind (idle timeout input)
        self.close_reason = ""

    # ---- owner-thread API -------------------------------------------------------------------
    def _own(self, what: str):
        if threading.get_ident() != self._owner:
            raise RuntimeError(f"{what} must run on the owner (main) thread")

    def start(self) -> Tuple[str, int]:
        self._own("start")
        if self._server is not None:
            raise TransportError("ALREADY_STARTED")
        try:
            srv = _Server((self.host, self.port), self)
        except OSError as e:
            code = "PORT_IN_USE" if (getattr(e, "winerror", None) in _EADDRINUSE or e.errno in _EADDRINUSE) \
                else "BIND_FAILED"
            raise TransportError(code, f"errno={e.errno} winerror={getattr(e, 'winerror', None)}") from None
        self._server = srv
        bound = srv.socket.getsockname()[:2]
        if self.port == 0:                      # tests only: ephemeral port
            self.port = bound[1]
            self.cfg = GateConfig(port=bound[1], max_body_bytes=self.cfg.max_body_bytes,
                                  setup_reads_need_admin=self.cfg.setup_reads_need_admin)
        self._thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="tdflowmcp-http", daemon=True)
        self._thread.start()
        return bound

    def sockname(self) -> Optional[Tuple[str, int]]:
        """Direct evidence for the bind gate: the address the listening socket is bound to."""
        try:
            return self._server.socket.getsockname()[:2] if self._server else None
        except OSError:
            return None

    def pump(self, dispatch: Callable[[Job], Response], max_jobs: int = 8, budget_s: float = 0.004) -> int:
        """Run up to ``max_jobs`` queued requests on this (main) thread within ``budget_s``."""
        self._own("pump")
        self._last_pump = time.monotonic()
        n, end = 0, time.perf_counter() + budget_s
        while n < max_jobs:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                break
            self._release_unauth(job)                 # every dequeued job frees its quota slot
            with job.lock:
                if job.state != PENDING:
                    continue
                job.state = CLAIMED
            # Re-check at dispatch time: a job queued while SERVING must not run after the state left
            # SERVING, the transport began closing, or the kill switch was set (review 4, H1).
            state = self.effective_state()
            if state is not ListenerState.SERVING:
                resp = error(503, "TDFLOW_CLOSING" if self._closing else "TDFLOW_VERIFYING")
                self._bump("dropped_not_serving")
            elif job.route == "mcp" and self._suspended:
                resp = error(503, "SUSPENDED")
                self._bump("dropped_suspended")
            else:
                try:
                    resp = dispatch(job)
                    if not isinstance(resp, Response):
                        raise TypeError("dispatch must return Response")
                except Exception as e:  # recovery: one failing request never stops the transport
                    self._bump("dispatch_errors")
                    self.last_dispatch_error = type(e).__name__
                    resp = error(500, "INTERNAL_ERROR")
            job.response = resp
            job.state = DONE
            job.done.set()
            self._bump("dispatched")
            n += 1
            if time.perf_counter() >= end:
                break
        return n

    def set_static(self, assets: Mapping[str, Tuple[str, bytes]]) -> None:
        """Replace the static snapshot (built on the main thread from DATs). Values must be bytes."""
        self._own("set_static")
        snap = {}
        for path, (ctype, data) in assets.items():
            if not isinstance(data, bytes):
                raise TypeError("static assets must be bytes")
            snap[str(path)] = (str(ctype), data)
        self._static = snap                      # atomic reference swap

    @property
    def mcp_enabled(self) -> bool:
        return self._mcp_enabled

    @mcp_enabled.setter
    def mcp_enabled(self, value: bool) -> None:
        self._own("mcp_enabled")
        self._mcp_enabled = bool(value)

    @property
    def suspended(self) -> bool:
        return self._suspended

    @suspended.setter
    def suspended(self, value: bool) -> None:
        self._own("suspended")
        self._suspended = bool(value)

    def begin_close(self) -> None:
        """Kill switch for the transport itself: stop accepting, fail queued jobs, close the socket
        off-thread (never blocks the frame). Idempotent. Confirm with ``is_closed`` and the OS table."""
        self._own("begin_close")
        self._close_any_thread("owner")

    def _close_any_thread(self, reason: str) -> None:
        with self._lock:                         # pairs with _submit: no job can slip in after the drain
            if self._closing:
                return
            self._closing = True
            self.close_reason = reason
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                break
            with job.lock:
                if job.state == PENDING:
                    job.state = CANCELLED
                    job.response = error(503, "TDFLOW_CLOSING")
                    job.done.set()
        srv = self._server
        if srv is None:
            return

        def _close():
            srv.shutdown()
            srv.server_close()
            self._drain_rejected()                 # serve thread has exited: safe to touch here
            with self._lock:
                conns = list(self._conns)
            for c in conns:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self._closer = threading.Thread(target=_close, name="tdflowmcp-http-close", daemon=True)
        self._closer.start()

    def is_closed(self) -> bool:
        if self._server is None:
            return True
        return (self._closing and self._closer is not None and not self._closer.is_alive()
                and not (self._thread and self._thread.is_alive()))

    def healthy(self) -> bool:
        return bool(self._server and not self._closing and self._thread and self._thread.is_alive())

    def effective_state(self) -> ListenerState:
        return ListenerState.CLOSING if self._closing else self._state_fn()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            s = dict(self._stats)
            s["active_connections"] = len(self._conns)
        s["queued"] = self._q.qsize()
        return s

    def drain_audit(self, max_items: int = 10_000) -> List[dict]:
        out = []
        while self._audit_ring and len(out) < max_items:
            out.append(self._audit_ring.popleft())
        return out

    # ---- handler-thread helpers (no TD access) ----------------------------------------------
    def _next_id(self) -> int:
        return next(self._ids)

    def _bump(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._stats[name] = self._stats.get(name, 0) + n

    def _audit(self, rec: dict) -> None:
        if rec.get("status", 500) < 400:          # only served requests count as activity (idle stop)
            self.last_activity = time.monotonic()
        if len(self._audit_ring) == self._audit_ring.maxlen:
            self._bump("audit_overflow")
        self._audit_ring.append(rec)

    def _track(self, sock, add: bool) -> None:
        with self._lock:
            if add:
                self._conns[sock] = time.monotonic()
                self._stats["max_active"] = max(self._stats.get("max_active", 0), len(self._conns))
            else:
                self._conns.pop(sock, None)

    def _reject(self, sock) -> None:
        """Serve thread: send 503 and keep the socket briefly so unread request bytes do not turn the
        close into a reset that would hide the 503. Bounded; overflow is closed at once."""
        try:
            sock.setblocking(False)
            sock.send(_BUSY_RAW)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        if len(self._rejected) >= _LINGER_MAX:
            sock.close()
            return
        self._rejected.append((sock, time.monotonic() + _LINGER_SECONDS))

    def _drain_rejected(self) -> None:
        now = time.monotonic()
        keep: deque = deque()
        while self._rejected:
            sock, deadline = self._rejected.popleft()
            done = now >= deadline or self._closing
            if not done:
                try:
                    while sock.recv(65536):
                        pass
                    done = True                     # peer finished sending and closed
                except BlockingIOError:
                    pass
                except OSError:
                    done = True
            if done:
                sock.close()
            else:
                keep.append((sock, deadline))
        self._rejected = keep

    def _watchdog(self) -> None:
        """Serve thread: close an orphaned listener (nobody pumped for ``heartbeat_timeout`` seconds)."""
        if self.heartbeat_timeout and not self._closing and \
                time.monotonic() - self._last_pump > self.heartbeat_timeout:
            self._bump("orphan_closed")
            self._close_any_thread("heartbeat_lost")   # starts the closer thread; never blocks here

    def _reap(self) -> None:
        """Serve-thread housekeeping: cut connections older than ``max_connection_seconds`` (slow clients)."""
        now = time.monotonic()
        with self._lock:
            old = [s for s, t in self._conns.items() if now - t > self.max_connection_seconds]
        for s in old:
            try:
                s.shutdown(socket.SHUT_RDWR)
                self._bump("reaped")
            except OSError:
                pass

    @staticmethod
    def _unauth(job: Job) -> bool:
        return job.principal is None or str(job.principal).startswith("pair:")

    def _release_unauth(self, job: Job) -> None:
        if self._unauth(job):
            with self._lock:
                self._unauth_pending = max(0, self._unauth_pending - 1)

    def _submit(self, job: Job) -> Response:
        busy = error(503, "TDFLOW_BUSY", [("Retry-After", "1")])
        with self._lock:
            if self._closing:
                return error(503, "TDFLOW_CLOSING")
            if self._unauth(job):
                # Unauthenticated jobs get a small separate quota so they cannot starve /mcp (review 4, M).
                if self._unauth_pending >= self.unauth_queue_max:
                    self._stats["busy_unauth"] = self._stats.get("busy_unauth", 0) + 1
                    return busy
                self._unauth_pending += 1
            try:
                self._q.put_nowait(job)              # under the lock: begin_close cannot miss it
            except queue.Full:
                if self._unauth(job):
                    self._unauth_pending -= 1
                self._stats["busy"] = self._stats.get("busy", 0) + 1
                return busy
        if job.done.wait(self.job_timeout):
            return job.response
        with job.lock:
            if job.state == PENDING:
                job.state = CANCELLED          # never dispatched
                self._bump("timeouts")
                return error(504, "TDFLOW_TIMEOUT")
        if job.done.wait(self.dispatch_grace):
            return job.response
        self._bump("timeouts_after_dispatch")  # outcome unknown to the client; see audit by request id
        return error(504, "TDFLOW_TIMEOUT_AFTER_DISPATCH")
