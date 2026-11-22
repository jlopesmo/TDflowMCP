"""S5 spike helper: read the OS TCP listener table for a given PID/port (Windows, stdlib ctypes only).

Disposable spike code — not TDflowMCP source.
"""
import ctypes
import ctypes.wintypes as wt
import socket
import struct

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_LISTENER = 3


def _table(family):
    iphlp = ctypes.windll.iphlpapi
    size = wt.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
    buf = ctypes.create_string_buffer(size.value + 1024)
    size = wt.DWORD(len(buf))
    rc = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
    if rc != 0:
        raise OSError(f"GetExtendedTcpTable rc={rc}")
    n = struct.unpack_from("<I", buf, 0)[0]
    rows = []
    if family == AF_INET:
        for i in range(n):
            _st, la, lp, _ra, _rp, pid = struct.unpack_from("<IIIIII", buf, 4 + i * 24)
            rows.append((socket.inet_ntoa(struct.pack("<I", la)), socket.ntohs(lp & 0xFFFF), pid))
    else:
        for i in range(n):
            off = 4 + i * 56
            la = buf.raw[off:off + 16]
            lp = struct.unpack_from("<I", buf, off + 20)[0]
            pid = struct.unpack_from("<I", buf, off + 52)[0]
            rows.append((socket.inet_ntop(socket.AF_INET6, la), socket.ntohs(lp & 0xFFFF), pid))
    return rows


def listeners(port, pid=None):
    """Return {'v4': [(addr, port, pid)], 'v6': [...]} for listeners on `port` (optionally one pid)."""
    out = {}
    for name, fam in (("v4", AF_INET), ("v6", AF_INET6)):
        out[name] = [r for r in _table(fam) if r[1] == port and (pid is None or r[2] == pid)]
    return out


def local_non_loopback_ipv4():
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = info[4][0]
            if not a.startswith("127."):
                addrs.add(a)
    except OSError:
        pass
    return sorted(addrs)


def verdict(port, pid, probe_timeout=0.2):
    """Bind-verification verdict per security-model §8.2: 'pass' | 'fail' | 'ambiguous' with evidence."""
    ev = {"port": port, "pid": pid}
    try:
        tab = listeners(port, pid)
        ev["table"] = tab
    except Exception as e:  # table unavailable -> ambiguous
        ev["table_error"] = repr(e)
        return "ambiguous", ev
    v4 = [a for a, _, _ in tab["v4"]]
    v6 = [a for a, _, _ in tab["v6"]]
    probes = {}
    for a in local_non_loopback_ipv4():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(probe_timeout)
        try:
            s.connect((a, port))
            probes[a] = "connected"
        except ConnectionRefusedError:
            probes[a] = "refused"
        except socket.timeout:
            probes[a] = "timeout"
        except OSError as e:
            probes[a] = f"error:{e.errno}"
        finally:
            s.close()
    ev["probes"] = probes
    if not v4 and not v6:
        return "ambiguous", ev  # nothing listening (yet) for this pid -> cannot verify
    bad = [a for a in v4 if a != "127.0.0.1"] + [a for a in v6 if a != "::1"]
    if bad or any(r == "connected" for r in probes.values()):
        ev["bad"] = bad
        return "fail", ev
    return "pass", ev
