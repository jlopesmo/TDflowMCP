import socket
import struct
import sys
import unittest

from tests.unit import SRC  # noqa: F401
from tdflowmcp.net import oslisteners


def _v4_row(addr, port, pid, state=2):
    la = struct.unpack("<I", socket.inet_aton(addr))[0]
    return struct.pack("<IIIIII", state, la, socket.htons(port), 0, 0, pid)


def _v6_row(addr, port, pid):
    return (socket.inet_pton(socket.AF_INET6, addr) + struct.pack("<II", 0, socket.htons(port))
            + b"\0" * 16 + struct.pack("<IIII", 0, 0, 2, pid))


class ParseTests(unittest.TestCase):
    def test_parse_ipv4(self):
        rows = [_v4_row("127.0.0.1", 9480, 11), _v4_row("0.0.0.0", 135, 22)]
        buf = struct.pack("<I", len(rows)) + b"".join(rows)
        self.assertEqual(oslisteners.parse_ipv4_table(buf), [("127.0.0.1", 9480, 11), ("0.0.0.0", 135, 22)])

    def test_parse_ipv6(self):
        rows = [_v6_row("::1", 9480, 11), _v6_row("::", 135, 22)]
        buf = struct.pack("<I", len(rows)) + b"".join(rows)
        self.assertEqual(oslisteners.parse_ipv6_table(buf), [("::1", 9480, 11), ("::", 135, 22)])

    @unittest.skipUnless(sys.platform == "win32", "Windows listener table")
    def test_reads_own_loopback_listener(self):
        import os
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            res = oslisteners.read_listeners(port, os.getpid())
            self.assertEqual(res, (["127.0.0.1"], []))
        finally:
            s.close()

    @unittest.skipIf(sys.platform == "win32", "non-Windows behaviour")
    def test_unavailable_elsewhere(self):
        self.assertIsNone(oslisteners.read_listeners(9480, 1234))

    def test_pid_is_required(self):
        for bad in (None, 0, -1, "1"):
            with self.assertRaises(ValueError):
                oslisteners.read_listeners(9480, bad)

    @unittest.skipUnless(sys.platform == "win32", "Windows listener table")
    def test_other_process_listener_is_not_ours(self):
        import os
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            self.assertEqual(oslisteners.read_listeners(port, os.getpid() + 1_000_000), ([], []))
            others = oslisteners.other_listeners(port, os.getpid() + 1_000_000)
            self.assertIn(("127.0.0.1", port, os.getpid()), others)
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
