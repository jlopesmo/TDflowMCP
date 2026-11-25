"""S5 spike: Web Server DAT callback logic (disposable; not TDflowMCP source).

Implements the VERIFYING -> SERVING gate from security-model §8.2 in miniature:
every request gets 503 until the runtime flips STATE['mode'] to 'SERVING'.
"""
import json
import threading
import time

STATE = {
    "mode": "VERIFYING",
    "main_ident": None,
    "log": [],          # per-request records (bounded)
    "browser": [],      # records from the browser page
    "pulses": [],       # parameter-execute pulse callbacks observed
    "server_events": [],
    "done_requested": False,
}

BROWSER_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>TDflowMCP S5 test</title></head>
<body style="font-family:sans-serif"><h1>TDflowMCP S5 compatibility test</h1>
<p>This tab was opened by a disposable TouchDesigner test. You can close it.</p>
<pre id="o">running…</pre>
<script>
(async () => {
  const out = [];
  try { const r = await fetch('/s5/echo?from=browser-get', {headers: {'X-TDflow-Request': '1'}});
        out.push('get ' + r.status); } catch (e) { out.push('get error ' + e); }
  try { const r = await fetch('/s5/echo?from=browser-post', {method: 'POST', headers: {'X-TDflow-Request': '1', 'Content-Type': 'application/json'}, body: '{"x":1}'});
        out.push('post ' + r.status); } catch (e) { out.push('post error ' + e); }
  document.getElementById('o').textContent = out.join('\\n');
})();
</script></body></html>"""


def _headers_view(request):
    """Return every non-body key of the request dict (values truncated) to learn the API shape."""
    view = {}
    for k, v in request.items():
        if k == "data":
            continue
        s = v if isinstance(v, str) else repr(v)
        view[str(k)] = s[:200]
    return view


def _resp(response, code, reason, body, ctype="application/json"):
    response["statusCode"] = code
    response["statusReason"] = reason
    response["content-type"] = ctype
    response["data"] = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
    return response


def onHTTPRequest(webServerDAT, request, response):
    t0 = time.perf_counter()
    uri = str(request.get("uri", ""))
    data = request.get("data")
    rec = {
        "t": time.time(),
        "uri": uri,
        "method": request.get("method"),
        "mode": STATE["mode"],
        "on_main_thread": threading.get_ident() == STATE["main_ident"],
        "thread_name": threading.current_thread().name,
        "body_len": len(data) if data else 0,
    }
    if len(STATE["log"]) < 2000:
        STATE["log"].append(rec)

    if STATE["mode"] != "SERVING":
        return _resp(response, 503, "Service Unavailable", "TDFLOW_VERIFYING", "text/plain")

    path = uri.split("?", 1)[0]
    if path == "/s5/ping":
        _resp(response, 200, "OK", json.dumps({"ok": True}))
    elif path == "/s5/echo":
        view = _headers_view(request)
        if "from=browser" in uri or "browser" in str(request.get("pars", "")):
            STATE["browser"].append({"uri": uri, "method": request.get("method"), "request": view,
                                     "body_len": rec["body_len"]})
        _resp(response, 200, "OK", json.dumps({"request": view, "body_len": rec["body_len"]}))
    elif path == "/s5/browser":
        STATE["browser"].append({"uri": uri, "method": request.get("method"), "request": _headers_view(request),
                                 "page_load": True})
        _resp(response, 200, "OK", BROWSER_PAGE, "text/html; charset=utf-8")
    elif path == "/s5/status403":
        response["X-TDflow-Test"] = "1"
        _resp(response, 403, "Forbidden", json.dumps({"denied": True}))
    elif path == "/s5/state":
        _resp(response, 200, "OK", json.dumps({
            "browser": STATE["browser"], "pulses": STATE["pulses"],
            "server_events": STATE["server_events"],
            "log_tail": STATE["log"][-5:], "log_count": len(STATE["log"]),
            "thread_summary": sorted({(r["on_main_thread"], r["thread_name"]) for r in STATE["log"]}),
        }, default=str))
    elif path == "/s5/done":
        STATE["done_requested"] = True
        import td
        td.run("import s5_runtime; s5_runtime.phase_c()", delayFrames=2)
        _resp(response, 200, "OK", json.dumps({"finishing": True}))
    else:
        _resp(response, 404, "Not Found", json.dumps({"error": "unknown"}))
    rec["handler_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return response


def onServerStart(webServerDAT):
    STATE["server_events"].append(("start", time.time(), threading.get_ident() == STATE["main_ident"]))


def onServerStop(webServerDAT):
    STATE["server_events"].append(("stop", time.time(), threading.get_ident() == STATE["main_ident"]))


def onWebSocketOpen(webServerDAT, client, uri):
    return


def onWebSocketClose(webServerDAT, client):
    return


def onWebSocketReceiveText(webServerDAT, client, data):
    return


def onWebSocketReceiveBinary(webServerDAT, client, data):
    return


def onWebSocketReceivePing(webServerDAT, client, data):
    webServerDAT.webSocketSendPong(client, data=data)


def onWebSocketReceivePong(webServerDAT, client, data):
    return
