"""Rendered checks of the web UI in a real browser (owner D25): the dashboard's Capabilities page and the pairing code
on the Setup and dashboard pages, at several window sizes, with keyboard and pointer checks and screenshots.

    python -I tests/web/run_web_screens.py

The pages are the real ``src/dashboard`` and ``src/setup/web`` files. A stub server on 127.0.0.1 answers their API
with data built by the real ``tdflowmcp.capabilities`` module (the same function the component uses), so the matrix
and the preview are the product's own. Nothing talks to TouchDesigner; the component's API itself is covered by the
TouchDesigner suites. An installed Edge or Chrome runs headless with a disposable profile under .tmp (nothing is
installed). Screenshots and results go to .tmp/web-screens/ (git-ignored).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
from tdflowmcp import capabilities as C  # noqa: E402
from tdflowmcp.policy import ROLES, Agent  # noqa: E402
import cdp  # noqa: E402

OUT = os.path.join(ROOT, ".tmp", "web-screens")
CSRF = "stub-csrf"
PROJECT = "/project1"
RESULTS = []


def T(name, ok, **detail):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail if not ok else {}})
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + json.dumps(detail, default=str)[:400]))


class Stub:
    """The API the pages call, built on the real capability module."""

    def __init__(self):
        self.code = "WWW-MMM"
        self.applied = []
        self.agents = {}
        self._gid = 0
        for aid, name, role, grants, caps in (
                ("agt_claude", "Claude Code (Builder)", "builder", [(PROJECT + "/**", sorted(ROLES["builder"]), "allow")], ()),
                ("agt_cursor", "Cursor (Read Only)", "observer", [(PROJECT + "/**", ["read"], "allow")], ()),
                ("agt_codex", "Codex (Operator)", "operator", [(PROJECT + "/fx/**", sorted(ROLES["operator"]), "allow"),
                                                               (PROJECT + "/fx/private/**", ["delete", "update"], "deny")],
                 ("memory:repo:write",))):
            rows = []
            for scope, acts, eff in grants:
                self._gid += 1
                rows.append({"id": self._gid, "scope": scope, "actions": list(acts), "effect": eff})
            self.agents[aid] = {"agent": Agent(aid, name, role, capabilities=frozenset(caps)), "rows": rows}

    facts = {"active": None, "code_mode": False, "approvals": True, "root": PROJECT}

    def view(self):
        return C.dashboard_view([(x["agent"], x["rows"], aid == "agt_claude") for aid, x in self.agents.items()],
                                self.facts, PROJECT, {"mcp": True, "suspended": False, "code_mode": False},
                                "http://127.0.0.1:9480/ui/#capabilities")

    def preview(self, body):
        x = self.agents.get(str(body.get("agent_id")))
        if x is None:
            return 404, {"code": "NOT_FOUND", "message": "no such agent"}
        try:
            return 200, C.preview(x["agent"], x["rows"], body.get("change"), PROJECT, self.facts)
        except C.ChangeError as e:
            return 400, {"code": e.code, "message": e.message}

    def apply(self, body):
        st, pv = self.preview(body)
        if st != 200:
            return st, pv
        if body.get("digest") != pv["digest"]:
            return 409, {"code": "PREVIEW_CHANGED", "message": "preview again"}
        if pv["escalation"] and body.get("confirm_escalation") is not True:
            return 409, {"code": "CONFIRMATION_REQUIRED", "message": "confirm it explicitly"}
        x = self.agents[body["agent_id"]]
        plan = pv["plan"]
        rows = [dict(r, actions=dict((i, a) for i, a in plan["rewrite"]).get(r["id"], r["actions"]))
                for r in x["rows"] if r["id"] not in plan["remove"]]
        for g in plan["add"]:
            self._gid += 1
            rows.append(dict(g, id=self._gid))
        a = x["agent"]
        x["agent"] = Agent(a.id, a.display_name, plan["role"], capabilities=frozenset(plan["capabilities"]))
        x["rows"] = rows
        self.applied.append(body)
        return 200, {"ok": True, "agent_id": a.id, "approvals_cancelled": 1, "role": plan["role"], "grants": rows}

    def agents_tab(self):
        out = []
        for aid, x in self.agents.items():
            a = x["agent"]
            out.append({"id": aid, "display_name": a.display_name, "role": a.role, "enabled": True,
                        "scopes": [r["scope"] for r in x["rows"]], "capabilities": sorted(a.capabilities),
                        "tokens": [{"token_id": "Tok" + aid[-6:], "created_at": "2026-11-25", "revoked": False}],
                        "grants": x["rows"], "last_seen_s": 3 if aid == "agt_claude" else None,
                        "connected": aid == "agt_claude"})
        return {"agents": out, "project_root": PROJECT, "roles": {r: sorted(v) for r, v in ROLES.items()}}


STUB = Stub()
FILES = {"/ui/": ("src/dashboard/index.html", "text/html"), "/ui": ("src/dashboard/index.html", "text/html"),
         "/ui/app.js": ("src/dashboard/app.js", "text/javascript"), "/ui/app.css": ("src/dashboard/app.css", "text/css"),
         "/setup/": ("src/setup/web/index.html", "text/html"), "/setup/app.js": ("src/setup/web/app.js", "text/javascript"),
         "/setup/app.css": ("src/setup/web/app.css", "text/css")}
BOOT = ("<!doctype html><meta charset='utf-8'><script>sessionStorage.setItem('tdflow_csrf','" + CSRF + "');"
        "location.replace(new URLSearchParams(location.search).get('to'));</script>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in FILES:
            f, ctype = FILES[path]
            with open(os.path.join(ROOT, f), "rb") as fh:
                return self._send(200, fh.read(), ctype + "; charset=utf-8")
        if path == "/boot.html":
            return self._send(200, BOOT.encode(), "text/html")
        if path == "/clear.html":
            return self._send(200, b"<!doctype html><script>sessionStorage.clear()</script>", "text/html")
        paired = self.headers.get("X-TDflow-CSRF") == CSRF
        if path == "/setup/api/status":
            return self._send(200, {"state": "Enabled", "message": "MCP on (this computer only).", "version": "1.0.0",
                                    "td_build": "2025.32440", "url": "http://127.0.0.1:9480/setup/", "port": 9480,
                                    "portstatus": "in use by TDflowMCP", "gate": "SERVING", "mcp": "on",
                                    "endpoint": "http://127.0.0.1:9480/mcp", "summary": "Ready", "checks": []})
        if path == "/setup/api/pair/status":
            return self._send(200, {"state": "pending", "match_code": STUB.code})
        if not paired:
            return self._send(401, {"error": "UNAUTHORIZED"})
        if path == "/setup/api/agents":
            return self._send(200, {"agents": STUB.agents_tab()["agents"], "sessions": []})
        if path == "/api/overview":
            return self._send(200, {"state": "Enabled", "detail": "Agent Connected — Ready", "mcp": True, "cooking": True,
                                    "suspended": False, "endpoint": "http://127.0.0.1:9480/mcp", "project_root": PROJECT,
                                    "agents": len(STUB.agents), "approvals_pending": 0, "last_seq": 120,
                                    "audit": {"events": 120, "chain_ok": True, "checkpoints_ok": True, "ok": True,
                                              "unverified_tail": 0}, "storage": {"location": "project folder"},
                                    "version": "1.0.0", "td_build": "2025.32440"})
        if path == "/api/capabilities":
            return self._send(200, STUB.view())
        if path == "/api/agents":
            return self._send(200, STUB.agents_tab())
        if path == "/api/approvals":
            return self._send(200, {"approvals": [], "pending": 0, "expiry_s": 600})
        return self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        path = self.path.split("?", 1)[0]
        if path == "/setup/api/pair":
            return self._send(200, {"state": "pending", "match_code": STUB.code, "message": "Compare this code."})
        if self.headers.get("X-TDflow-CSRF") != CSRF:
            return self._send(403, {"error": "MISSING_CSRF"})
        if path == "/api/permissions/preview":
            return self._send(*STUB.preview(body))
        if path == "/api/permissions/apply":
            return self._send(*STUB.apply(body))
        if path == "/api/audit/search":
            return self._send(200, {"events": [], "more": False, "last_seq": 120})
        return self._send(404, {"error": "NOT_FOUND"})


# ---- checks run inside the page -------------------------------------------------------------------------------------
CLIPPED = """(() => { const bad = []; for (const e of document.querySelectorAll(SEL)) { const r = e.getBoundingClientRect();
  if (r.width === 0) continue; if (e.scrollWidth > e.clientWidth + 1 || r.left < -1 || r.right > innerWidth + 1)
  bad.push((e.id || e.className) + ':' + e.textContent.slice(0, 30)); } return bad; })()"""


def clipped(b, sel):
    return b.js(CLIPPED.replace("SEL", json.dumps(sel)))


def pairing(b, base, page, sizes):
    for code in ("WWW-MMM", "AAA-222", "MWM-WMW"):
        STUB.code = code
        for w, h in sizes:
            b.size(w, h)
            b.goto(base + "/clear.html", 0.2)
            b.goto(base + page + "#pair", 0.3)
            shown = b.wait(f"document.getElementById('pair-code') && !document.getElementById('pair-code').hidden && "
                           f"document.getElementById('pair-code').textContent === {json.dumps(code)}")
            tag = f"{page.strip('/') or 'ui'}.{code}.{w}x{h}"
            T(f"pair.CODE_SHOWN.{tag}", shown)
            T(f"pair.CODE_NOT_CLIPPED.{tag}", shown and not clipped(b, "#pair-code"), bad=clipped(b, "#pair-code"))
            fs = b.js("parseFloat(getComputedStyle(document.getElementById('pair-code')).fontSize)")
            T(f"pair.CODE_READABLE.{tag}", fs and fs >= 18, font_px=fs)
            T(f"pair.HINT_NAMES_THE_BUTTON.{tag}", "press the button listed next to the code below" in (b.js(
                "document.getElementById('pair-status').textContent") or ""))
            hit = b.js("(() => { const e = document.getElementById('pair'); e.scrollIntoView({block: 'center'}); "
                       "const r = e.getBoundingClientRect(); "
                       "return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === e && "
                       "r.right <= innerWidth; })()")
            T(f"pair.BUTTON_CLICKABLE.{tag}", hit)
            if code == "WWW-MMM":
                b.js("document.activeElement && document.activeElement.blur()")
                focused = False
                for _ in range(12):
                    b.key("Tab", "Tab", 9)
                    if b.js("document.activeElement && document.activeElement.id === 'pair'"):
                        focused = True
                        break
                outline = b.js("getComputedStyle(document.getElementById('pair')).outlineStyle")
                T(f"pair.KEYBOARD_FOCUS_VISIBLE.{tag}", focused and outline not in (None, "none"), outline=outline)
                b.screenshot(os.path.join(OUT, f"pair-{page.strip('/') or 'ui'}-{w}x{h}.png"))


def capabilities(b, base, sizes):
    b.size(1920, 1080)
    b.goto(base + "/clear.html", 0.2)
    b.goto(base + "/ui/#capabilities", 0.5)
    T("caps.DEEP_LINK_ASKS_TO_PAIR_FIRST", b.wait("document.getElementById('pair-status').textContent.includes("
                                                  "'capabilities')") and b.js("document.getElementById('app').hidden"))
    n_rows = sum(1 for c in C.CATALOG if c.group not in ("protected", "future"))
    for w, h in sizes:
        b.size(w, h)
        b.goto(base + "/boot.html?to=" + "/ui/%23capabilities", 0.3)
        ok = b.wait(f"document.querySelectorAll('#caps tbody tr').length === {n_rows}")
        tag = f"{w}x{h}"
        T(f"caps.DEEP_LINK_OPENS_THE_PAGE.{tag}", ok and b.js("document.body.dataset.tab") == "capabilities",
          rows=b.js("document.querySelectorAll('#caps tbody tr').length"))
        T(f"caps.LEGEND_FIVE_STATUSES.{tag}", b.js("Array.from(document.querySelectorAll('#c-legend .chip')).map(e => "
                                                    "e.textContent).join('|')") ==
          "Available|Approval Required|Denied|Not Implemented|Protected")
        bad = clipped(b, "#caps .chip, #c-legend .chip, .caps-fixed .chip")
        T(f"caps.NO_CLIPPED_STATUS.{tag}", not bad, bad=bad[:5])
        hscroll = b.js("(() => { const s = document.querySelector('.caps-scroll'); return [s.scrollWidth, s.clientWidth, "
                       "document.documentElement.scrollWidth, innerWidth]; })()")
        T(f"caps.NO_HORIZONTAL_SCROLL.{tag}", hscroll[0] <= hscroll[1] + 1 and hscroll[2] <= hscroll[3] + 1, sizes=hscroll)
        rowh = b.js("(() => { const h = Array.from(document.querySelectorAll('#caps tbody tr')).map(r => "
                    "r.getBoundingClientRect().height).sort((a, b) => a - b); return h[Math.floor(h.length / 2)]; })()")
        T(f"caps.COMPACT_ROWS.{tag}", rowh <= 34, median_row_px=rowh)
        geo = b.js("(() => { const t = document.getElementById('caps').getBoundingClientRect(); const e = document."
                   "querySelector('.caps-edit').getBoundingClientRect(); return {table_h: Math.round(t.height), "
                   "page_h: document.documentElement.scrollHeight, side: e.left >= t.right - 1, edit_w: "
                   "Math.round(e.width)}; })()")
        if w >= 1500:
            T(f"caps.EDITOR_BESIDE_THE_MATRIX.{tag}", geo["side"] and geo["edit_w"] >= 300, geo=geo)
        else:
            T(f"caps.JUMP_TO_THE_EDITOR.{tag}", b.js("(() => { const j = document.getElementById('c-jump'); "
                                                     "return j.getBoundingClientRect().width > 0 && j.getBoundingClientRect()"
                                                     ".bottom <= innerHeight; })()"))
        T(f"caps.MATRIX_FITS_ABOUT_ONE_SCREEN.{tag}", geo["table_h"] <= 1.25 * h, geo=geo)
        b.screenshot(os.path.join(OUT, f"caps-{w}x{h}.png"))
        if w in (1920, 1366):
            b.screenshot(os.path.join(OUT, f"caps-{w}x{h}-full.png"), full=True)
    T("caps.LEGEND_DEFINES_EACH_STATUS", "you can change this" in (b.js("document.getElementById('c-legend').textContent") or ""))
    T("caps.DENIED_CELLS_SAY_WHY", "· role" in (b.js("document.querySelector('#caps tr[data-cap=\"expressions\"] td.agentcell')"
                                                    ".textContent") or ""))
    T("caps.PROTECTED_HAS_NO_CONTROLS", b.js("document.querySelectorAll('.caps-fixed input, .caps-fixed button, "
                                              ".caps-fixed select').length") == 0)


def editing(b, base):
    b.size(1920, 1080)
    b.goto(base + "/boot.html?to=" + "/ui/%23capabilities", 0.3)
    b.wait("document.querySelectorAll('#caps tbody tr').length > 10")
    # Keyboard: rows are focusable; Enter shows the detail; Tab moves to the next row.
    b.js("document.querySelector('#caps tbody tr').focus()")
    b.key("Enter", "Enter", 13)
    T("edit.KEYBOARD_ROW_DETAIL", "Tools: tdflow_whoami" in (b.js("document.getElementById('c-detail').textContent") or ""),
      detail=b.js("document.getElementById('c-detail').textContent"))
    b.key("ArrowDown", "ArrowDown", 40)
    T("edit.ARROW_MOVES_TO_THE_NEXT_ROW", b.js("document.activeElement === document.querySelectorAll('#caps tbody tr')[1]"))
    T("edit.ONE_TAB_STOP_FOR_THE_MATRIX", b.js("document.querySelectorAll('#caps tbody tr[tabindex=\"0\"]').length") == 1)
    T("edit.FOCUS_VISIBLE_ON_ROWS", b.js("getComputedStyle(document.activeElement).outlineStyle") != "none")
    # Builder -> Operator: preview shows the effect and needs an explicit confirmation before Apply.
    b.js("(() => { const s = document.getElementById('c-agent'); s.value = 'agt_claude'; s.dispatchEvent(new Event('change')); })()")
    time.sleep(1.0)                                         # the agent switch reloads the page data first
    b.wait("document.getElementById('c-role').value === 'builder'")
    b.js("(() => { const s = document.getElementById('c-role'); s.value = 'operator'; s.dispatchEvent(new Event('change')); })()")
    b.js("document.getElementById('c-preview').click()")
    ok = b.wait("!document.getElementById('c-result').hidden")
    T("edit.PREVIEW_SHOWN", ok)
    T("edit.PREVIEW_STORES_NOTHING", not STUB.applied and STUB.agents["agt_claude"]["agent"].role == "builder")
    T("edit.ESCALATION_WARNED", b.js("!document.getElementById('c-escalation').hidden && document.getElementById("
                                     "'c-escalation').textContent.includes('more access')"))
    T("edit.APPLY_NEEDS_CONFIRMATION", b.js("document.getElementById('c-apply').disabled") is True)
    T("edit.MATRIX_SHOWS_THE_PROPOSED_STATE", (b.js("document.querySelectorAll('#caps tr.changed').length") or 0) >= 5)
    T("edit.DIFF_LISTS_CHANGES", "Approval Required" in (b.js("document.getElementById('c-diff').textContent") or ""))
    T("edit.SCOPES_AFTER_SHOW_ADDED_ACTIONS", "(+ execute)" in (b.js("document.getElementById('c-scopes-after').textContent") or ""),
      after=b.js("document.getElementById('c-scopes-after').textContent"))
    T("edit.RESULT_ON_SCREEN", b.js("(() => { const r = document.getElementById('c-apply').getBoundingClientRect(); "
                                    "return r.top >= 0 && r.bottom <= innerHeight + 1; })()"))
    T("edit.RENAME_NOTED", "Claude Code (Operator)" in (b.js("document.getElementById('c-notes').textContent") or ""))
    b.screenshot(os.path.join(OUT, "caps-preview-1920x1080.png"))
    b.js("document.getElementById('c-confirm').click()")
    T("edit.CONFIRM_ENABLES_APPLY", b.js("document.getElementById('c-apply').disabled") is False)
    b.js("document.getElementById('c-apply').click()")
    T("edit.APPLIED", b.wait("document.getElementById('c-status').textContent.startsWith('Applied')"),
      status=b.js("document.getElementById('c-status').textContent"))
    T("edit.APPLIED_WITH_CONFIRMATION", bool(STUB.applied) and STUB.applied[-1].get("confirm_escalation") is True
      and STUB.agents["agt_claude"]["agent"].role == "operator")
    T("edit.MATRIX_REFRESHED", b.wait("(() => { const r = document.querySelector('#caps tr[data-cap=\"expressions\"] "
                                      "td.agentcell .chip'); return r && r.dataset.status === 'approval'; })()"))
    b.screenshot(os.path.join(OUT, "caps-applied-1920x1080.png"))
    # Unsupported fields never reach the backend as editable permissions.
    st = b.js("fetch('/api/permissions/preview', {method: 'POST', headers: {'X-TDflow-CSRF': '" + CSRF + "', "
              "'Content-Type': 'application/json'}, body: JSON.stringify({agent_id: 'agt_claude', change: {protected: "
              "{p_root: true}}})}).then(r => r.json())")
    T("edit.UNSUPPORTED_CHANGE_REFUSED", (st or {}).get("code") == "UNSUPPORTED_CHANGE", st=st)
    # The Agents tab's Add permission goes through the same preview and confirmation.
    n = len(STUB.applied)
    b.js("window.__confirms = 0; window.confirm = () => { window.__confirms++; return true; };")
    b.js("document.querySelector('[role=tab][data-tab=agents]').click()")
    b.wait("document.querySelectorAll('#agents tbody tr').length === 3")
    b.js("(() => { const row = Array.from(document.querySelectorAll('#agents tbody tr')).find(r => r.textContent.includes("
         "'Cursor')); const ins = row.querySelectorAll('.row input'); ins[0].value = '/project1/out/**'; ins[1].value = "
         "'read'; Array.from(row.querySelectorAll('button')).find(x => x.textContent === 'Add permission').click(); })()")
    T("edit.AGENTS_TAB_USES_PREVIEW_AND_CONFIRMATION", b.wait("window.__confirms === 1") and len(STUB.applied) == n + 1
      and STUB.applied[-1].get("confirm_escalation") is True, confirms=b.js("window.__confirms"), applied=len(STUB.applied) - n)


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    prof = tempfile.mkdtemp(prefix="web-screens-profile-", dir=os.path.join(ROOT, ".tmp"))
    b = None
    t0 = time.time()
    try:
        b = cdp.Browser(prof)
        ver = b.call("Browser.getVersion")
        print("browser:", ver.get("product"))
        pairing(b, base, "/ui/", [(1920, 1080), (1280, 800), (768, 1024), (480, 800), (360, 740)])
        pairing(b, base, "/setup/", [(1920, 1080), (768, 1024), (360, 740)])
        capabilities(b, base, [(1920, 1080), (1600, 900), (1366, 768), (1280, 800)])
        editing(b, base)
    finally:
        if b is not None:
            b.close()
        srv.shutdown()
        shutil.rmtree(prof, ignore_errors=True)
    failed = [r for r in RESULTS if not r["ok"]]
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"browser": ver.get("product"), "seconds": round(time.time() - t0, 1), "passed": len(RESULTS) - len(failed),
                   "failed": len(failed), "results": RESULTS}, f, indent=1)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed; screenshots in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
