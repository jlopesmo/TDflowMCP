"""A minimal Chrome DevTools Protocol client (stdlib only) for the rendered-page checks of the web UI.

It launches an installed Chromium browser (Microsoft Edge or Google Chrome; nothing is installed) headless, with a
disposable profile under the given folder and the debugging port bound to 127.0.0.1, and talks to one page over a
WebSocket. Used only by ``run_web_screens.py``; never by the product.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request

BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def find_browser():
    return next((b for b in BROWSERS if os.path.exists(b)), None)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class WebSocket:
    def __init__(self, url, timeout=30):
        assert url.startswith("ws://")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head[:200])

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def send(self, text):
        data = text.encode("utf-8")
        hdr = bytes([0x81])
        n = len(data)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 0x8:
                raise ConnectionError("closed by browser")
            if op == 0x9:
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8")


class Browser:
    def __init__(self, profile_dir):
        exe = find_browser()
        if not exe:
            raise RuntimeError("no installed Edge or Chrome")
        self.exe = exe
        self.port = free_port()
        self.proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={self.port}",
                                      "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile_dir}",
                                      "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                                      "--disable-sync", "--disable-background-networking", "--disable-gpu",
                                      "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2))
                url = next((t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page"), None)
                if url:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        if not url:
            self.close()
            raise RuntimeError("the browser did not start")
        self.ws = WebSocket(url)
        self.n, self.events = 0, []
        self.call("Page.enable")
        self.call("Runtime.enable")

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            self.events.append(m)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:400])
        return (r.get("result") or {}).get("value")

    def size(self, w, h):
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

    def goto(self, url, settle=1.5):
        self.call("Page.navigate", url=url)
        time.sleep(settle)

    def wait(self, expr, seconds=8.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.js(expr):
                return True
            time.sleep(0.1)
        return False

    def key(self, key, code=None, keycode=0, shift=False):
        mods = 8 if shift else 0
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key=key, code=code or key, windowsVirtualKeyCode=keycode,
                      modifiers=mods)

    def screenshot(self, path, full=False):
        params = {"format": "png"}
        if full:
            params["captureBeyondViewport"] = True
            m = self.call("Page.getLayoutMetrics")
            cs = m.get("cssContentSize") or m.get("contentSize")
            params["clip"] = {"x": 0, "y": 0, "width": cs["width"], "height": cs["height"], "scale": 1}
        data = self.call("Page.captureScreenshot", **params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
