"""Native TouchDesigner administration (owner UX assignment, 2026-11-23): Connect Agent, the one-time token
handoff, the Agents page and pending approvals, without a browser.

One backend: every administrative action goes through the same handler the dashboard uses
(``TDflowMCPExt._dashboard_api``), with the principal ``admin:touchdesigner`` - someone at TouchDesigner, the same
trust the pairing approval already relies on. So the same validation, audit records and permission rules apply.
That handler waits for durable audit writes, so it runs on a short-lived background thread here, never on
TouchDesigner's main thread; results are applied on the main thread by ``tick`` (called from OnFrame).

Secrets: a newly created token lives only in memory (``Handoff``), for at most ``HANDOFF_SECONDS``, and leaves it
only through the clipboard (Copy Token). It is never written to a parameter, a DAT or a log. If the clipboard still
holds it ``CLIPBOARD_CLEAR_SECONDS`` after the copy, the clipboard is cleared.
"""
from __future__ import annotations

import json
import queue
import hashlib
import shutil
import sys
import threading
import time
import types

import td

HANDOFF_SECONDS = 120.0
CLIPBOARD_CLEAR_SECONDS = 60.0
ROLE_LABELS = {"observer": "Read Only", "builder": "Builder", "operator": "Operator"}
APPROVAL_VIEW_SECONDS = 120.0           # agent arguments never stay in a DAT (saved with the .toe) longer than this

