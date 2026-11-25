"""Consent-based coding-agent connection (owner D22, ADR-0015). Pure Python, no `td`.

What this module does, and only after the user approved the exact plan inside TouchDesigner:
- **Discovery** (read-only): looks names up on PATH (``shutil.which``) and lists known extension and configuration
  folders. It never runs a discovered program, never reads a client's configuration during discovery and never
  reports paths.
- **Claude Code** (CLI, the VS Code extension and other local interfaces share ``~/.claude.json``): one user-scope
  ``mcpServers.tdflow`` entry with a ``headersHelper``. The helper is a fixed command (Windows PowerShell or
  ``/bin/sh``) that asks the local listener which project it serves (``/.well-known/tdflowmcp``, ``connect``) and reads
  that project's token from the OS-protected vault. The token never appears in the configuration file, a command
  line, an environment variable, a parameter, a DAT or a log.
- **Vault:** Windows: DPAPI (current user) files under the private per-user root, in the format PowerShell's
  ``ConvertTo-SecureString`` reads. macOS: the login Keychain through ``security -i`` with the command on stdin (so
  not on a command line). Other platforms: unsupported (manual fallback).
- **Change discipline:** the plan is computed from the file as it is; the change is one key; other servers and
  settings are preserved; a backup is written to private storage first; the write is atomic and re-read to validate;
  undo removes only an entry that is still TDflowMCP's (and restores an entry it replaced, from the backup).

- **OpenAI Codex** (ADR-0017; the CLI, the IDE extension and the desktop app share ``~/.codex/config.toml``): one
  ``[mcp_servers.tdflow]`` table with ``url`` and ``http_headers_helper``, on Windows. The helper is the same fixed
  PowerShell command and the same vault and listener proof; on any failure it exits non-zero and prints nothing
  (observed 2026-11-25: Codex then sends no request at all, whereas an empty header object makes it connect without
  credentials to whatever listens on the port). The file is edited as text: only TDflowMCP's own table is added,
  updated or removed, every other byte is kept, and the result is re-parsed and compared before it is written.

Other clients (Cursor, VS Code Copilot; Codex on other platforms) have no helper mechanism TDflowMCP can use: their
documented credential options are an environment variable present when the client starts, or OAuth (not implemented
by TDflowMCP). They keep the short manual fallback; the reason per client is in the registry (``manual_reason``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

SERVER_NAME = "tdflow"
VAULT_SERVICE = "TDflowMCP"
HELPER_MARK = "TDflowMCP"                      # every helper command we write contains this (ownership test)
PROJECT_RE = re.compile(r"^prj_[A-Za-z0-9]{16}\Z")
TOKEN_RE = re.compile(r"^tdf_[A-Za-z0-9]{12}\.[A-Za-z0-9_\-]{43,128}$")
KEY_RE = re.compile(r"^[0-9a-f]{64}\Z")
NONCE_RE = re.compile(r"^[0-9a-f]{32}\Z")
PROOF_PREFIX = "/.well-known/tdflowmcp-proof/"
SLOT_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}\Z")
MAX_CONFIG_BYTES = 32 * 1024 * 1024
KEEP_BACKUPS = 5

AUTOMATIC = ("claude-code", "codex")          # clients whose configuration and credentials can be set up safely
PLATFORMS = {"claude-code": ("win32", "darwin"), "codex": ("win32",)}   # where automatic setup exists per client
# Oldest Codex VS Code extension build observed to run ``http_headers_helper`` (its engine: codex-cli
# 0.171.0-alpha.13, 2026-11-25). The folder name carries the version, so discovery stays read-only.
CODEX_EXT_MIN = (26, 1117)


class ConnectError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- environment (injectable for tests) ------------------------------------------------------------------------
@dataclass
class Env:
    platform: str = sys.platform
    home: str = os.path.expanduser("~")
    environ: Dict[str, str] = field(default_factory=lambda: dict(os.environ))
    which: Callable[[str], Optional[str]] = None
    listdir: Callable[[str], List[str]] = None
    isdir: Callable[[str], bool] = os.path.isdir
    isfile: Callable[[str], bool] = os.path.isfile

    def __post_init__(self):
        if self.which is None:
            import shutil
            self.which = shutil.which
        if self.listdir is None:
            def _ls(p):
                try:
                    return os.listdir(p)
                except OSError:
                    return []
            self.listdir = _ls

    @staticmethod
    def current(home_override: str = "") -> "Env":
        """The real environment. ``home_override`` (tests: ``TDFLOW_CLIENT_HOME``) replaces the user's home for client
        files, so in-TD tests never touch the real client configuration."""
        e = Env()
        if home_override:
            e.home = home_override
            e.environ = {k: v for k, v in e.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        return e


# ---- discovery ---------------------------------------------------------------------------------------------------
def _ext_dirs(env: Env, prefixes: Sequence[str]) -> List[str]:
    """Names of installed editor extensions starting with one of ``prefixes`` (VS Code, Insiders, Cursor, Windsurf
    hosts). Folder names only; nothing is opened."""
    hits = []
    for host in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        for name in env.listdir(os.path.join(env.home, host, "extensions")):
            low = name.lower()
            if any(low.startswith(p) for p in prefixes):
                hits.append(host)
                break
    return hits


HOST_LABELS = {".vscode": "VS Code", ".vscode-insiders": "VS Code Insiders", ".cursor": "Cursor",
               ".windsurf": "Windsurf"}


def _ext_versions(env: Env, prefix: str) -> Dict[str, List[tuple]]:
    """{editor host: [(major, minor), ...]} for installed extensions whose folder name starts with ``prefix``. Read
    from folder names only (``openai.chatgpt-26.1120.50957-win32-x64``); an unreadable version counts as (0, 0)."""
    out: Dict[str, List[tuple]] = {}
    for host in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        for name in env.listdir(os.path.join(env.home, host, "extensions")):
            low = name.lower()
            if low.startswith(prefix):
                m = re.match(r"(\d{1,6})\.(\d{1,6})", low[len(prefix):])
                out.setdefault(host, []).append((int(m.group(1)), int(m.group(2))) if m else (0, 0))
    return out


def discover(env: Env) -> List[Dict]:
    """Read-only discovery. One row per client with the interfaces found (labels only, no paths) and whether
    automatic setup is available on this platform. Never runs anything."""
    out = []
    # Claude Code: the CLI and every editor host of the extension share one configuration.
    ifaces = []
    if env.which("claude"):
        ifaces.append("CLI")
    for host in _ext_dirs(env, ("anthropic.claude-code-",)):
        ifaces.append(HOST_LABELS[host] + " extension")
    if ifaces or env.isdir(os.path.join(env.home, ".claude")):
        out.append({"client": "claude-code", "interfaces": ifaces or ["configuration folder"],
                    "automatic": env.platform in ("win32", "darwin")})
    # Codex: the CLI, the IDE extension and the desktop app share one configuration (~/.codex/config.toml). The
    # desktop app has no install marker that was verified, so it is not listed separately.
    has_cli = bool(env.which("codex"))
    ext = _ext_versions(env, "openai.chatgpt-")
    ifaces = (["CLI"] if has_cli else []) + [HOST_LABELS[h] + " extension" for h in ext]
    if ifaces or env.isdir(os.path.join(env.home, ".codex")):
        newest = max((v for vs in ext.values() for v in vs), default=None)
        too_old = bool(ext) and not has_cli and newest is not None and newest < CODEX_EXT_MIN
        row = {"client": "codex", "interfaces": ifaces or ["configuration folder"],
               "automatic": env.platform in PLATFORMS["codex"] and not too_old}
        if too_old:
            row["manual_why"] = "this Codex extension is older than the first version tested with the header helper"
        out.append(row)
    if env.which("cursor") or env.isdir(os.path.join(env.home, ".cursor")):
        out.append({"client": "cursor", "interfaces": ["editor"], "automatic": False})
    if ".vscode" in _ext_dirs(env, ("github.copilot",)):
        out.append({"client": "vscode-copilot", "interfaces": ["VS Code extension"], "automatic": False})
    return out


# ---- Claude Code configuration ----------------------------------------------------------------------------------
def claude_config_path(env: Env) -> str:
    d = env.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(d, ".claude.json") if d else os.path.join(env.home, ".claude.json")


def display_path(path: str, env: Env) -> str:
    """User-facing form without the user name (``~/.claude.json``, ``~/.codex/config.toml``)."""
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        d = env.environ.get(var)
        if d and os.path.normcase(os.path.dirname(path)) == os.path.normcase(d.rstrip("\\/")):
            return f"%{var}%/" + os.path.basename(path) if env.platform == "win32" else \
                f"${var}/" + os.path.basename(path)
    home = os.path.normcase(env.home.rstrip("\\/"))
    if os.path.normcase(path).startswith(home + os.sep) or os.path.normcase(path).startswith(home + "/"):
        return "~/" + path[len(env.home.rstrip("\\/")) + 1:].replace("\\", "/")
    return os.path.basename(path)


def proof(key_hex: str, pid: str, port: int, nonce: str) -> str:
    """The listener's answer to the helper's challenge (review 19, finding 1): HMAC-SHA256 with the project's connect
    key over ``tdflowmcp-proof|<project id>|<port>|<nonce>``. Only the TouchDesigner instance that holds the key can
    answer, so a process squatting the port cannot make the helper release any project's token."""
    if not (KEY_RE.match(key_hex or "") and PROJECT_RE.match(pid or "") and NONCE_RE.match(nonce or "")):
        raise ConnectError("BAD_PROOF_INPUT", "invalid proof input")
    msg = f"tdflowmcp-proof|{pid}|{int(port)}|{nonce}".encode("utf-8")
    return hmac.new(bytes.fromhex(key_hex), msg, hashlib.sha256).hexdigest()


