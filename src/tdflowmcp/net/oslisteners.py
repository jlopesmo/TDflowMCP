"""OS listening-socket table (evidence source for the bind-verification gate).

Parsing is pure and testable on any OS. Reading uses Windows ``GetExtendedTcpTable`` via ctypes
(stdlib only); on other platforms ``read_listeners`` returns None (-> verdict 'ambiguous').
Validated against ``netstat`` in spike S5.
"""
from __future__ import annotations

import socket
import struct
import sys
from typing import List, Optional, Tuple

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_LISTENER = 3
Row = Tuple[str, int, int]  # (address, port, pid)


def parse_ipv4_table(buf: bytes) -> List[Row]:
    """MIB_TCPTABLE_OWNER_PID: DWORD count, then rows of 6 DWORDs (state, laddr, lport, raddr, rport, pid)."""
    n = struct.unpack_from("<I", buf, 0)[0]
    rows = []
    for i in range(n):
        _state, laddr, lport, _ra, _rp, pid = struct.unpack_from("<IIIIII", buf, 4 + i * 24)
        rows.append((socket.inet_ntoa(struct.pack("<I", laddr)), socket.ntohs(lport & 0xFFFF), pid))
    return rows


def parse_ipv6_table(buf: bytes) -> List[Row]:
    """MIB_TCP6TABLE_OWNER_PID: DWORD count, then 56-byte rows
    (laddr[16], lscope, lport, raddr[16], rscope, rport, state, pid)."""
    n = struct.unpack_from("<I", buf, 0)[0]
    rows = []
    for i in range(n):
        off = 4 + i * 56
        laddr = bytes(buf[off:off + 16])
        lport = struct.unpack_from("<I", buf, off + 20)[0]
        pid = struct.unpack_from("<I", buf, off + 52)[0]
        rows.append((socket.inet_ntop(socket.AF_INET6, laddr), socket.ntohs(lport & 0xFFFF), pid))
    return rows


def _read_raw(family: int) -> bytes:  # pragma: no cover - Windows only, exercised in spikes/in-TD tests
    import ctypes
    import ctypes.wintypes as wt
    iphlp = ctypes.windll.iphlpapi
    size = wt.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
    buf = ctypes.create_string_buffer(size.value + 4096)
    size = wt.DWORD(len(buf))
    rc = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
    if rc != 0:
        raise OSError(f"GetExtendedTcpTable rc={rc}")
    return buf.raw


def read_listeners(port: int, pid: int) -> Optional[Tuple[List[str], List[str]]]:
    """Return (v4_addresses, v6_addresses) that process ``pid`` listens on for ``port``, or None if unavailable.

    ``pid`` is required: another process's loopback listener on the same port must never make
    TouchDesigner's own binding look loopback-only (review 3, M8).
    """
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid is required")
    if sys.platform != "win32":
        return None
    try:
        v4 = [a for a, p, owner in parse_ipv4_table(_read_raw(AF_INET)) if p == port and owner == pid]
        v6 = [a for a, p, owner in parse_ipv6_table(_read_raw(AF_INET6)) if p == port and owner == pid]
    except Exception:
        return None
    return v4, v6


def other_listeners(port: int, own_pid: int) -> Optional[List[Row]]:
    """Listeners on ``port`` owned by *other* processes (port-conflict evidence), or None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        rows = parse_ipv4_table(_read_raw(AF_INET)) + parse_ipv6_table(_read_raw(AF_INET6))
    except Exception:
        return None
    return [r for r in rows if r[1] == port and r[2] != own_pid]