def set_secret_clipboard(text):
    """Windows: put ``text`` on the clipboard so that Clipboard History and Cloud Clipboard do not keep or sync it and
    clipboard monitors are asked to ignore it (review 18). Formats: CanIncludeInClipboardHistory = 0,
    CanUploadToCloudClipboard = 0, ExcludeClipboardContentFromMonitorProcessing. Returns True on success; never
    falls back to a plain copy (the caller then refuses)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes, u32.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    u32.SetClipboardData.argtypes, u32.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
    u32.RegisterClipboardFormatW.argtypes, u32.RegisterClipboardFormatW.restype = [wintypes.LPCWSTR], wintypes.UINT
    k32.GlobalAlloc.argtypes, k32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
    k32.GlobalLock.argtypes, k32.GlobalLock.restype = [wintypes.HGLOBAL], ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    GMEM_MOVEABLE, CF_UNICODETEXT = 0x0002, 13

    def glob(data):
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return None
        ptr = k32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(h)
        return h
    for _ in range(5):
        if u32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        u32.EmptyClipboard()
        h = glob((text + "\0").encode("utf-16-le"))
        if not h or not u32.SetClipboardData(CF_UNICODETEXT, h):
            return False
        zero = (0).to_bytes(4, "little")
        for name, data in (("CanIncludeInClipboardHistory", zero), ("CanUploadToCloudClipboard", zero),
                           ("ExcludeClipboardContentFromMonitorProcessing", zero)):
            fmt = u32.RegisterClipboardFormatW(name)
            hd = glob(data)
            if not fmt or not hd or not u32.SetClipboardData(fmt, hd):
                u32.EmptyClipboard()
                return False
        return True
    finally:
        u32.CloseClipboard()



class Native:
    def __init__(self, ext):
        self.ext = ext
        self.results = queue.Queue()
        self.busy = False
        self.handoff = None                  # {"token", "agent_id", "name", "expires", "copied_at"}
        self.detected = []                   # client ids found on PATH (never run)
        self.pending_connect = None          # (name, role, scope) waiting for the store to open
        self.approvals = []                  # pending approvals (no arguments), refreshed in the background
        self._appr_at = 0.0
        self.viewed = None                   # (id, args_sha256, shown_at): the request View Approval showed
        self._clip = None                    # (sha256 of the token, clear_at): clipboard clearing, separate from handoff
        self.clipboard_writer = set_secret_clipboard     # replaceable by tests

    # ---- background calls into the one admin backend -----------------------------------------------
    def _call(self, path, body, then):
        """Run ``_dashboard_api`` on a background thread; ``then(status, data)`` runs on the main thread."""
        ext = self.ext
        st, idn = ext.store, ext.identity
        if st is None or idn is None or not st.available:
            ext._set_note("Storage not open yet")
            return False
        if self.busy:
            ext._set_note("Still working on the previous action; try again in a moment.")
            return False
        self.busy = True
        ext._audit_async("admin", tool="native_action", detail={"path": path, "via": "touchdesigner"})
        ctx = types.SimpleNamespace(path=path, method="POST", body=json.dumps(body).encode("utf-8"),
                                    principal="admin:touchdesigner", headers={}, rid="native")

        def run():
            try:
                r = ext._dashboard_api(ctx, st, idn)
                data = json.loads((r.body or b"{}").decode("utf-8") or "{}")
                self.results.put((then, r.status, data))
            except Exception as e:                          # never leak internals; report a plain failure
                self.results.put((then, 500, {"code": type(e).__name__, "message": "the action failed"}))
        threading.Thread(target=run, name="tdflowmcp-native", daemon=True).start()
        return True

    def tick(self):
        """Main thread, every frame: apply finished actions, expire the handoff, clear the clipboard."""
        ext = self.ext
        while True:
            try:
                then, status, data = self.results.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            try:
                then(status, data)
            except Exception as e:
                ext._set_note("TouchDesigner could not show the result (" + type(e).__name__ + ").")
        now = time.monotonic()
        c = self._clip
        if c and now > c[1]:
            self._clip = None
            try:
                cur = td.ui.clipboard
                if cur and hashlib.sha256(cur.encode("utf-8")).hexdigest() == c[0]:
                    td.ui.clipboard = ""
            except Exception:
                pass
        h = self.handoff
        if h and now > h["expires"]:                    # never longer than HANDOFF_SECONDS, copied or not
            self.handoff = None
            if not h["copied_at"]:
                ext._set_note("Token expired unused. Pulse New Token to make another.")
        v = self.viewed
        if v and now - v[2] > APPROVAL_VIEW_SECONDS:
            self.viewed = None
            d = ext.ownerComp.op("approval_view")
            if d is not None and d.text:
                d.text = ""
        if self.pending_connect and ext.identity is not None and ext.store is not None and ext.store.available:
            args, self.pending_connect = self.pending_connect, None
            self._create(*args)
        if ext.mcp_on and now - self._appr_at > 3.0 and not self.busy and ext.store is not None:
            self._appr_at = now
            self._refresh_approvals()

    # ---- detection and selection --------------------------------------------------------------------
    def detect(self):
        """Read-only discovery: command names on PATH (never run) plus known extension and configuration folders
        (names only). No paths are kept."""
        found = set(self.ext._m.tellagent.detect(self.ext.clients, shutil.which))
        found |= set(self.ext.connector.discover())
        order = [c["id"] for c in (self.ext.clients or {}).get("clients", [])]
        self.detected = [i for i in order if i in found]
        return self.detected

    def selected(self):
        p = self.ext.ownerComp.par
        sel = str(p.Codingagent.eval()) if hasattr(p, "Codingagent") else "auto"
        return self.ext._m.tellagent.choose(sel, self.detected, self.ext.clients)

    def primary_label(self):
        """One client to name a new agent after: the selected one, else the first detected primary client."""
        ids = self.selected()
        reg = {c["id"]: c for c in (self.ext.clients or {}).get("clients", [])}
        best = sorted(ids, key=lambda i: 0 if reg.get(i, {}).get("support") == "primary" else 1)
        return self.ext._m.tellagent.display(self.ext.clients, best[0]) if best else "Coding agent"

    def tell_clients(self):
        """Whose instructions Tell Your Coding Agent shows: the selected client; under Auto the agent that can be set
        up automatically (or the one connected), else every one found (usability review)."""
        ids = self.selected()
        ext = self.ext
        if str(ext.ownerComp.par.Codingagent.eval()) != "auto" if hasattr(ext.ownerComp.par, "Codingagent") else True:
            return ids
        st = ext.connector.state or {}
        if st.get("client") in ids:
            return [st["client"]]
        auto = [i for i in ids if ext.connector.automatic(i)]
        return auto[:1] or ids

    def client_label(self):
        """Who the status line waits for: one named client, else (several found by Auto) 'your agent'."""
        ids = self.selected()
        if len(ids) != 1:
            return "your agent"
        return self.ext._m.tellagent.display(self.ext.clients, ids[0])

    # ---- Connect Agent (TDflowMCP page) ---------------------------------------------------------------
    def connect(self):
        """Connect Agent (owner D22): environment check, then the agent. Clients that support it get the guided,
        consent-based connection (``connection.Connector``); the others get a credential for the manual fallback."""
        ext = self.ext
        st = ext.connector.state
        if st and st.get("stage") == "awaiting":           # a second pulse may be the approval (no dialog)
            ext.connector.start([st["client"]])
            return
        ext.RunDiagnostics()
        env = ext._m.readiness.environment(ext.checks)
        if env["state"] == "not_ready":
            ext._set_note("Not ready: see Diagnostics", 30)
            return
        is_auto = str(ext.ownerComp.par.Codingagent.eval()) == "auto"
        ids = [i for i in self.selected() if i != "other" or len(self.selected()) == 1]
        if not ids or ids == ["other"] and is_auto:
            ext._set_note("No agent found: pick one", 30)
            return
        auto = [i for i in ids if ext.connector.automatic(i)]
        if is_auto and len(auto) == 1:
            # Usability review: one agent can be set up automatically: recommend it without asking; the plan names
            # the others (they need manual setup and are chosen in Coding Agent).
            ext.connector.start(auto, also_found=[i for i in ids if i not in auto])
            return
        if len(ids) > 1:
            ext.connector.start(ids)                        # the user chooses; manual ones fall back below
            return
        if auto:
            ext.connector.start(auto)
            return
        self.connect_manual()

    def connect_manual(self, cid=None):
        """Manual fallback (clients without secure automatic provisioning): a credential for Copy Token."""
        ext, p = self.ext, self.ext.ownerComp.par
        role = str(p.Agentaccess.eval()) if hasattr(p, "Agentaccess") else "builder"
        scope = str(p.Allowedscope.eval()).strip() if hasattr(p, "Allowedscope") else ""
        scope = scope or (ext._root_text + "/**")
        label = ext._m.tellagent.display(ext.clients, cid) if cid else self.primary_label()
        name = f"{label} ({ROLE_LABELS.get(role, role)})"[:64]
        ext.ensure_store()
        if ext.identity is None or ext.store is None or not ext.store.available:
            self.pending_connect = (name, role, scope)
            ext._set_note("Preparing secure storage for the agent's credentials...", 10)
            return
        self._create(name, role, scope)

    def _create(self, name, role, scope):
        ext = self.ext

        def done(status, data):
            if status != 200 or not data.get("token"):
                code = data.get("code") or data.get("error") or status
                ext._set_note(f"No agent created ({code}): scope must be inside {ext._root_text}."[:80], 30)
                return
            a = data["agent"]
            self.handoff = {"token": data["token"], "agent_id": a["id"], "name": a["display_name"],
                            "expires": time.monotonic() + HANDOFF_SECONDS, "copied_at": None}
            ext._set_note("Copy Token (Agents page)", 60)
            ext._refresh_tell()
        self._call("/api/tokens", {"display_name": name, "role": role, "scope": scope}, done)

    def copy_token(self):
        ext, h = self.ext, self.handoff
        if not h or time.monotonic() > h["expires"]:
            self.handoff = None
            ext._set_note("No token: pulse New Token")
            return
        try:
            ok = bool(self.clipboard_writer(h["token"]))
        except Exception:
            ok = False
        if not ok:
            ext._set_note("Can't copy: use Open Setup")
            return
        h["copied_at"] = time.monotonic()
        self._clip = (hashlib.sha256(h["token"].encode("utf-8")).hexdigest(), h["copied_at"] + CLIPBOARD_CLEAR_SECONDS)
        ext._set_note("Token copied (1 min)", 60)
        ext._audit_async("admin", tool="token_handoff", agent_id=h["agent_id"], detail={"via": "clipboard"})

    # ---- Agents page ------------------------------------------------------------------------------------
    def agent_rows(self):
        idn = self.ext.identity
        return idn.public_list() if idn is not None else []

    def selected_agent(self):
        p = self.ext.ownerComp.par
        aid = str(p.Agentselect.eval()) if hasattr(p, "Agentselect") else ""
        return next((a for a in self.agent_rows() if a["id"] == aid), None)

    def refresh_menu(self):
        """Main thread: the Agent menu lists every agent (no secrets)."""
        p = self.ext.ownerComp.par
        if not hasattr(p, "Agentselect"):
            return
        rows = self.agent_rows()
        names = [a["id"] for a in rows] or ["none"]
        empty = "(not loaded yet)" if self.ext._not_loaded() else "(no agents yet)"   # rc4 (UX-09)
        labels = [a["display_name"] + ("" if a["enabled"] else " (disabled)") for a in rows] or [empty]
        par = p.Agentselect
        if list(par.menuNames) != names or list(par.menuLabels) != labels:
            cur = str(par.eval())
            par.menuNames, par.menuLabels = names, labels
            if cur not in names:
                par.val = names[0]
                self.on_select()

    def on_select(self):
        """Main thread: prefill Scope with the selected agent's first scope (for Remove Scope or a similar one)."""
        a = self.selected_agent()
        p = self.ext.ownerComp.par
        if a and a["scopes"] and hasattr(p, "Agentscope"):
            p.Agentscope = a["scopes"][0]

    def info_line(self, seen):
        a = self.selected_agent()
        if not a:
            return "Not loaded: pulse Manage Agents" if self.ext._not_loaded() else "No agent selected"
        live = [t for t in a["tokens"] if not t["revoked"]]
        last = seen.get(a["id"])
        when = f"seen {int(time.time() - last['last'])} s ago" if last else "not seen yet"
        n = len(a["scopes"])
        return (f"{'On' if a['enabled'] else 'Off'} · {ROLE_LABELS.get(a['role'], a['role'])} · {len(live)} token"
                f"{'' if len(live) == 1 else 's'} · {n} scope{'' if n == 1 else 's'} · {when}")[:200]

    def _need_agent(self):
        a = self.selected_agent()
        if not a:
            self.ext._set_note("Select an agent on the Agents page first.")
        return a

    def set_enabled(self, enabled):
        a = self._need_agent()
        if a:
            self._call("/api/agents/state", {"agent_id": a["id"], "enabled": enabled},
                       lambda s, d: self.ext._set_note(f"Agent '{a['display_name']}' "
                                                       + (("enabled." if enabled else "disabled.") if s == 200
                                                          else f"not changed ({d.get('code', s)}).")))

    def scope(self, add):
        a = self._need_agent()
        p = self.ext.ownerComp.par
        if not a:
            return
        scope = str(p.Agentscope.eval()).strip()
        if add:
            acts = sorted(self.ext._m.policy.ROLES[a["role"]])
            self._call("/api/grants/add", {"agent_id": a["id"], "scope": scope, "actions": acts, "effect": "allow"},
                       lambda s, d: self.ext._set_note(f"Scope {scope} " + ("added." if s == 200 else
                                                                            f"not added ({d.get('code', s)}).")))
            return
        def removed(s, d):
            self.ext._set_note(f"Scope {scope} " + ("removed." if s == 200 else f"not removed ({d.get('code', s)})."))

        def found(status, data):                     # the grants come from the same backend (never the main thread)
            row = next((x for x in data.get("agents") or [] if x.get("id") == a["id"]), {})
            gid = next((g["id"] for g in row.get("grants") or [] if g.get("scope") == scope), None)
            if gid is None:
                self.ext._set_note(f"'{a['display_name']}' has no grant with scope {scope}.")
                return
            self._call("/api/grants/remove", {"agent_id": a["id"], "grant_id": gid}, removed)
        self._call_get("/api/agents", found)

    def rotate(self):
        """New token for the selected agent: its other tokens are revoked; the new one goes to the handoff."""
        a = self._need_agent()
        if not a:
            return

        def done(status, data):
            if status != 200 or not data.get("token"):
                self.ext._set_note(f"No new token ({data.get('code', status)}).")
                return
            self.handoff = {"token": data["token"], "agent_id": a["id"], "name": a["display_name"],
                            "expires": time.monotonic() + HANDOFF_SECONDS, "copied_at": None}
            self.ext._set_note("New token made, old ones revoked. Pulse Copy Token (2 min).", 60)
        self._call("/api/agents/rotate", {"agent_id": a["id"]}, done)

    def revoke_all(self):
        a = self._need_agent()
        if a:
            self._call("/api/agents/rotate", {"agent_id": a["id"], "revoke_only": True},
                       lambda s, d: self.ext._set_note(f"All tokens of '{a['display_name']}' revoked." if s == 200
                                                       else f"Nothing revoked ({d.get('code', s)})."))

    def view_agents(self, seen):
        lines = ["TDflowMCP agents (no tokens are ever shown here)", ""]
        for a in self.agent_rows():
            live = [t for t in a["tokens"] if not t["revoked"]]
            last = seen.get(a["id"])
            lines.append(f"- {a['display_name']} [{a['id']}]: {ROLE_LABELS.get(a['role'], a['role'])}, "
                         f"{'enabled' if a['enabled'] else 'disabled'}, {len(live)} active token(s), "
                         f"scopes {', '.join(a['scopes']) or 'none'}, "
                         + (f"last request {int(time.time() - last['last'])} s ago" if last else "no request seen yet"))
        if len(lines) == 2:
            lines.append("No agents yet. Pulse Connect Agent on the TDflowMCP page.")
        lines += ["", "Manage: select an agent on the Agents page; Add Scope / Remove Scope, Enable / Disable, "
                  "New Token, Revoke Tokens. The dashboard (Open Dashboard) shows the same agents."]
        self._show("agents_view", "\n".join(lines))

    def _show(self, dat, text):
        d = self.ext.ownerComp.op(dat)
        if d is None:
            return
        d.text = text
        try:
            d.openViewer()
        except Exception:
            pass

    # ---- approvals --------------------------------------------------------------------------------------
    def _refresh_approvals(self):
        ext = self.ext

        def done(status, data):
            self.approvals = [a for a in (data.get("approvals") or []) if a.get("state") == "pending"]
        self._call_get("/api/approvals", done)

    def _call_get(self, path, then):
        ext = self.ext
        st, idn = ext.store, ext.identity
        if st is None or idn is None or not st.available or self.busy:
            return
        self.busy = True
        ctx = types.SimpleNamespace(path=path, method="GET", body=b"", principal="admin:touchdesigner", headers={},
                                    rid="native")

        def run():
            try:
                r = ext._dashboard_api(ctx, st, idn)
                self.results.put((then, r.status, json.loads((r.body or b"{}").decode("utf-8") or "{}")))
            except Exception:
                self.results.put((then, 500, {}))
        threading.Thread(target=run, name="tdflowmcp-native", daemon=True).start()

    def approval_line(self):
        if not self.approvals:
            return "none waiting"
        a = self.approvals[0]
        more = f" (+{len(self.approvals) - 1} more)" if len(self.approvals) > 1 else ""
        tool = str(a.get('tool') or "").replace("tdflow_", "")
        return f"{len(self.approvals)} waiting: {a.get('agent_name') or a.get('agent_id')} · {tool}{more}"[:80]

    def view_approval(self):
        if not self.approvals:
            self.ext._set_note("No approval request is waiting.")
            return
        a = self.approvals[-1]                               # the oldest is last (newest first from the backend)
        self.viewed = (a["id"], a.get("args_sha256"), time.monotonic())
        lines = [f"Approval request {a.get('id')} from {a.get('agent_name') or a.get('agent_id')}", f"Tool: {a.get('tool')}",
                 "", "Why it needs approval:"] + [f"- {r}" for r in a.get("reasons") or []] + \
                ["", "Targets:"] + [f"- {t}" for t in a.get("targets") or []] + \
                ["", "Complete arguments (exactly what will run):", str(a.get("args") or "")]
        self._show("approval_view", "\n".join(lines))
        self.ext._set_note("Review, then approve/deny", 30)

    def decide(self, approve):
        """Review 18: only the request View Approval showed (same id and argument digest) is decided."""
        if not self.viewed:
            self.ext._set_note("Pulse View Approval first")
            return
        aid, sha, _ = self.viewed
        a = {"id": aid}
        decision = "approved" if approve else "denied"

        def done(status, data):
            self.ext._set_note(f"Request {a.get('id')} {decision}." if status == 200
                               else f"Request not decided ({data.get('code', status)}).")
            self._appr_at = 0.0
            self.viewed = None
            d = self.ext.ownerComp.op("approval_view")
            if d is not None:
                d.text = ""                                  # never keep agent arguments in the project
        self._call("/api/approvals/decide", {"id": aid, "decision": decision, "args_sha256": sha}, done)
