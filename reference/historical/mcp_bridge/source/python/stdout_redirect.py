import sys
import io
import datetime


class _TDStdout(io.TextIOBase):
    def __init__(self, log_op_path, original):
        self._log      = log_op_path
        self._original = original

    def write(self, msg):
        if self._original:
            self._original.write(msg)
            self._original.flush()
        stripped = msg.rstrip('\n')
        if stripped:
            table = op(self._log)
            if table:
                ts = datetime.datetime.now().strftime('%H:%M:%S')
                table.appendRow([ts, stripped])
        return len(msg)

    def flush(self):
        if self._original:
            self._original.flush()


def enable():
    if not isinstance(sys.stdout, _TDStdout):
        original   = sys.__stdout__ if sys.__stdout__ else None
        sys.stdout = _TDStdout('/mcp_bridge/textport_log', original)
        op('/mcp_bridge/textport_log').appendRow([
            datetime.datetime.now().strftime('%H:%M:%S'),
            '[MCP] stdout redirect enabled'
        ])


def disable():
    if isinstance(sys.stdout, _TDStdout):
        orig       = sys.stdout._original
        sys.stdout = orig if orig else sys.__stdout__
