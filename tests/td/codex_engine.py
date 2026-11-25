"""Drive a real Codex engine for tests: ``codex app-server`` over stdio (JSON lines), the process the Codex VS Code
extension itself runs. Runs OUTSIDE TouchDesigner.

- The engine gets ``CODEX_HOME`` = a disposable folder, so the user's real Codex profile (configuration, sign-in,
  sessions) is never read or written. No model is called and no sign-in is needed: ``mcpServerStatus/list`` makes the
  engine connect to its configured MCP servers, and ``mcpServer/tool/call`` makes it call one tool.
- Engines are located read-only: the binary bundled in the installed VS Code extension, and the one behind the
  ``codex`` command on PATH.
"""
from __future__ import annotations

import glob
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

# The environment Codex gives ``http_headers_helper`` (observed 2026-11-25): these names only.
HELPER_ENV = ("APPDATA", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT", "PROGRAMDATA",
              "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROMPT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP",
              "USERDOMAIN", "USERNAME", "USERPROFILE", "WINDIR")


def _version_key(path):
    m = re.search(r"openai\.chatgpt-(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def extension_engine():
    """(path, extension version) of the newest installed Codex VS Code extension's engine, or (None, '')."""
    hits = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".vscode", "extensions", "openai.chatgpt-*", "bin",
                                         "windows-x86_64", "codex.exe")), key=_version_key)
    if not hits:
        return None, ""
    return hits[-1], ".".join(map(str, _version_key(hits[-1])))


def cli_engine():
    """The native binary behind the ``codex`` command on PATH (npm package layout), or None."""
    w = shutil.which("codex")
    if not w:
        return None
    if w.lower().endswith(".exe"):
        return w
    hits = glob.glob(os.path.join(os.path.dirname(w), "node_modules", "@openai", "codex", "node_modules", "@openai",
                                  "codex-win32-*", "vendor", "*", "bin", "codex.exe"))
    return hits[0] if hits else None


def run_helper(command, cwd):
    """Run an ``http_headers_helper`` command exactly as Codex does on Windows: ``cmd.exe /Q /D /C "<command>"`` with
    the reduced environment. Returns (exit status, parsed header object or None)."""
    env = {k: v for k, v in os.environ.items() if k.upper() in HELPER_ENV}
    r = subprocess.run('cmd.exe /Q /D /C "' + command + '"', capture_output=True, text=True, timeout=60, cwd=cwd, env=env)
    try:
        hdr = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        hdr = None
    return r.returncode, hdr


class Engine:
    def __init__(self, exe, codex_home, cwd, label):
        self.exe, self.home, self.cwd, self.label = exe, codex_home, cwd, label
        self.p, self.q, self.notes, self.err, self._id, self.tid, self.version = None, queue.Queue(), [], [], 0, None, ""

    # ---- process ------------------------------------------------------------------------------------------
    def open(self):
        env = dict(os.environ, CODEX_HOME=self.home)
        for k in ("TDFLOW_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(k, None)
        self.p = subprocess.Popen([self.exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=self.cwd, env=env)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        r = self.request("initialize", {"clientInfo": {"name": "tdflowmcp-test", "title": "TDflowMCP test",
                                                       "version": "0.0.1"}, "capabilities": {"experimentalApi": True}})
        self._send({"method": "initialized"})
        res = r.get("result") or {}
        m = re.search(r"/(\d[\w.\-]*)", str(res.get("userAgent", "")))
        self.version = m.group(1) if m else ""
        self.codex_home = res.get("codexHome", "")
        return self

    def _read(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line.decode("utf-8", "replace")))
            except ValueError:
                pass
        self.q.put(None)

    def _read_err(self):
        for line in self.p.stderr:
            self.err.append(line.decode("utf-8", "replace").rstrip())

    def _send(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def request(self, method, params=None, timeout=90):
        self._id += 1
        rid = self._id
        self._send({"id": rid, "method": method, "params": params if params is not None else {}})
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = self.q.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            if m is None:
                return {"error": {"message": "the engine exited"}}
            if m.get("id") == rid and ("result" in m or "error" in m):
                return m
            if "method" in m and "id" in m:              # a request to the client (approval, elicitation): decline
                self._send({"id": m["id"], "error": {"code": -32601, "message": "not supported by the test client"}})
            self.notes.append(m)
        return {"error": {"message": "timeout", "method": method}}

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def close(self):
        """End this engine and the helpers it started (its own process tree only: Codex starts background work such
        as a plugin catalogue download, which would otherwise outlive it and keep files in the test folder open)."""
        if self.p is None:
            return
        if self.p.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.p.pid), "/T", "/F"], capture_output=True)
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.p = None

    # ---- MCP through the engine ----------------------------------------------------------------------------
    def servers(self):
        """{server name: status row}; asking makes the engine connect to every enabled server."""
        r = self.request("mcpServerStatus/list", {})
        return {x.get("name"): x for x in ((r.get("result") or {}).get("data") or [])}

    def thread(self):
        if not self.tid:
            r = self.request("thread/start", {"cwd": self.cwd})
            self.tid = ((r.get("result") or {}).get("thread") or {}).get("id")
        return self.tid

    def call(self, tool, args=None, server="tdflow"):
        """Call one MCP tool through the engine. Returns (answered, structured result or error text)."""
        r = self.request("mcpServer/tool/call", {"server": server, "threadId": self.thread(), "tool": tool,
                                                 "arguments": args or {}})
        if "result" not in r:
            return False, str((r.get("error") or {}).get("message", ""))[-300:]
        res = r["result"]
        out = dict(res.get("structuredContent") or {})
        out["_isError"] = res.get("isError")
        return True, out

    def output(self):
        """Everything the engine printed or sent that a token could leak into."""
        return "\n".join(self.err) + "\n" + json.dumps(self.notes)