_UNSAFE_PATH = set('%"!^\r\n\u2018\u2019\u201a\u201b')     # cmd.exe expansion, quoting, PowerShell quote look-alikes


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def helper_command(platform: str, port: int, slot: str, vault_dir: str, fail: str = "empty") -> str:
    """The fixed ``headersHelper`` command. It prints ``{"Authorization": "Bearer <token>"}`` only for the project
    that the listener on ``port`` announces **and proves** (an HMAC over a fresh nonce with that project's connect key).
    The token travels only on the helper's stdout to the client.

    ``fail``: what happens otherwise. ``"empty"`` (Claude Code): it prints ``{}`` and Claude Code reports a failed
    connection. ``"exit"`` (Codex, Windows only): it prints nothing and exits with status 1, because Codex treats an
    empty object as "no extra headers" and would then talk to the port without credentials."""
    if not SLOT_RE.match(slot):
        raise ConnectError("BAD_SLOT", "invalid credential slot")
    if fail not in ("empty", "exit") or (fail == "exit" and platform != "win32"):
        raise ConnectError("PLATFORM_UNSUPPORTED", "this helper variant is available on Windows only")
    port = int(port)
    if platform == "win32":
        # Claude Code runs helpers through cmd.exe on Windows (observed 2026-11-24, CLI 2.1.289 and the VS Code
        # extension's 2.1.330). So: no double quotes inside the script and no '%' except %SystemRoot%; PowerShell is
        # addressed by its absolute path (cmd.exe searches the working directory first). -Command text is not subject
        # to the script execution policy. Review 19: PSModulePath is pinned to Windows PowerShell's own modules (a
        # PowerShell 7 parent breaks autoloading otherwise); the result is written with [Console]::Out, which bypasses
        # transcription and module logging. The HMAC check needs .NET, so Constrained Language Mode fails closed.
        if any(c in _UNSAFE_PATH for c in vault_dir) or not os.path.isabs(vault_dir):
            raise ConnectError("BAD_VAULT_PATH", "the private folder's path cannot be used in the helper")
        base = f"http://127.0.0.1:{port}/.well-known/"
        script = (
            "$ErrorActionPreference='Stop';$env:PSModulePath=$PSHOME+'\\Modules';$q=[char]34;try{"
            f"$w=Invoke-RestMethod -UseBasicParsing -TimeoutSec 3 -Uri '{base}tdflowmcp';"
            "$p=[string]$w.connect;if($p -cnotmatch '^prj_[A-Za-z0-9]{16}$'){throw 'project'};"
            f"$f=Join-Path {_ps_quote(os.path.join(vault_dir, slot))} ($p+'.dpapi');"
            "$s=Get-Content -LiteralPath $f -Raw|ConvertTo-SecureString;"
            "$v=(New-Object System.Management.Automation.PSCredential('t',$s)).GetNetworkCredential().Password;"
            "$t,$k=$v.Split('|');"
            "if($t -cnotmatch '^tdf_[A-Za-z0-9]{12}[.][A-Za-z0-9_-]{43,128}$' -or $k -cnotmatch '^[0-9a-f]{64}$')"
            "{throw 'secret'};"
            "$n=[Guid]::NewGuid().ToString('N');"
            f"$r=Invoke-RestMethod -UseBasicParsing -TimeoutSec 3 -Uri ('{base}tdflowmcp-proof/'+$n);"
            "$b=[byte[]]::new(32);for($i=0;$i -lt 32;$i++){$b[$i]=[Convert]::ToByte($k.Substring(2*$i,2),16)};"
            "$h=New-Object System.Security.Cryptography.HMACSHA256(,$b);"
            f"$e=[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes('tdflowmcp-proof|'+$p+'|{port}|'+$n)))"
            ".Replace('-','').ToLower();"
            "if([string]$r.proof -cne $e){throw 'proof'};"
            "[Console]::Out.Write('{'+$q+'Authorization'+$q+':'+$q+'Bearer '+$t+$q+'}')}"
            + ("catch{[Console]::Out.Write('{}')} # " if fail == "empty" else "catch{exit 1} # ") + HELPER_MARK)
        return ("%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -NoProfile -NonInteractive "
                f"-Command \"{script}\"")
    if platform == "darwin":
        # Not validated on macOS (no macOS machine available): implemented from the platform documentation. The key
        # reaches perl through its environment (not argv: other users can see command lines on macOS).
        return (f"B=http://127.0.0.1:{port}/.well-known; "
                "c() { /usr/bin/curl -q -fsS --noproxy 127.0.0.1 -m 3 \"$@\"; }; "
                "p=$(c $B/tdflowmcp | /usr/bin/sed -n 's/.*\"connect\":\"\\(prj_[A-Za-z0-9]\\{16\\}\\)\".*/\\1/p'); "
                f"s=$([ -n \"$p\" ] && /usr/bin/security find-generic-password -s {VAULT_SERVICE} -a \"{slot}:$p\" -w "
                "2>/dev/null); t=${s%%|*}; k=${s##*|}; "
                "n=$(/usr/bin/od -An -tx1 -N16 /dev/urandom | /usr/bin/tr -d ' \\n'); "
                "r=$(c $B/tdflowmcp-proof/$n | /usr/bin/sed -n 's/.*\"proof\":\"\\([0-9a-f]\\{64\\}\\)\".*/\\1/p'); "
                f"e=$(K=\"$k\" M=\"tdflowmcp-proof|$p|{port}|$n\" /usr/bin/perl -MDigest::SHA=hmac_sha256_hex "
                "-e 'print hmac_sha256_hex($ENV{M}, pack(\"H*\", $ENV{K}))' 2>/dev/null); "
                "case \"$t\" in tdf_*) if [ -n \"$r\" ] && [ \"$r\" = \"$e\" ]; then "
                "printf '{\"Authorization\":\"Bearer %s\"}' \"$t\"; else printf '{}'; fi;; "
                f"*) printf '{{}}';; esac # {HELPER_MARK}")
    raise ConnectError("PLATFORM_UNSUPPORTED", "automatic setup is available on Windows and macOS only")


