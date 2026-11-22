"""MCP protocol layer (ADR-0003): JSON-RPC 2.0 over Streamable HTTP POST. Pure Python, no `td`.

Two eras are served by one endpoint:
- **2026-07-28 (stateless):** every request carries ``_meta`` ``io.modelcontextprotocol/protocolVersion``
  and ``…/clientCapabilities``; headers ``MCP-Protocol-Version``, ``Mcp-Method`` (and ``Mcp-Name`` for
  ``tools/call``) must match the body (400, ``-32020``). Unsupported version → 400 ``-32022``.
  ``server/discover`` is implemented. Unknown method → 404 ``-32601``.
- **Legacy 2025-11-25 / 2025-06-18 / 2025-03-26:** ``initialize`` + ``notifications/initialized``;
  ``MCP-Protocol-Version`` header after initialisation (absent = 2025-03-26).
Notifications are answered ``202 Accepted`` with no body. Responses are ``application/json``.
Batches (JSON arrays) are rejected. No session ids are minted or required.

``handle`` receives the already-authenticated request (the transport authenticated the bearer
token) and a ``ToolHost`` that lists and calls tools for that caller.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from tdflowmcp import __version__

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED = (MODERN,) + LEGACY
K_VERSION = "io.modelcontextprotocol/protocolVersion"
K_CAPS = "io.modelcontextprotocol/clientCapabilities"
K_INFO = "io.modelcontextprotocol/clientInfo"
K_SERVER = "io.modelcontextprotocol/serverInfo"
SERVER_INFO = {"name": "TDflowMCP", "version": __version__}
INSTRUCTIONS = ("TDflowMCP controls a TouchDesigner project with server-enforced permissions. Call tdflow_whoami "
                "first to see your scopes. Every change is audited; use tdflow_undo to revert your last change.")

Reply = Tuple[int, Optional[bytes], Dict[str, str]]


def _dump(obj) -> bytes:
    return (json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _err(status: int, mid, code: int, message: str, data=None) -> Reply:
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return status, _dump({"jsonrpc": "2.0", "id": mid, "error": e}), {}


class ToolHost:
    """Implemented by the TD adapter for one authenticated caller."""

    def list_tools(self) -> list:  # pragma: no cover - interface
        raise NotImplementedError

    def call_tool(self, name: str, arguments: dict, client_info: Optional[dict]) -> dict:  # pragma: no cover
        raise NotImplementedError


def handle(body: bytes, headers: Dict[str, str], host: ToolHost) -> Reply:
    h = {k.lower(): v for k, v in (headers or {}).items()}
    try:
        msg = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _err(400, None, -32700, "Parse error")
    if isinstance(msg, list):
        return _err(400, None, -32600, "Batch requests are not supported")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _err(400, msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, params = msg["method"], msg.get("params")
    is_notification = "id" not in msg
    mid = msg.get("id")
    if mid is not None and not isinstance(mid, (str, int)) or isinstance(mid, bool):
        return _err(400, None, -32600, "Invalid Request id")
    if params is not None and not isinstance(params, dict):
        return _err(400, mid, -32602, "params must be an object")
    params = params or {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    hv = h.get("mcp-protocol-version")

    if is_notification:
        return 202, None, {}

    if method == "initialize":                                   # legacy era handshake
        req = params.get("protocolVersion")
        pv = req if req in LEGACY else LEGACY[0]
        return 200, _dump({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}}), {}

    modern = K_VERSION in meta
    client_info = None
    if modern:
        pv = meta.get(K_VERSION)
        if pv != MODERN:
            return _err(400, mid, -32022, "Unsupported protocol version",
                        {"supported": list(SUPPORTED), "requested": pv})
        if hv is None or hv != pv:
            return _err(400, mid, -32020, "MCP-Protocol-Version header does not match _meta")
        if h.get("mcp-method") != method:
            return _err(400, mid, -32020, "Mcp-Method header does not match the request method")
        if method == "tools/call" and h.get("mcp-name") != params.get("name"):
            return _err(400, mid, -32020, "Mcp-Name header does not match the tool name")
        if not isinstance(meta.get(K_CAPS), dict):
            return _err(400, mid, -32602, f"Missing required _meta field {K_CAPS}")
        client_info = meta.get(K_INFO) if isinstance(meta.get(K_INFO), dict) else None
    else:
        if hv == MODERN:
            return _err(400, mid, -32602, f"Missing required _meta field {K_VERSION}")
        if hv is not None and hv not in LEGACY:
            return _err(400, mid, -32022, "Unsupported protocol version",
                        {"supported": list(SUPPORTED), "requested": hv})

    def ok(result: dict) -> Reply:
        if modern:
            result = dict(result)
            result.setdefault("resultType", "complete")
            result["_meta"] = {K_SERVER: SERVER_INFO}
        return 200, _dump({"jsonrpc": "2.0", "id": mid, "result": result}), {}

    if method == "ping":
        return ok({})
    if method == "server/discover":
        return ok({"supportedVersions": list(SUPPORTED), "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "tools/list":
        return ok({"tools": host.list_tools()})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str):
            return _err(400 if modern else 200, mid, -32602, "params.name must be a string")
        if not isinstance(args, dict):
            return _err(400 if modern else 200, mid, -32602, "params.arguments must be an object")
        result = host.call_tool(name, args, client_info)
        if result is None:
            return _err(400 if modern else 200, mid, -32602, f"Unknown tool: {name}")
        return ok(result)
    return _err(404 if modern else 200, mid, -32601, "Method not found")


def tool_result(structured: dict, is_error: bool = False) -> dict:
    """Tool result with both content (JSON text) and structuredContent (tool-surface conventions)."""
    return {"content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, default=str)}],
            "structuredContent": structured, "isError": bool(is_error)}


def tool_error(code: str, message: str, **extra) -> dict:
    return tool_result(dict({"code": code, "message": message}, **extra), is_error=True)
