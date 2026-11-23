"""Agent identity, hashed tokens and session grouping (security-model §2). Pure Python, no `td`.

Tokens: ``tdf_<token_id>.<secret>``; ``token_id`` is 12 base62 characters, ``secret`` ≥ 256 bits from
``secrets.token_urlsafe``. Only ``sha256(salt || secret)`` is stored. The secret is returned once.

Threading: ``Identity`` mutates only on the main thread (store calls). Handler threads use
``authenticate`` / ``admin`` hooks that read an immutable snapshot dict swapped atomically, so they
never touch SQLite and never block on the main thread.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from tdflowmcp.policy import CAP_MEMORY_REPO_WRITE, CAPABILITIES, ROLES, Agent, Grant

TOKEN_RE = re.compile(r"^tdf_([A-Za-z0-9]{12})\.([A-Za-z0-9_\-]{43,128})$")
_B62 = string.ascii_letters + string.digits


def _rand_id(n: int = 12) -> str:
    return "".join(secrets.choice(_B62) for _ in range(n))


def _digest(salt: str, secret: str) -> str:
    return hashlib.sha256(bytes.fromhex(salt) + secret.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TokenRecord:
    token_id: str
    agent_id: str
    salt: str
    digest: str
    created_at: str
    revoked_at: Optional[str]


class Identity:
    def __init__(self, store, preload=None):
        """``preload``: rows from ``store.load_identity()`` already fetched by the audit worker, so the TD
        main thread can build the identity snapshot without waiting for disk I/O."""
        self.store = store
        self.agents: Dict[str, Agent] = {}
        self.tokens: Dict[str, TokenRecord] = {}
        self.created: Dict[str, str] = {}
        self._snap: Dict[str, Tuple[str, str, str]] = {}     # token_id -> (salt, digest, agent_id)  [immutable]
        self._apply(preload) if preload is not None else self.reload()

    def reload(self):
        self._apply(self.store.load_identity())

    def _apply(self, rows):
        agents, tokens, grants = rows[0], rows[1], rows[2]
        caps = rows[3] if len(rows) > 3 else ()
        g: Dict[str, List[Grant]] = {}
        for agent_id, scope, actions, effect in grants:
            g.setdefault(agent_id, []).append(Grant(scope=scope, actions=frozenset(actions.split(",")), effect=effect))
        cp: Dict[str, set] = {}
        for agent_id, name in caps:
            if name in CAPABILITIES:                 # an unknown name (newer build) never grants anything here
                cp.setdefault(agent_id, set()).add(name)
        self.agents = {a[0]: Agent(id=a[0], display_name=a[1], role=a[2], enabled=bool(a[3]),
                                   grants=tuple(g.get(a[0], ())), capabilities=frozenset(cp.get(a[0], ())))
                       for a in agents}
        created = {a[0]: a[4] for a in agents}
        toks = {t[0]: TokenRecord(*t) for t in tokens}
        self.created, self.tokens = created, toks
        self._snap = {t.token_id: (t.salt, t.digest, t.agent_id) for t in self.tokens.values()
                      if t.revoked_at is None and self.agents.get(t.agent_id) and self.agents[t.agent_id].enabled}

    # ---- main thread: administration -----------------------------------------------------------
    def create_agent(self, display_name: str, role: str, scopes: Sequence[str],
                     capabilities: Sequence[str] = ()) -> Tuple[Agent, str]:
        name = (display_name or "").strip()
        if not (1 <= len(name) <= 64) or any(ord(ch) < 32 for ch in name):
            raise ValueError("display name must be 1–64 printable characters")
        if role not in ROLES:
            raise ValueError("unknown role")
        if not scopes:
            raise ValueError("at least one scope is required")
        caps = sorted(set(capabilities))
        if not set(caps) <= CAPABILITIES:
            raise ValueError("unknown capability")
        if CAP_MEMORY_REPO_WRITE in caps and "update" not in ROLES[role]:
            raise ValueError("writing project memory needs a role with update")
        grants = [Grant(scope=s, actions=ROLES[role]) for s in scopes]          # validates scopes
        agent_id = "agt_" + _rand_id(10)
        self.store.put_agent(agent_id, name, role, [(gr.scope, ",".join(sorted(gr.actions)), "allow") for gr in grants],
                             caps)
        token = self.issue_token(agent_id)
        return self.agents[agent_id], token

    def issue_token(self, agent_id: str) -> str:
        token_id, secret = _rand_id(12), secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        self.store.put_token(token_id, agent_id, salt, _digest(salt, secret))
        self.reload()
        return f"tdf_{token_id}.{secret}"

    def revoke(self, token_id: str) -> bool:
        # Review 7 F1: deny in memory *before* the durable write, and never depend on a second I/O call for
        # the denial to hold. If the write fails the caller reports it; the token stays unusable here.
        self._snap = {k: v for k, v in self._snap.items() if k != token_id}
        ok = self.store.revoke_token(token_id)
        try:
            self.reload()
        except Exception:
            self._snap = {k: v for k, v in self._snap.items() if k != token_id}
        if token_id in self._snap:                       # a concurrent reload raced the write: deny again
            self._snap = {k: v for k, v in self._snap.items() if k != token_id}
        return ok

    def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        ok = self.store.set_agent_enabled(agent_id, enabled)
        self.reload()
        return ok

    def agent_for_token(self, token_id: Optional[str]) -> Optional[Agent]:
        """Main thread, at dispatch time: the token must still be valid *now* (revocation is immediate)."""
        rec = self._snap.get(token_id or "")
        if rec is None:
            return None
        a = self.agents.get(rec[2])
        return a if a and a.enabled else None

    def has_enabled_agent(self) -> bool:
        return bool(self._snap)

    def public_list(self) -> List[dict]:
        """For the paired Setup page: no salts, digests or secrets."""
        out = []
        for a in self.agents.values():
            out.append({"id": a.id, "display_name": a.display_name, "role": a.role, "enabled": a.enabled,
                        "created_at": self.created.get(a.id),
                        "scopes": [g.scope for g in a.grants], "capabilities": sorted(a.capabilities),
                        "tokens": [{"token_id": t.token_id, "created_at": t.created_at, "revoked": bool(t.revoked_at)}
                                   for t in self.tokens.values() if t.agent_id == a.id]})
        return out

    # ---- any thread: authentication against the immutable snapshot ---------------------------
    def authenticate(self, token: str) -> Optional[str]:
        """Return the token_id for a valid token, else None. Constant-time digest compare."""
        m = TOKEN_RE.match(token or "")
        snap = self._snap
        if not m:
            hmac.compare_digest("0" * 64, "1" * 64)          # keep timing similar for malformed tokens
            return None
        rec = snap.get(m.group(1))
        if rec is None:
            hmac.compare_digest("0" * 64, "1" * 64)
            return None
        ok = hmac.compare_digest(_digest(rec[0], m.group(2)), rec[1])
        return m.group(1) if ok else None


class Sessions:
    """Session grouping (§2.3): consecutive requests from one token with the same client info, ending
    after an idle gap. Observability only, never an authorization input. Main thread only."""

    def __init__(self, idle_seconds: float = 15 * 60, clock=time.monotonic):
        self.idle, self.clock = idle_seconds, clock
        self._open: Dict[Tuple[str, str], Tuple[str, float]] = {}

    def session_for(self, token_id: str, client_info: str) -> str:
        key, now = (token_id, client_info or ""), self.clock()
        cur = self._open.get(key)
        if cur is None or now - cur[1] > self.idle:
            cur = ("ses_" + _rand_id(12), now)
        self._open[key] = (cur[0], now)
        return cur[0]
