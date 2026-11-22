"""Bind-verification evidence collection (security-model §8.2). Pure sockets + OS table; no `td`.

Safe to run off TouchDesigner's main thread: it only reads the OS listener table and opens short
outbound TCP connections to this machine's own addresses. Only the verdict is applied on the main
thread (by the controller).
"""
from __future__ import annotations

import http.client
import json
import socket
from typing import List, Optional

from tdflowmcp.net import bindverify, oslisteners


def lan_ipv4() -> List[str]:
    """This machine's non-loopback IPv4 addresses (never reported to users or logs)."""
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = info[4][0]
            if not a.startswith("127."):
                out.add(a)
    except OSError:
        pass
    return sorted(out)


def probe(addr: str, port: int, timeout: float = 0.3) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((addr, port))
        return "connected"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


def collect(port: int, pid: int, sockname_host: Optional[str]) -> bindverify.Evidence:
    """Positive evidence for a loopback-only binding of ``pid`` on ``port`` (verdict only, no side effects)."""
    tab = oslisteners.read_listeners(port, pid)
    others = oslisteners.other_listeners(port, pid)
    probes = {a: probe(a, port) for a in lan_ipv4()}
    v4, v6 = tab if tab is not None else (None, None)
    return bindverify.verdict(v4, v6, probes, sockname=sockname_host, others=others)


def responds_like_tdflowmcp(port: int, timeout: float = 0.2) -> Optional[str]:
    """Loopback port-ownership probe (security-model §8: untrusted, display only). Returns the claimed
    version if the listener answers like TDflowMCP, else None. Never drives an automatic action."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/.well-known/tdflowmcp", headers={"Host": f"127.0.0.1:{port}"})
        r = conn.getresponse()
        if r.status != 200:
            return None
        obj = json.loads(r.read(4096) or b"{}")
        if isinstance(obj, dict) and obj.get("product") == "TDflowMCP":
            v = str(obj.get("version", "?"))
            return "".join(ch for ch in v if ch.isalnum() or ch in ".-+")[:32] or "?"
        return None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()