def claude_entry(platform: str, port: int, slot: str, vault_dir: str) -> Dict:
    return {"type": "http", "url": f"http://127.0.0.1:{int(port)}/mcp",
            "headersHelper": helper_command(platform, port, slot, vault_dir)}




def _shape(entry) -> Optional[Dict]:
    """The entry with its port blanked (the only part that may differ between TDflowMCP's own entries)."""
    if not isinstance(entry, dict) or set(entry) != {"type", "url", "headersHelper"}:
        return None
    m = re.match(r"^http://127\.0\.0\.1:(\d{1,5})/mcp$", str(entry.get("url") or ""))
    if not m:
        return None
    port = m.group(1)
    return {k: str(v).replace(f"127.0.0.1:{port}", "127.0.0.1:PORT").replace(f"|{port}|", "|PORT|")
            for k, v in entry.items()}


def is_ours(entry, template: Dict) -> bool:
    """Review 19 (finding 8): an entry is TDflowMCP's only if it is exactly the generated template, apart from the
    port. A user's own helper that merely mentions TDflowMCP is never treated as ours (so never silently replaced or
    removed)."""
    a, b = _shape(entry), _shape(template)
    return a is not None and a == b


@dataclass
class Plan:
    client: str
    path: str                     # absolute (private: kept in memory and private storage only)
    shown_path: str               # without the user name
    action: str                   # create_file | add | update | replace | unchanged
    entry: Dict
    base_sha: str                 # sha256 of the file the plan was made from ("" when it did not exist)
    preserved: List[str]          # other user-scope MCP server names kept
    previous_url: str = ""        # the url of an entry that would be replaced (never its headers or query)
    format_preserved: bool = True
    previous_entry: Optional[Dict] = None   # the replaced entry itself (kept privately, restored by undo)


def _load(text: str) -> dict:
    try:
        d = json.loads(text)
    except ValueError:
        raise ConnectError("CONFIG_INVALID", "the agent's configuration file is not valid JSON; nothing was changed")
    if not isinstance(d, dict):
        raise ConnectError("CONFIG_INVALID", "the agent's configuration file is not a JSON object; nothing changed")
    servers = d.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise ConnectError("CONFIG_INVALID", "mcpServers in the agent's configuration is not an object")
    return d


def _dump(d: dict, like: str) -> str:
    out = json.dumps(d, indent=2, ensure_ascii=False)
    try:
        out.encode("utf-8")
    except UnicodeEncodeError:                  # review 19: lone surrogates written by the client's JSON encoder
        out = json.dumps(d, indent=2, ensure_ascii=True)
    return out + "\n" if like.endswith("\n") else out


def _no_link(path: str):
    """Review 19 (finding 12): a linked configuration file (dotfiles) is never replaced by a regular file."""
    if os.path.islink(path):
        raise ConnectError("CONFIG_IS_LINK", "the agent's configuration file is a link; set it up manually")


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ConnectError("CONFIG_UNREADABLE", f"the agent's configuration could not be read ({type(e).__name__})")
    if len(data) > MAX_CONFIG_BYTES:
        raise ConnectError("CONFIG_TOO_LARGE", "the agent's configuration file is unexpectedly large; nothing changed")
    return data


def _sha(b: Optional[bytes]) -> str:
    return hashlib.sha256(b).hexdigest() if b is not None else ""


def _safe_url(u: str) -> str:
    """For display only: scheme, host and path of a replaced entry (review 19: never its query or user info)."""
    m = re.match(r"^([a-z][a-z0-9+.-]*://)(?:[^/@]*@)?([^/?#]*)([^?#]*)", u, re.I)
    return (m.group(1) + m.group(2) + m.group(3))[:120] if m else u.split(" ")[0].split("?")[0][:60]


def _safe_target(cur) -> str:
    """For display only: where a replaced entry points. A URL without query or user info; of a command only the
    program's file name (review 23 (5): never its folder, which can hold the user name, nor its arguments)."""
    if not isinstance(cur, dict):
        return ""
    if cur.get("url"):
        return _safe_url(str(cur["url"]))
    cmd = str(cur.get("command") or "").strip().strip('"\'')
    base = re.split(r"[\\/]", cmd)[-1].split(" ")[0]
    return ("program " + re.sub(r"[^A-Za-z0-9_.-]", "", base)[:40]) if base else ""


def _show_name(name) -> str:
    """A server name as the plan shows it: short and plain, so that file content cannot write lines of the plan."""
    s = str(name)
    return s if re.match(r"^[A-Za-z0-9_.-]{1,40}\Z", s) else "(a server with an unusual name)"


def plan_claude(path: str, entry: Dict, env: Env, name: str = SERVER_NAME) -> Plan:
    _no_link(path)
    raw = _read(path)
    shown = display_path(path, env)
    if raw is None:
        return Plan("claude-code", path, shown, "create_file", entry, "", [])
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConnectError("CONFIG_INVALID", "the agent's configuration file is not UTF-8; nothing was changed")
    d = _load(text)
    servers = d.get("mcpServers") or {}
    cur = servers.get(name)
    others = sorted(k for k in servers if k != name)
    if cur == entry:
        action = "unchanged"
    elif cur is None:
        action = "add"
    elif is_ours(cur, entry):
        action = "update"
    else:
        action = "replace"
    prev = cur if action == "replace" else None
    prev_url = _safe_target(cur) if prev else ""
    return Plan("claude-code", path, shown, action, entry, _sha(raw), others, prev_url,
                _dump(d, text) == text, prev)


