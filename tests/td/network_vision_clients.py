"""Exercise installed clients using only the disposable network fixture."""

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import secrets
import threading
import time
from unittest.mock import patch

import codex_engine
import run_td_diagnostic_tests as diagnostic
import run_td_gate_tests as gate
import run_td_mcp_tests as mcp
from tdflowmcp import protocol
from tdflowmcp.tools import core_registry


CAPTURE = "tdflow_capture_network"
GET = "tdflow_get_operator"
CONNECT = "tdflow_connect"
DISCONNECT = "tdflow_disconnect"


class ImageOnly(protocol.ToolHost):
    def __init__(self, path, png):
        self.path = path
        self.attachment = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode("ascii")}
        self.calls = 0

    def list_tools(self):
        return [core_registry().get(CAPTURE).describe()]

    def call_tool(self, name, arguments, client_info, meta=None):
        if name != CAPTURE or arguments != {"path": self.path}:
            return None
        self.calls += 1
        return {"content": [self.attachment], "isError": False}


@contextmanager
def image_only_endpoint(host):
    """Serve one synthetic artifact with no textual layout, so model inference requires the PNG."""
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != "/mcp" or self.headers.get("Authorization") != "Bearer " + token:
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error(400)
                return
            if not 0 <= size <= 65536:
                self.send_error(413)
                return
            status, body, _ = protocol.handle(self.rfile.read(size), dict(self.headers.items()), host)
            body = body or b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/mcp", token
        finally:
            server.shutdown()
            thread.join(5)


def claude_interpretation(project, path, png):
    image = ImageOnly(path, png)
    prompt = (f"Call {CAPTURE} exactly once at {path}. Its only result is an attached PNG from a disposable "
              "fixture. Examine the PNG. What direction does the arrow between markers 1 and 2 point, left "
              "or right? Is marker 3 above or below marker 4? What are the fill colors of markers 1 and 5? "
              "Use common color names. If you cannot see the attached image, say NO_IMAGE. Otherwise reply as "
              "VISION: direction, above/below, first color, second color. In a second sentence suggest one "
              "layout improvement based solely on the image.")
    with image_only_endpoint(image) as (endpoint, token):
        ok, uses, answer, seconds = diagnostic.run_live(project, token, prompt, [CAPTURE], endpoint=endpoint)
    normalized = " ".join(re.findall(r"[a-z]+", answer.lower()))
    parsed = re.search(r"vision left below (?:(?:light|pale) )?(?:purple|lavender) (?:and )?"
                       r"(?:(?:light|pale) )?(?:green|mint)", normalized)
    tool_calls = [part for part in uses if part.get("name") == "mcp__tdflow__" + CAPTURE]
    verified = ok and image.calls == 1 and len(tool_calls) == 1 and bool(parsed)
    gate.T("vision.claude_interprets_image_without_text_facts", verified, answer=answer,
           forwarded_images=image.calls, diagram_calls=len(tool_calls), seconds=seconds)
    return {"client": "Claude Code", "model_observation": answer, "seconds": seconds, "image_only": True,
            "verified": verified}


def scoped_token(path, role, name):
    response = mcp.admin("POST", "/setup/api/tokens", {"display_name": name, "role": role, "scope": path + "/**",
                                                        "actions": ["read"] if role == "observer" else ["read", "update"]})
    token = (mcp.base.jbody(response) or {}).get("token")
    if response["status"] != 200 or not token:
        raise RuntimeError("The disposable client token could not be created.")
    return token


def codex_delivery(work, project, path):
    token = scoped_token(path, "observer", "Codex network diagram observer")
    evidence = {}
    for label, executable in (("cli", codex_engine.cli_engine()), ("vscode", codex_engine.extension_engine()[0])):
        if not executable:
            gate.T(f"vision.codex_{label}_installed", False)
            continue
        home = Path(work) / f"codex-{label}"
        home.mkdir()
        (home / "config.toml").write_text(
            "[mcp_servers.tdflow]\n" + f'url = "{mcp.O}/mcp"\n'
            + 'bearer_token_env_var = "TDFLOW_VISION_TEST_TOKEN"\n', encoding="utf-8")
        engine = codex_engine.Engine(executable, str(home), str(project), label)
        try:
            with patch.dict(os.environ, {"TDFLOW_VISION_TEST_TOKEN": token}):
                engine.open()
            catalog = engine.servers().get("tdflow", {}).get("tools", {})
            reply = engine.request("mcpServer/tool/call", {"server": "tdflow", "threadId": engine.thread(),
                                                          "tool": CAPTURE, "arguments": {"path": path}})
            result = reply.get("result") or {}
            pngs = [part for part in result.get("content", []) if part.get("type") == "image"]
            data = result.get("structuredContent") or {}
            valid = (CAPTURE in catalog and len(pngs) == 1 and pngs[0].get("mimeType") == "image/png"
                     and base64.b64decode(pngs[0].get("data", ""))[:8] == b"\x89PNG\r\n\x1a\n"
                     and data.get("representation") == "rendered_network_diagram")
            gate.T(f"vision.codex_{label}_receives_png_and_facts", valid, engine=engine.version)
            evidence[label] = {"engine": engine.version, "image_and_facts": valid}
        finally:
            engine.close()
        time.sleep(1.05)
    return evidence


def claude_repair(project, path):
    token = scoped_token(path, "builder", "Claude network diagram repair fixture")
    prompt = (f"Use {CAPTURE} on disposable fixture {path}. Describe one visually confusing wire. "
              f"Use {GET} with connectors=true on dd_output to verify the current input. The intended chain is "
              "aa_source to bb_stage to dd_output. Help correct the one wrong wire into dd_output input 0. "
              f"If it comes from cc_wrong, use {DISCONNECT} for that wire and {CONNECT} from bb_stage output 0 "
              f"to dd_output input 0. Make no other changes. Finish by calling {GET} with connectors=true on "
              "dd_output to verify the exact repair. Report the verified source path.")
    ok, calls, answer, seconds = diagnostic.run_live(project, token, prompt, [CAPTURE, GET, CONNECT, DISCONNECT])
    uses = [(part.get("name", "").removeprefix("mcp__tdflow__"), part.get("input") or {}) for part in calls]
    edits = [(method, args.get("src"), args.get("dst"), args.get("src_index", 0), args.get("dst_index", 0))
             for method, args in uses if method in (CONNECT, DISCONNECT)]
    destination = path + "/dd_output"
    expected = [(DISCONNECT, path + "/cc_wrong", destination, 0, 0), (CONNECT, path + "/bb_stage", destination, 0, 0)]
    markers = [method for method, args in uses if method == CAPTURE or
               (method == GET and args.get("path") == destination and args.get("connectors") is True)
               or method in (CONNECT, DISCONNECT)]
    verified = False
    if ok and edits == expected:
        start, end = markers.index(DISCONNECT), markers.index(CONNECT)
        before = markers[:start]
        verified = CAPTURE in before and GET in before[before.index(CAPTURE) + 1:] and GET in markers[end + 1:]
    gate.T("vision.claude_uses_diagram_and_native_facts_then_repairs", verified, calls=markers, seconds=seconds)
    return {"verified": verified, "calls": markers, "response": answer, "seconds": seconds}
