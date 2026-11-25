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

Other clients (Codex, Cursor, VS Code Copilot) have no helper mechanism: their documented credential options are an
environment variable present when the client starts, or OAuth (not implemented by TDflowMCP). They keep the short
manual fallback; see ``MANUAL``.
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

AUTOMATIC = ("claude-code",)                  # clients whose configuration and credentials can be set up safely


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
            e.environ = {k: v for k, v in e.environ.items() if k not in ("CLAUDE_CONFIG_DIR",)}
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
    ifaces = (["CLI"] if env.which("codex") else []) + [HOST_LABELS[h] + " extension" for h in
                                                         _ext_dirs(env, ("openai.chatgpt-",))]
    if ifaces:
        out.append({"client": "codex", "interfaces": ifaces, "automatic": False})
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
    """User-facing form without the user name (``~/.claude.json``)."""
    d = env.environ.get("CLAUDE_CONFIG_DIR")
    if d and os.path.normcase(os.path.dirname(path)) == os.path.normcase(d):
        return "%CLAUDE_CONFIG_DIR%/" + os.path.basename(path) if env.platform == "win32" else \
            "$CLAUDE_CONFIG_DIR/" + os.path.basename(path)
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


def helper_command(platform: str, port: int, slot: str, vault_dir: str) -> str:
    """The fixed ``headersHelper`` command. It prints ``{"Authorization": "Bearer <token>"}`` only for the project
    that the listener on ``port`` announces **and proves** (an HMAC over a fresh nonce with that project's connect key),
    otherwise ``{}`` (Claude Code then reports a failed connection). The token travels only on the helper's stdout to
    Claude Code."""
    if not SLOT_RE.match(slot):
        raise ConnectError("BAD_SLOT", "invalid credential slot")
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
            "catch{[Console]::Out.Write('{}')} # " + HELPER_MARK)
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
    prev_url = _safe_url(str(cur.get("url") or cur.get("command") or "")) if isinstance(cur, dict) and prev else ""
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
    for attempt in range(4):
        _no_link(plan.path)
        raw = _read(plan.path)
        if _sha(raw) != plan.base_sha:
            again = plan_claude(plan.path, plan.entry, env, name)
            same = again.action == approved.action or {again.action, approved.action} <= {"add", "create_file"} or \
                again.action == "unchanged"
            if not same or again.previous_url != approved.previous_url:
                raise ConnectError("CONFIG_CHANGED", "the agent's configuration changed since you reviewed it")
            plan = again
            raw = _read(plan.path)
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
            if e.code == "CONFIG_CHANGED" and attempt < 3:
                plan = plan_claude(plan.path, plan.entry, env, name)
                continue
            raise
        break
    check = _read(plan.path)
    try:
        got = (_load(check.decode("utf-8-sig")).get("mcpServers") or {}) if check is not None else {}
    except (ConnectError, UnicodeDecodeError):
        got = {}
    kept = sorted(k for k in got if k != name)
    if got.get(name) != plan.entry or kept != plan.preserved:
        if raw is not None:                          # put the file back as it was
            try:
                _atomic_write(plan.path, raw)
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
        lines.append(f"- {client} settings ({plan.shown_path}): {ACTION_TEXT[plan.action]}.")
        if plan.action == "replace" and plan.previous_url:
            lines.append(f"  The existing \"tdflow\" points to: {plan.previous_url}")
        if plan.preserved:
            kept = ", ".join(plan.preserved[:6]) + (f" and {len(plan.preserved) - 6} more" if len(plan.preserved) > 6
                                                    else "")
            lines.append(f"  Your other servers ({kept}) stay as they are; a backup is kept.")
        else:
            lines.append("  Nothing else in the file changes; a backup is kept.")
        if not plan.format_preserved:
            lines.append("  (The file is rewritten with the same content, so its spacing may change.)")
    store = "Windows' protected storage" if platform == "win32" else "the macOS Keychain (not yet tested on macOS)"
    lines.append(f"- Its key is kept in {store}, never in a file or the project.")
    if enable_mcp:
        lines.append("- Enable MCP is TURNED ON (this computer only). Turn it off or Suspend Agents at any time.")
    lines += ["", f"Then start a new {client} chat.", "Undo any time: Agents page > Disconnect Agent."]
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
        if plan.action == "replace":
            lines += ["REPLACES your \"tdflow\"", "entry (backed up, put", "back on Disconnect)."]
        elif plan.action == "unchanged":
            lines += ["Settings: no change."]
        else:
            lines += ["Adds \"tdflow\" to", plan.shown_path]
        lines += ["(other servers kept," if plan.preserved else "(nothing else changes,", "backup made)."]
    lines += ["Key: " + ("Windows protected" if platform == "win32" else "macOS Keychain"),
              "storage" + ("." if platform == "win32" else " (untested).")]
    if enable_mcp:
        lines += ["Turns ON Enable MCP", "(this computer only)."]
    lines += ["", "Then start a new", f"{client} chat.", "Undo: Disconnect Agent.", "Full plan: the viewer."]
    if also_found:
        lines += ["Also found: " + ", ".join(also_found), "(manual setup: pick it", "in Coding Agent)."]
    return "\n".join(lines)


# ---- manual fallback (clients without secure automatic provisioning) --------------------------------------------
MANUAL = {
    "codex": ("OpenAI Codex has no way to fetch a credential by itself: it reads the token from an environment "
              "variable that must exist when Codex starts (or uses OAuth, which TDflowMCP does not offer)."),
    "cursor": ("Cursor reads the token from an environment variable that must exist when Cursor starts (or uses "
               "OAuth, which TDflowMCP does not offer)."),
    "vscode-copilot": ("VS Code Copilot reads the token from an environment variable or asks for it once and keeps "
                       "it in VS Code's secret storage (deprecated editor format)."),
    "other": "Your client needs a Streamable HTTP server entry and an Authorization header.",
}