def _atomic_write(path: str, data: bytes, expect_sha: Optional[str] = None, private: bool = False):
    """Write ``data`` atomically. Review 19: the temporary file is created exclusively, without following links, with
    owner-only permissions; the replaced file keeps its own mode (``private``: always owner-only); ``expect_sha``: the
    file must still be exactly what was read (re-checked right before the replace, so a concurrent writer is never
    overwritten silently)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.tdflowmcp-{os.getpid()}-{os.urandom(6).hex()}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if not private and os.name != "nt" and os.path.exists(path):
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        for attempt in range(6):
            if expect_sha is not None and _sha(_read(path)) != expect_sha:
                raise ConnectError("CONFIG_CHANGED", "the agent's configuration changed while it was being written")
            try:
                os.replace(tmp, path)
                break
            except PermissionError:          # Windows: another program (antivirus, indexer, the client) holds the file
                if attempt == 5:
                    raise ConnectError("CONFIG_BUSY", "the agent's configuration file is in use; try again")
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _backup(raw: Optional[bytes], backup_dir: str, stem: str, now: float) -> str:
    """A copy of the file before the change, in private per-user storage (it may hold other servers' settings)."""
    if raw is None:
        return ""
    os.makedirs(backup_dir, exist_ok=True)
    name = f"{stem}.{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}.{_sha(raw)[:8]}.bak"
    _atomic_write(os.path.join(backup_dir, name), raw, private=True)
    olds = sorted(n for n in os.listdir(backup_dir) if n.startswith(stem + ".") and n.endswith(".bak"))
    for n in olds[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(backup_dir, n))
        except OSError:
            pass
    return name


def apply_claude(plan: Plan, env: Env, backup_dir: str, now: Optional[float] = None,
                 name: str = SERVER_NAME) -> Dict:
    """Write the approved change. If the file changed since the review (Claude Code rewrites it often), the plan is
    recomputed; it proceeds only when the approved action is still the same, else ``CONFIG_CHANGED`` (ask again). The
    file is re-checked right before it is replaced (a few retries). If the result cannot be confirmed, the file is put
    back from the backup just taken (review 19)."""
    now = time.time() if now is None else now
    approved = plan
    raw, out = None, b""
    for attempt in range(6):
        _no_link(plan.path)
        raw = _read(plan.path)
        if _sha(raw) != plan.base_sha:
            # Review 23 (1): every plan made after the approval, also one made after a failed write, is compared with
            # the approved one before anything is written; the loop then reads the file again.
            again = plan_claude(plan.path, plan.entry, env, name)
            same = again.action == approved.action or {again.action, approved.action} <= {"add", "create_file"} or \
                again.action == "unchanged"
            if not same or again.previous_url != approved.previous_url:
                raise ConnectError("CONFIG_CHANGED", "the agent's configuration changed since you reviewed it")
            plan = again
            continue
        if plan.action == "unchanged":
            return {"action": "unchanged", "backup": "", "shown_path": plan.shown_path, "created_key": False,
                    "previous_entry": None}
        text = raw.decode("utf-8-sig") if raw is not None else ""
        d = _load(text) if raw is not None else {}
        servers = d.get("mcpServers")
        created_key = not isinstance(servers, dict)
        servers = dict(servers) if isinstance(servers, dict) else {}
        servers[name] = plan.entry
        d["mcpServers"] = servers
        out = _dump(d, text or "\n").encode("utf-8")
        backup = _backup(raw, backup_dir, "claude.json", now)
        try:
            if raw is None:
                if os.path.exists(plan.path):
                    raise ConnectError("CONFIG_CHANGED", "the agent's configuration appeared while it was written")
                _atomic_write(plan.path, out)
            else:
                _atomic_write(plan.path, out, expect_sha=_sha(raw))
        except ConnectError as e:
            if e.code == "CONFIG_CHANGED" and attempt < 5:
                continue                             # the next pass sees the changed file and re-checks the plan
            raise
        break
    else:
        raise ConnectError("CONFIG_CHANGED", "the agent's configuration kept changing while it was being written")
    check = _read(plan.path)
    try:
        got = (_load(check.decode("utf-8-sig")).get("mcpServers") or {}) if check is not None else {}
    except (ConnectError, UnicodeDecodeError):
        got = {}
    kept = sorted(k for k in got if k != name)
    if got.get(name) != plan.entry or kept != plan.preserved:
        if raw is not None:      # put the file back as it was, unless someone else wrote it meanwhile (review 23 (4))
            try:
                _atomic_write(plan.path, raw, expect_sha=_sha(out))
            except Exception:
                pass
        raise ConnectError("CONFIG_NOT_VALIDATED", "the change could not be confirmed by reading the file back")
    return {"action": plan.action, "backup": backup, "shown_path": plan.shown_path, "created_key": created_key,
            "previous_entry": plan.previous_entry}


def still_configured(path: str, entry: Dict, name: str = SERVER_NAME) -> Optional[bool]:
    """True when the file still holds exactly our entry; None when unreadable."""
    try:
        raw = _read(path)
        if raw is None:
            return False
        return (_load(raw.decode("utf-8-sig")).get("mcpServers") or {}).get(name) == entry
    except ConnectError:
        return None


def remove_claude(path: str, env: Env, backup_dir: str, template: Dict, restore_entry: Optional[Dict] = None,
                  now: Optional[float] = None, name: str = SERVER_NAME, drop_empty_key: bool = False) -> Dict:
    """Undo: remove ``mcpServers.tdflow`` only if it is still exactly TDflowMCP's entry (``template``, port aside); put
    back the entry the connection replaced (``restore_entry``, kept in the private connection record). Everything else
    stays as it is now. ``drop_empty_key``: the connection created ``mcpServers``; remove it when nothing else is in
    it."""
    now = time.time() if now is None else now
    _no_link(path)
    raw = _read(path)
    if raw is None:
        return {"action": "absent"}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConnectError("CONFIG_INVALID", "the agent's configuration file is not UTF-8")
    d = _load(text)
    servers = dict(d.get("mcpServers") or {})
    cur = servers.get(name)
    if cur is None:
        return {"action": "absent"}
    if not is_ours(cur, template):
        return {"action": "not_ours"}                # the user changed it since: leave it alone
    previous = restore_entry if isinstance(restore_entry, dict) and not is_ours(restore_entry, template) else None
    backup = _backup(raw, backup_dir, "claude.json", now)
    if previous is not None:
        servers[name] = previous
    else:
        del servers[name]
    if servers or not drop_empty_key:
        d["mcpServers"] = servers
    else:
        d.pop("mcpServers", None)
    _atomic_write(path, _dump(d, text).encode("utf-8"), expect_sha=_sha(raw))
    return {"action": "restored" if previous is not None else "removed", "backup": backup}


# ---- OpenAI Codex configuration: ~/.codex/config.toml, edited as text (ADR-0017) -----------------------------------
# Ownership is an allowlist (review 23 (7)): besides its two own keys, TDflowMCP's "tdflow" table may only carry the
# documented preference keys. Any other key (another transport, another credential source such as
# bearer_token_env_var, http_headers or OAuth, which Codex gives precedence over the helper, an execution
# environment, or a key this version does not know) makes the table someone else's.
CODEX_PREFERENCES = frozenset(("enabled", "required", "startup_readiness", "startup_timeout_sec", "startup_timeout_ms",
                               "tool_timeout_sec", "enabled_tools", "disabled_tools", "omit_tools_from",
                               "default_tools_approval_mode", "tools", "supports_parallel_tool_calls",
                               "tool_input_schema_max_bytes"))
CODEX_COMMENT = "# Added by TDflowMCP (Connect Agent). It holds no secret. Disconnect Agent in TouchDesigner removes it."
_BOM = b"\xef\xbb\xbf"
_TOML_HDR = re.compile(r"^[ \t]*(\[\[?)(.*?)(\]\]?)[ \t]*(?:#.*)?$")
_UNSUPPORTED = "the agent's configuration file uses a layout TDflowMCP does not edit; nothing was changed"


def codex_config_path(env: Env) -> str:
    d = env.environ.get("CODEX_HOME")
    return os.path.join(d if d else os.path.join(env.home, ".codex"), "config.toml")


def codex_entry(platform: str, port: int, slot: str, vault_dir: str) -> Dict:
    """Codex runs ``http_headers_helper`` through ``cmd.exe /Q /D /C "<command>"`` with a reduced environment
    (observed 2026-11-25: CLI 0.160.0 and the VS Code extension's engine 0.171.0-alpha.13.2), caches the headers for
    the connection, and runs it again once after a 401. Windows only: no other platform was observed."""
    if platform != "win32":
        raise ConnectError("PLATFORM_UNSUPPORTED", "automatic Codex setup is available on Windows only")
    return {"url": f"http://127.0.0.1:{int(port)}/mcp",
            "http_headers_helper": helper_command(platform, port, slot, vault_dir, fail="exit")}


def _codex_shape(entry) -> Optional[tuple]:
    url, helper = entry.get("url"), entry.get("http_headers_helper")
    if not isinstance(url, str) or not isinstance(helper, str):
        return None
    m = re.match(r"^http://127\.0\.0\.1:(\d{1,5})/mcp$", url)
    if not m:
        return None
    port = m.group(1)
    return ("http://127.0.0.1:PORT/mcp",
            helper.replace(f"127.0.0.1:{port}", "127.0.0.1:PORT").replace(f"|{port}|", "|PORT|"))


def codex_is_ours(cur, template: Dict) -> bool:
    """A ``tdflow`` table is TDflowMCP's only if its url and helper equal the generated ones (port aside) and every
    other key is a known preference. Preferences Codex or the user added to it (``enabled``, timeouts, tool lists,
    per-tool approval modes) are kept on update and go with the table on removal."""
    if not isinstance(cur, dict) or set(cur) - CODEX_PREFERENCES - {"url", "http_headers_helper"}:
        return False
    a, b = _codex_shape(cur), _codex_shape(template)
    return a is not None and a == b


def _toml_str(s: str) -> str:
    """A TOML basic string (the helper holds quotes and backslashes)."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_parse(text: str, code: str = "CONFIG_INVALID") -> dict:
    try:
        import tomllib
    except ImportError:                                  # Python < 3.11: never guess at a file we cannot check
        raise ConnectError("CONFIG_UNSUPPORTED", "this Python cannot check TOML files; set the agent up manually")
    try:
        return tomllib.loads(text)
    except (ValueError, RecursionError):
        raise ConnectError(code, "the agent's configuration file is not valid TOML; nothing was changed"
                           if code == "CONFIG_INVALID" else _UNSUPPORTED)


def _codex_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConnectError("CONFIG_INVALID", "the agent's configuration file is not UTF-8; nothing was changed")


def _toml_key(s: str) -> Optional[List[str]]:
    """The parts of a dotted key (bare, "basic" or 'literal'), or None when it is not understood."""
    parts, i, n = [], 0, len(s)
    while True:
        while i < n and s[i] in " \t":
            i += 1
        if i >= n:
            return None
        if s[i] == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                return None
            try:
                part = json.loads(s[i:j + 1])
            except ValueError:
                return None
            if not isinstance(part, str):
                return None
            parts.append(part)
            i = j + 1
        elif s[i] == "'":
            j = s.find("'", i + 1)
            if j == -1:
                return None
            parts.append(s[i + 1:j])
            i = j + 1
        else:
            m = re.match(r"[A-Za-z0-9_-]+", s[i:])
            if not m:
                return None
            parts.append(m.group(0))
            i += m.end()
        while i < n and s[i] in " \t":
            i += 1
        if i >= n:
            return parts
        if s[i] != ".":
            return None
        i += 1


def _toml_state(s: str, ml: Optional[str], depth: int):
    """Advance over one line: ``ml`` is the delimiter of an open multi-line string, ``depth`` the nesting of arrays
    and inline tables. Used only to tell table headers from lines that merely look like one."""
    i, n = 0, len(s)
    while i < n:
        if ml:
            j = s.find(ml, i)
            while j != -1 and ml == '"""' and (len(s[:j]) - len(s[:j].rstrip("\\"))) % 2 == 1:
                j = s.find(ml, j + 1)
            if j == -1:
                return ml, depth
            i, ml = j + 3, None
            continue
        c = s[i]
        if c == "#":
            break
        if s.startswith('"""', i) or s.startswith("'''", i):
            ml = s[i:i + 3]
            i += 3
            continue
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if c == "'":
            j = s.find("'", i + 1)
            i = n if j == -1 else j + 1
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth = max(0, depth - 1)
        i += 1
    return ml, depth


def _toml_heads(lines: List[str]) -> List[tuple]:
    """(line index, key path or None, is array-of-tables) for every table header."""
    heads, ml, depth = [], None, 0
    for i, line in enumerate(lines):
        s = line.rstrip("\r\n")
        if ml is None and depth == 0:
            m = _TOML_HDR.match(s)
            if m and len(m.group(1)) == len(m.group(3)):
                heads.append((i, _toml_key(m.group(2)), len(m.group(1)) == 2))
                continue
        ml, depth = _toml_state(s, ml, depth)
    return heads


def _codex_ranges(lines: List[str], name: str):
    """Line ranges of the ``[mcp_servers.<name>]`` table and of its sub-tables: (main, [subs]), each
    (header line, end of its last content line). Trailing blank lines and comments stay with what follows."""
    heads = _toml_heads(lines)
    main, subs = [], []
    for k, (i, path, is_array) in enumerate(heads):
        if not path or path[:2] != ["mcp_servers", name]:
            continue
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        while end > i + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        (main if len(path) == 2 and not is_array else subs).append((i, end))
    if len(main) != 1:
        raise ConnectError("CONFIG_UNSUPPORTED", _UNSUPPORTED)
    return main[0], subs


def _codex_block(entry: Dict, eol: str, name: str) -> List[str]:
    return [f"[mcp_servers.{name}]{eol}", CODEX_COMMENT + eol, f"url = {_toml_str(entry['url'])}{eol}",
            f"http_headers_helper = {_toml_str(entry['http_headers_helper'])}{eol}"]


def _toml_lines(text: str) -> List[str]:
    """The lines of a TOML text with their endings. Only ``\n`` ends a line (review 23 (3): ``str.splitlines`` also
    splits on U+0085, U+2028 and U+2029, which TOML allows inside comments and strings)."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _append(lines: List[str], block: List[str], eol: str) -> bool:
    """Append ``block`` after one blank line that is always added (``_cut`` removes exactly that line again, so a
    file that already ended with blank lines keeps them). Returns True when the file had no final newline."""
    added = False
    if lines:
        if not lines[-1].endswith("\n"):
            lines[-1] += eol
            added = True
        lines.append(eol)
    lines += block
    return added


def _cut(lines: List[str], ranges: List[tuple]) -> str:
    """Remove the ranges (and the one blank line that separates each from what precedes it); returns their text."""
    taken = []
    for start, end in sorted(ranges, reverse=True):
        taken.insert(0, "".join(lines[start:end]))
        if start > 0 and not lines[start - 1].strip():
            start -= 1
        del lines[start:end]
    return "".join(taken)


def _codex_edit(text: str, entry: Dict, mode: str, name: str, restore_text: str = "", strip_newline: bool = False):
    """The new file text for ``mode`` (add | update | replace | remove). Only TDflowMCP's table is touched; every
    other line stays byte for byte. Returns (text, removed text, a final newline was added)."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = _toml_lines(text)
    removed, added = "", False
    if mode == "add":
        added = _append(lines, _codex_block(entry, eol, name), eol)
    elif mode == "update":
        (start, end), _subs = _codex_ranges(lines, name)
        found, ml, depth = {}, None, 0
        for i in range(start + 1, end):
            s = lines[i].rstrip("\r\n")
            if ml is None and depth == 0:
                m = re.match(r"^[ \t]*(url|http_headers_helper)[ \t]*=", s)
                if m:
                    found.setdefault(m.group(1), []).append(i)
            ml, depth = _toml_state(s, ml, depth)
        if sorted(found) != ["http_headers_helper", "url"] or any(len(v) != 1 for v in found.values()):
            raise ConnectError("CONFIG_UNSUPPORTED", _UNSUPPORTED)
        for key, (i,) in found.items():
            tail = lines[i][len(lines[i].rstrip("\r\n")):]
            lines[i] = f"{key} = {_toml_str(entry[key])}{tail}"
    elif mode in ("replace", "remove"):
        main, subs = _codex_ranges(lines, name)
        last = max(e for _, e in [main] + subs) == len(lines)
        removed = _cut(lines, [main] + subs)
        if mode == "replace":
            added = _append(lines, _codex_block(entry, eol, name), eol)
        elif restore_text:
            _append(lines, _toml_lines(restore_text), eol)
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += eol
        elif strip_newline and last and lines and lines[-1].endswith(eol):
            lines[-1] = lines[-1][:-len(eol)]          # the connection had added the file's final newline
    else:
        raise ConnectError("CONFIG_UNSUPPORTED", _UNSUPPORTED)
    return "".join(lines), removed, added


def _without_server(d: dict, name: str):
    import copy
    d = copy.deepcopy(d)
    ms, cur = d.get("mcp_servers"), None
    if isinstance(ms, dict):
        cur = ms.pop(name, None)
        if not ms:
            d.pop("mcp_servers")
    return d, cur


def _codex_verify(old: dict, new_text: str, name: str, mode: str, entry: Dict = None, expect=None):
    """Fail closed: the edited text must parse, everything except ``mcp_servers.<name>`` must be equal to before, and
    that table must be exactly what the mode intends."""
    new = _toml_parse(new_text, "CONFIG_UNSUPPORTED")
    rest_old, cur_old = _without_server(old, name)
    rest_new, cur_new = _without_server(new, name)
    if mode in ("add", "replace"):
        ok = cur_new == entry
    elif mode == "update":
        ok = (isinstance(cur_new, dict) and isinstance(cur_old, dict) and all(cur_new.get(k) == v for k, v in entry.items())
              and {k: v for k, v in cur_new.items() if k not in entry} == {k: v for k, v in cur_old.items()
                                                                           if k not in entry})
    else:
        ok = cur_new == expect
    if rest_old != rest_new or not ok:
        raise ConnectError("CONFIG_UNSUPPORTED", _UNSUPPORTED)


def _codex_saved(removed: str, cur, name: str) -> Dict:
    """What Disconnect puts back after a replace: the removed text, accepted only if it parses to exactly the table
    it was cut from (review 23 (2): otherwise the user's entry could not be restored later)."""
    got = (_toml_parse(removed, "CONFIG_UNSUPPORTED").get("mcp_servers") or {}).get(name)
    if got != cur:
        raise ConnectError("CONFIG_UNSUPPORTED", _UNSUPPORTED)
    return {"toml": removed}


def plan_codex(path: str, entry: Dict, env: Env, name: str = SERVER_NAME) -> Plan:
    """The change, computed and checked as a dry run, so an unsupported file is refused before the user is asked."""
    _no_link(path)
    raw = _read(path)
    shown = display_path(path, env)
    if raw is None:
        return Plan("codex", path, shown, "create_file", entry, "", [])
    text = _codex_text(raw)
    old = _toml_parse(text)
    ms = old.get("mcp_servers")
    if ms is not None and not isinstance(ms, dict):
        raise ConnectError("CONFIG_UNSUPPORTED", _UNSUPPORTED)
    cur = (ms or {}).get(name)
    others = sorted(k for k in (ms or {}) if k != name)
    if cur is None:
        action = "add"
    elif codex_is_ours(cur, entry):
        action = "unchanged" if all(cur.get(k) == v for k, v in entry.items()) else "update"
    else:
        action = "replace"
    prev, prev_url = None, ""
    if action != "unchanged":
        new_text, removed, _ = _codex_edit(text, entry, action, name)
        _codex_verify(old, new_text, name, action, entry)
        if action == "replace":
            prev = _codex_saved(removed, cur, name)
            prev_url = _safe_target(cur)
    return Plan("codex", path, shown, action, entry, _sha(raw), others, prev_url, True, prev)


def apply_codex(plan: Plan, env: Env, backup_dir: str, now: Optional[float] = None, name: str = SERVER_NAME) -> Dict:
    """Write the approved change (same discipline as ``apply_claude``): the plan is recomputed if the file changed
    since the review and must still be the approved action; a backup goes to private storage first; the file is
    re-checked right before the atomic replace; the result is read back, and put back if it cannot be confirmed."""
    now = time.time() if now is None else now
    approved = plan
    raw, old, mode, backup, added, out = None, {}, "add", "", False, b""
    for attempt in range(6):
        _no_link(plan.path)
        raw = _read(plan.path)
        if _sha(raw) != plan.base_sha:
            # Review 23 (1): every plan made after the approval, also one made after a failed write, is compared with
            # the approved one before anything is written.
            again = plan_codex(plan.path, plan.entry, env, name)
            same = again.action == approved.action or {again.action, approved.action} <= {"add", "create_file"} or \
                again.action == "unchanged"
            if not same or again.previous_url != approved.previous_url:
                raise ConnectError("CONFIG_CHANGED", "the agent's configuration changed since you reviewed it")
            plan = again
            continue
        if plan.action == "unchanged":
            return {"action": "unchanged", "backup": "", "shown_path": plan.shown_path, "created_key": False,
                    "created_file": False, "added_newline": False, "previous_entry": None}
        text = _codex_text(raw) if raw is not None else ""
        old = _toml_parse(text) if raw is not None else {}
        mode = "add" if plan.action == "create_file" else plan.action
        new_text, removed, added = _codex_edit(text, plan.entry, mode, name)
        _codex_verify(old, new_text, name, mode, plan.entry)
        if mode == "replace":                            # the text that is saved is the text that is written away
            plan.previous_entry = _codex_saved(removed, (old.get("mcp_servers") or {}).get(name), name)
        out = (_BOM if raw is not None and raw.startswith(_BOM) else b"") + new_text.encode("utf-8")
        backup = _backup(raw, backup_dir, "codex-config.toml", now)
        try:
            if raw is None:
                if os.path.exists(plan.path):
                    raise ConnectError("CONFIG_CHANGED", "the agent's configuration appeared while it was written")
                _atomic_write(plan.path, out)
            else:
                _atomic_write(plan.path, out, expect_sha=_sha(raw))
        except ConnectError as e:
            if e.code == "CONFIG_CHANGED" and attempt < 5:
                continue                                 # the next pass sees the changed file and re-checks the plan
            raise
        break
    else:
        raise ConnectError("CONFIG_CHANGED", "the agent's configuration kept changing while it was being written")
    try:
        check = _read(plan.path)
        if check is None:
            raise ConnectError("CONFIG_NOT_VALIDATED", "missing")
        _codex_verify(old, _codex_text(check), name, mode, plan.entry)
    except ConnectError:
        try:     # put the file back as it was, unless someone else wrote it meanwhile (review 23 (4))
            if raw is not None:
                _atomic_write(plan.path, raw, expect_sha=_sha(out))
            elif _sha(_read(plan.path)) == _sha(out):
                os.remove(plan.path)
        except Exception:
            pass
        raise ConnectError("CONFIG_NOT_VALIDATED", "the change could not be confirmed by reading the file back")
    return {"action": plan.action, "backup": backup, "shown_path": plan.shown_path, "created_key": False,
            "created_file": raw is None, "added_newline": bool(added), "previous_entry": plan.previous_entry}


def codex_still_configured(path: str, entry: Dict, name: str = SERVER_NAME) -> Optional[bool]:
    try:
        raw = _read(path)
        if raw is None:
            return False
        cur = (_toml_parse(_codex_text(raw)).get("mcp_servers") or {}).get(name)
        return isinstance(cur, dict) and all(cur.get(k) == v for k, v in entry.items()) and codex_is_ours(cur, entry)
    except (ConnectError, AttributeError):
        return None


def remove_codex(path: str, env: Env, backup_dir: str, template: Dict, restore_entry: Optional[Dict] = None,
                 now: Optional[float] = None, name: str = SERVER_NAME, created_file: bool = False,
                 added_newline: bool = False) -> Dict:
    """Undo: remove the ``tdflow`` table (with its sub-tables) only if it is still TDflowMCP's; put back the text of
    the table the connection replaced. A file the connection created is deleted again when nothing else is in it."""
    now = time.time() if now is None else now
    _no_link(path)
    raw = _read(path)
    if raw is None:
        return {"action": "absent"}
    text = _codex_text(raw)
    old = _toml_parse(text)
    ms = old.get("mcp_servers")
    cur = ms.get(name) if isinstance(ms, dict) else None
    if cur is None:
        return {"action": "absent"}
    if not codex_is_ours(cur, template):
        return {"action": "not_ours"}                    # the user changed it since: leave it alone
    restore = restore_entry.get("toml") if isinstance(restore_entry, dict) else ""
    restore = restore if isinstance(restore, str) and restore.strip() else ""
    expect = None
    if restore:
        expect = (_toml_parse(restore, "CONFIG_UNSUPPORTED").get("mcp_servers") or {}).get(name)
        if expect is None or codex_is_ours(expect, template):
            restore, expect = "", None
    new_text, _removed, _ = _codex_edit(text, template, "remove", name, restore_text=restore,
                                        strip_newline=added_newline)
    _codex_verify(old, new_text, name, "remove", expect=expect)
    backup = _backup(raw, backup_dir, "codex-config.toml", now)
    if created_file and not new_text.strip():
        if _sha(_read(path)) != _sha(raw):
            raise ConnectError("CONFIG_CHANGED", "the agent's configuration changed while it was being written")
        os.remove(path)
    else:
        _atomic_write(path, (_BOM if raw.startswith(_BOM) else b"") + new_text.encode("utf-8"), expect_sha=_sha(raw))
    return {"action": "restored" if restore else "removed", "backup": backup}


# ---- one interface for the adapter: which functions serve which client -------------------------------------------
def client_entry(client: str, platform: str, port: int, slot: str, vault_dir: str) -> Dict:
    if client == "codex":
        return codex_entry(platform, port, slot, vault_dir)
    return claude_entry(platform, port, slot, vault_dir)


def client_config_path(client: str, env: Env) -> str:
    return codex_config_path(env) if client == "codex" else claude_config_path(env)


def plan_client(client: str, path: str, entry: Dict, env: Env) -> Plan:
    return plan_codex(path, entry, env) if client == "codex" else plan_claude(path, entry, env)


def apply_client(plan: Plan, env: Env, backup_dir: str) -> Dict:
    return apply_codex(plan, env, backup_dir) if plan.client == "codex" else apply_claude(plan, env, backup_dir)


def remove_client(client: str, path: str, env: Env, backup_dir: str, template: Dict, record: Dict) -> Dict:
    """Undo for a connection ``record`` (or the result of the apply that is being rolled back)."""
    restore = record.get("previous_entry") if record.get("replaced", record.get("previous_entry") is not None) else None
    if client == "codex":
        return remove_codex(path, env, backup_dir, template, restore_entry=restore,
                            created_file=bool(record.get("created_file")),
                            added_newline=bool(record.get("added_newline")))
    return remove_claude(path, env, backup_dir, template, restore_entry=restore,
                         drop_empty_key=bool(record.get("created_key")))


def client_still_configured(client: str, path: str, entry: Dict) -> Optional[bool]:
    return codex_still_configured(path, entry) if client == "codex" else still_configured(path, entry)


def helper_text(entry: Dict) -> str:
    """The helper command of an entry, whichever client it belongs to (shown in View Connection Details)."""
    return str((entry or {}).get("headersHelper") or (entry or {}).get("http_headers_helper") or "")


# ---- credential vault ---------------------------------------------------------------------------------------------
class Vault:
    """OS-protected storage for one token per (slot, project). Windows DPAPI files; macOS Keychain."""

    def __init__(self, platform: str, root: str, runner: Callable = None, dpapi: Callable = None):
        self.platform, self.root = platform, root
        self.runner = runner or _run_security
        self.dpapi = dpapi or _dpapi_protect

    @property
    def supported(self) -> bool:
        return self.platform in ("win32", "darwin")

    def _file(self, slot: str, pid: str) -> str:
        if not SLOT_RE.match(slot) or not PROJECT_RE.match(pid):
            raise ConnectError("BAD_SLOT", "invalid credential slot or project id")
        return os.path.join(self.root, slot, pid + ".dpapi")

    def put(self, slot: str, pid: str, token: str, key_hex: str):
        """Store ``token|key``: the agent's token and the project's connect key (the helper checks the listener's
        proof with it before it releases the token)."""
        if not TOKEN_RE.match(token or ""):
            raise ConnectError("BAD_TOKEN", "not a TDflowMCP token")
        if not KEY_RE.match(key_hex or ""):
            raise ConnectError("BAD_KEY", "not a connect key")
        secret = token + "|" + key_hex
        if self.platform == "win32":
            blob = self.dpapi(secret.encode("utf-16-le")).hex()
            _atomic_write(self._file(slot, pid), blob.encode("ascii"), private=True)
        elif self.platform == "darwin":
            self._file(slot, pid)                    # validates slot and id
            cmd = f"add-generic-password -U -s {VAULT_SERVICE} -a {slot}:{pid} -l {VAULT_SERVICE} -w {secret}\n"
            if self.runner(["/usr/bin/security", "-i"], cmd) != 0 or not self.has(slot, pid):
                raise ConnectError("VAULT_FAILED", "the Keychain did not accept the credential")
        else:
            raise ConnectError("PLATFORM_UNSUPPORTED", "no protected credential store on this platform")

    def others(self, slot: str, pid: str) -> int:
        """How many other projects still have a credential in this slot (Windows; macOS: unknown, counted as 0)."""
        if self.platform != "win32":
            return 0
        try:
            names = os.listdir(os.path.join(self.root, slot))
        except OSError:
            return 0
        return sum(1 for n in names if n.endswith(".dpapi") and PROJECT_RE.match(n[:-6]) and n[:-6] != pid)

    def has(self, slot: str, pid: str) -> bool:
        if self.platform == "win32":
            return os.path.isfile(self._file(slot, pid))
        if self.platform == "darwin":
            self._file(slot, pid)
            return self.runner(["/usr/bin/security", "find-generic-password", "-s", VAULT_SERVICE, "-a",
                                f"{slot}:{pid}"], "") == 0
        return False

    def delete(self, slot: str, pid: str) -> bool:
        if self.platform == "win32":
            try:
                os.remove(self._file(slot, pid))
                return True
            except FileNotFoundError:
                return False
        if self.platform == "darwin":
            self._file(slot, pid)
            return self.runner(["/usr/bin/security", "delete-generic-password", "-s", VAULT_SERVICE, "-a",
                                f"{slot}:{pid}"], "") == 0
        return False


def _run_security(argv: List[str], stdin_text: str) -> int:
    """The core never starts processes (boundary test): on macOS the TD adapter injects the runner for the fixed
    ``/usr/bin/security`` commands. Without it, the Keychain is unavailable."""
    raise ConnectError("VAULT_UNAVAILABLE", "the macOS Keychain runner is not available")


def _dpapi_protect(data: bytes) -> bytes:
    """Windows CryptProtectData (current user, no UI)."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    c32, k32 = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32")
    c32.CryptProtectData.argtypes = [ctypes.POINTER(BLOB), wintypes.LPCWSTR, ctypes.POINTER(BLOB), ctypes.c_void_p,
                                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    c32.CryptProtectData.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    i, o = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    if not c32.CryptProtectData(ctypes.byref(i), None, None, None, None, 0x1, ctypes.byref(o)):
        raise ConnectError("VAULT_FAILED", "Windows could not protect the credential")
    try:
        return ctypes.string_at(o.pbData, o.cbData)
    finally:
        ctypes.memset(buf, 0, len(data))
        k32.LocalFree(o.pbData)


# ---- consent text -------------------------------------------------------------------------------------------------
ACTION_TEXT = {
    "create_file": "creates the file with the server \"tdflow\"",
    "add": "adds the server \"tdflow\"",
    "update": "updates TDflowMCP's server \"tdflow\"",
    "replace": "REPLACES your existing server \"tdflow\" (backed up, and put back if you disconnect)",
    "unchanged": "nothing to change (\"tdflow\" is already set up)",
}


CLIENT_NOTES = {
    "codex": ("Needs a recent Codex (tested: Codex CLI 0.160.0 and the VS Code extension 26.1120). If Next Step "
              "later says Codex sent no key, update Codex and pulse Repair Connection. Works for Codex running on "
              "this computer, not for Codex cloud tasks or a remote session."),
}
NEW_CHAT = {
    "Codex": " (VS Code: a new chat in the Codex panel; terminal: run codex)",
    "Claude Code": " (VS Code: a new conversation; terminal: run claude)",
}


def consent_text(client: str, interfaces: Sequence[str], plan: Optional[Plan], access: str, scope: str,
                 enable_mcp: bool, reuse_agent: str = "", platform: str = sys.platform,
                 also_found: Sequence[str] = ()) -> str:
    """What the user approves, before anything changes (usability review: short, plain words; the technical details
    are in View Connection Details). No secrets, no user names."""
    where = f" ({' and '.join(interfaces)})" if interfaces else ""
    lines = [f"{client}{where} will get access to this TouchDesigner project.", ""]
    if reuse_agent:
        lines.append(f"- Agent: {reuse_agent} gets a new key; its old keys stop working.")
    else:
        lines.append(f"- Access: {access}, only in {scope}")
    if plan is not None:
        lines.append(f"- {client} settings ({plan.shown_path}, in your user folder): {ACTION_TEXT[plan.action]}.")
        if plan.action == "replace" and plan.previous_url:
            lines.append(f"  The existing \"tdflow\" points to: {plan.previous_url}")
        if plan.action == "unchanged":
            pass                                         # nothing is written, so no backup is made
        elif plan.preserved:
            kept = ", ".join(map(_show_name, plan.preserved[:6])) + (
                f" and {len(plan.preserved) - 6} more" if len(plan.preserved) > 6 else "")
            lines.append(f"  Your other servers ({kept}) stay as they are; a backup is kept.")
        else:
            lines.append("  Nothing else in the file changes; a backup is kept.")
        if not plan.format_preserved:
            lines.append("  (The file is rewritten with the same content, so its spacing may change.)")
    store = "Windows' protected storage" if platform == "win32" else "the macOS Keychain (not yet tested on macOS)"
    lines.append(f"- Its key is kept in {store}, never in {client}'s settings or the project.")
    if enable_mcp:
        lines.append("- Enable MCP is TURNED ON (this computer only). Turn it off or Suspend Agents at any time.")
    if plan is not None and CLIENT_NOTES.get(plan.client):
        lines.append("- " + CLIENT_NOTES[plan.client])
    lines += ["", f"Then: pulse Copy Agent Prompt, start a new {client} chat{NEW_CHAT.get(client, '')} and paste it. "
              "A chat that was already open does not have the connection.",
              "Undo any time: Agents page > Disconnect Agent."]
    if also_found:
        lines.append("Also found: " + ", ".join(also_found) + " (manual setup: choose it in Coding Agent).")
    return "\n".join(lines)


def consent_short(client: str, plan: Optional[Plan], access: str, scope: str, enable_mcp: bool,
                  reuse_agent: str = "", platform: str = sys.platform, also_found: Sequence[str] = ()) -> str:
    """UX-08 (rc4, visual review): the plan as TouchDesigner's narrow dialog shows it (about 25 characters a line,
    short enough that its buttons stay visible). The full plan is in the viewer and View Connection Details."""
    lines = [f"{client} gets access", "to this project.", ""]
    if reuse_agent:
        lines += ["Same agent, new key", "(old keys stop working)."]
    else:
        lines += [f"Access: {access}", f"Scope: {scope}"]
    if plan is not None:
        # Usability review (ADR-0017): each action says what happens to which file; no backup is claimed when
        # nothing is written.
        if plan.action == "replace":
            lines += ["REPLACES \"tdflow\" in", plan.shown_path + ".", "Disconnect puts it back."]
        elif plan.action == "unchanged":
            lines += ["\"tdflow\" is already set", "up: no file change."]
        elif plan.action == "update":
            lines += ["Updates \"tdflow\" in", plan.shown_path]
        else:
            lines += ["Adds \"tdflow\" to", plan.shown_path]
        if plan.action != "unchanged":
            lines += ["(other servers kept," if plan.preserved else "(nothing else changes,", "backup made)."]
    lines += ["Key: Windows-protected."] if platform == "win32" else ["Key: macOS Keychain", "storage (untested)."]
    if enable_mcp:
        lines += ["Turns ON Enable MCP", "(this computer only)."]
    lines += ["", "Then: Copy Agent Prompt,", "paste it in a new", f"{client} chat.", "Undo: Disconnect Agent.",
              "Full plan: the viewer."]
    if also_found:
        lines += ["Also found: " + ", ".join(also_found), "(manual setup: pick it", "in Coding Agent)."]
    return "\n".join(lines)
