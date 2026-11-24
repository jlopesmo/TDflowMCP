"""Request gate: per-route pre-checks before any TouchDesigner access (security-model §8.1, §8.2).

Pure function of (request, listener state, config). It never authenticates tokens or sessions itself;
it returns which later check is still required (``next``) and the single validated ``path`` that the
router must use. Responses never carry CORS headers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional

from tdflowmcp.net.bindverify import ListenerState

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Headers whose conflicting duplicates (e.g. 'Host' and 'host' with different values) are rejected.
SENSITIVE = ("host", "origin", "content-length", "transfer-encoding", "sec-fetch-site", "content-type",
             "x-tdflow-request", "x-tdflow-csrf", "upgrade")
_PATH_OK = re.compile(r"^/[A-Za-z0-9._~\-/]*$")
_DIGITS = re.compile(r"[0-9]{1,10}")   # ASCII only; str.isdigit() accepts e.g. '²' (review 4, M)


@dataclass(frozen=True)
class GateConfig:
    port: int = 9480
    max_body_bytes: int = 1024 * 1024
    setup_reads_need_admin: bool = False   # True would require the admin session for every Setup read
    # Setup reads that always need the paired admin session (Phase 1B): agent/session information.
    admin_read_prefixes: tuple = ("/setup/api/agents", "/setup/api/sessions", "/setup/api/skills",
                                  "/setup/api/memory", "/api")      # every dashboard read (Phase 2B)

    @property
    def canonical_origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def canonical_host(self) -> str:
        return f"127.0.0.1:{self.port}"


@dataclass(frozen=True)
class Decision:
    allow: bool
    status: int
    code: str
    route: str = ""
    next: str = ""            # '', 'agent_token', 'admin_session', 'admin_session+csrf'
    location: str = ""        # for redirects
    path: str = ""            # the validated path the router must use (never re-normalise)


class _DuplicateHeader(Exception):
    pass


def _h(headers: Mapping[str, str], name: str) -> Optional[str]:
    name = name.lower()
    vals = {str(v) for k, v in headers.items() if str(k).lower() == name}
    if len(vals) > 1:
        raise _DuplicateHeader(name)
    return next(iter(vals)) if vals else None


def validate_path(raw: str) -> Optional[str]:
    """Return the path (query stripped) if it is canonical and safe, else None.

    Rejects '..' / '.' segments, '//', backslashes, percent-encoding, non-ASCII and control characters,
    so classification and routing always see the same path (review 3, H2).
    """
    path = (raw or "").split("?", 1)[0]
    if not _PATH_OK.match(path) or "//" in path:
        return None
    if any(seg in (".", "..") for seg in path.split("/")):
        return None
    return path


def classify(path: str) -> str:
    if path == "/mcp":
        return "mcp"
    if path == "/.well-known/tdflowmcp":
        return "well_known"
    if path == "/api/events":
        return "events"
    if path in ("/setup/api/pair", "/setup/api/pair/status", "/setup/api/pair/ticket"):
        return "pair"
    if path.startswith("/setup/api/") or path.startswith("/api/"):
        return "api"
    if path == "/setup" or path.startswith("/setup/") or path == "/ui" or path.startswith("/ui/"):
        return "static"
    return "unknown"


def decide(method: str, raw_path: str, headers: Mapping[str, str], state: ListenerState,
           cfg: GateConfig = GateConfig(), body_len: Optional[int] = None) -> Decision:
    method = (method or "").upper()

    # §8.2: nothing but 503 until the effective loopback binding is verified.
    if state is not ListenerState.SERVING:
        return Decision(False, 503, "TDFLOW_VERIFYING")

    try:
        for name in SENSITIVE:
            _h(headers, name)
    except _DuplicateHeader as e:
        return Decision(False, 400, f"DUPLICATE_HEADER:{e}")

    path = validate_path(raw_path)
    if path is None:
        return Decision(False, 400, "BAD_PATH")
    route = classify(path)

    host = _h(headers, "Host")
    localhost_host = f"localhost:{cfg.port}"
    if host not in (cfg.canonical_host, localhost_host):
        return Decision(False, 403, "BAD_HOST", route, path=path)

    if method == "OPTIONS":                       # preflights are never granted
        return Decision(False, 403, "PREFLIGHT_DENIED", route, path=path)

    if _h(headers, "Transfer-Encoding") is not None and body_len is None:
        return Decision(False, 400, "TRANSFER_ENCODING_UNSUPPORTED", route, path=path)
    length = _h(headers, "Content-Length")
    if length is not None:
        if not _DIGITS.fullmatch(length):
            return Decision(False, 400, "BAD_CONTENT_LENGTH", route, path=path)
        if int(length) > cfg.max_body_bytes:
            return Decision(False, 413, "BODY_TOO_LARGE", route, path=path)
    if body_len is not None:
        if body_len > cfg.max_body_bytes:
            return Decision(False, 413, "BODY_TOO_LARGE", route, path=path)
        if length is not None and int(length) != body_len:
            return Decision(False, 400, "CONTENT_LENGTH_MISMATCH", route, path=path)

    origin = _h(headers, "Origin")
    fetch_site = _h(headers, "Sec-Fetch-Site")

    def d(allow, status, code, nxt="", location=""):
        return Decision(allow, status, code, route, nxt, location, path)

    if route == "unknown":
        return d(False, 404, "NOT_FOUND")

    if route == "mcp":
        if method != "POST":
            return d(False, 405, "METHOD_NOT_ALLOWED")
        if origin is not None and origin != cfg.canonical_origin:
            return d(False, 403, "BAD_ORIGIN")
        ctype = (_h(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return d(False, 415, "UNSUPPORTED_MEDIA_TYPE")
        return d(True, 200, "OK", "agent_token")

    # Browser-facing routes: canonical host only (redirect localhost for page loads).
    if host == localhost_host:
        if route == "static" and method == "GET":
            return d(False, 308, "REDIRECT_CANONICAL", location=cfg.canonical_origin + path)
        return d(False, 403, "NON_CANONICAL_HOST")

    if route == "well_known":
        return d(True, 200, "OK") if method == "GET" else d(False, 405, "METHOD_NOT_ALLOWED")

    if route == "static":
        if method != "GET":
            return d(False, 405, "METHOD_NOT_ALLOWED")
        if fetch_site is not None and fetch_site not in ("same-origin", "none"):
            return d(False, 403, "CROSS_SITE_FETCH")
        return d(True, 200, "OK")

    if route == "events":
        if method != "GET" or (_h(headers, "Upgrade") or "").lower() != "websocket":
            return d(False, 400, "WEBSOCKET_REQUIRED")
        if origin != cfg.canonical_origin:
            return d(False, 403, "BAD_ORIGIN")
        return Decision(True, 101, "OK", route, "admin_session+csrf", "", path)

    if route == "pair":
        # Pairing needs no session yet (it creates one), but it is a browser route: custom header,
        # same-origin fetch, and for the POST an exact Origin. Rate limiting happens on the main thread.
        if _h(headers, "X-TDflow-Request") != "1":
            return d(False, 403, "MISSING_REQUEST_HEADER")
        if fetch_site is not None and fetch_site != "same-origin":
            return d(False, 403, "CROSS_SITE_FETCH")
        if method == "POST" and path == "/setup/api/pair":
            if origin != cfg.canonical_origin:
                return d(False, 403, "BAD_ORIGIN")
            return d(True, 200, "OK", "pair")
        if method == "POST" and path == "/setup/api/pair/ticket":
            # One-click authorization: the ticket itself (in the body) is the credential; exact Origin required.
            if origin != cfg.canonical_origin:
                return d(False, 403, "BAD_ORIGIN")
            return d(True, 200, "OK", "")
        if method == "GET" and path == "/setup/api/pair/status":
            if origin is not None and origin != cfg.canonical_origin:
                return d(False, 403, "BAD_ORIGIN")
            return d(True, 200, "OK", "pair")
        return d(False, 405, "METHOD_NOT_ALLOWED")

    # route == "api"
    if method == "GET":
        if _h(headers, "X-TDflow-Request") != "1":
            return d(False, 403, "MISSING_REQUEST_HEADER")
        if fetch_site is not None and fetch_site != "same-origin":
            return d(False, 403, "CROSS_SITE_FETCH")
        if origin is not None and origin != cfg.canonical_origin:
            return d(False, 403, "BAD_ORIGIN")
        admin_read = any(path == p or path.startswith(p + "/") for p in cfg.admin_read_prefixes)
        if admin_read:
            # Cookies are not port-isolated, so admin reads also need the in-page CSRF token (review 6).
            return d(True, 200, "OK", "admin_session+csrf")
        needs_admin = path.startswith("/api/") or cfg.setup_reads_need_admin
        return d(True, 200, "OK", "admin_session" if needs_admin else "")
    if method in MUTATING:
        if origin != cfg.canonical_origin:              # a missing Origin is rejected
            return d(False, 403, "BAD_ORIGIN")
        if not _h(headers, "X-TDflow-CSRF"):
            return d(False, 403, "MISSING_CSRF")
        return d(True, 200, "OK", "admin_session+csrf")
    return d(False, 405, "METHOD_NOT_ALLOWED")
