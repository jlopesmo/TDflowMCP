# ADR-0017 — One-click OpenAI Codex connection through `http_headers_helper`

**Status:** Accepted (owner D28, [GitHub Issue #3](https://github.com/jlopesmo/TDflowMCP/issues/3#issuecomment-7175371704), 2026-11-25) as delivered for the tested Windows and Codex interfaces, with the limitations and security controls documented below.

History: the owner first approved it subject to the validation gates below (D27, 2026-11-25); they passed. It extends [ADR-0015](0015-consent-based-client-connection.md) to a second client and corrects its statement that Codex has no helper mechanism. Target: 1.0.1 (release in progress).

## Context
ADR-0015 gave Claude Code a consent-based, one-click connection and left Codex, Cursor and VS Code Copilot on a manual fallback, because their documented credential options were an environment variable present at start, or OAuth. The owner asked (D27, Issue #3) for the same experience with Codex: **Coding Agent: Codex → Connect Agent → Approve → new Codex chat → connected**, without copying a token, editing TOML or setting an environment variable.

What the vendor documents (read 2026-11-25, [client audit](../research/2026-11-25-codex-one-click-connection.md#what-codex-documents-and-what-was-observed)):
- `mcp_servers.<id>.http_headers_helper` is a `string (command)`: a "local command that prints a JSON object of HTTP header names and values. Supported only for locally connected HTTP MCP servers. Explicit bearer tokens and OAuth credentials take precedence over helper-provided Authorization headers."
- User configuration lives in `~/.codex/config.toml`.

What was **observed** on this Windows computer, with an isolated `CODEX_HOME` and a stand-in server (same record):
- **Supported by the installed engines:** Codex CLI 0.160.0 and the engine bundled in the Codex VS Code extension 26.1120.50957 (codex-cli 0.171.0-alpha.13.2; also present in 26.1117).
- **How the helper runs:** `cmd.exe /Q /D /C "<command>"`, with a reduced environment (20 variables; no `CODEX_HOME`, no `PSModulePath`).
- **When it runs:** once per connection (the headers are cached), and once more after an HTTP 401; the request is retried if the headers changed. A rotated key therefore works in a running Codex without a restart.
- **Failures:** a non-zero exit status or output that is not a JSON object of strings stops the connection before any request is sent. An empty object `{}` does not: Codex then sends its requests **without credentials** to whatever listens on the port.
- **Identity:** Codex's MCP client names itself `codex-mcp-client` and uses protocol 2025-06-18, where tool calls carry no client information.
- Not observed: macOS and Linux; the Codex desktop app; Codex in a remote or cloud execution environment (the documentation excludes the helper there).

## Decision
1. **Codex gets automatic setup on Windows**, through the same workflow, consent rules, audit records and undo as ADR-0015. Everything in ADR-0015 decisions 1, 2, 4, 6, 7, 9 and 10 applies unchanged.
2. **The entry** is one table in `~/.codex/config.toml` (or `%CODEX_HOME%\config.toml`):
   ```toml
   [mcp_servers.tdflow]
   # Added by TDflowMCP (Connect Agent). It holds no secret. Disconnect Agent in TouchDesigner removes it.
   url = "http://127.0.0.1:<port>/mcp"
   http_headers_helper = "<fixed command>"
   ```
3. **The helper is the ADR-0015 helper**: the same PowerShell command, the same DPAPI vault and the same listener proof (HMAC over a fresh nonce with the project's connect key). Two differences:
   - **Its own credential slot** (`connect\codex\<project id>.dpapi`), so Codex has its own agent identity, token and audit records next to Claude Code's.
   - **It fails closed by exiting with status 1 and printing nothing.** Claude Code's helper prints `{}`; for Codex that would mean an unauthenticated connection to whatever holds the port.
   The Claude Code helper is byte-for-byte unchanged, so existing connections are still recognised.
4. **The file is edited as text, never re-serialised.** TOML written by a person has comments and layout that a re-serialisation would destroy, and no TOML writer is bundled.
   - Only TDflowMCP's table is added (appended after one blank line), updated (its `url` and `http_headers_helper` lines) or removed (the table and its sub-tables). Every other byte stays, including the line endings and a byte-order mark.
   - **Fail closed by re-parsing.** The edited text is parsed (`tomllib`), and it must equal the file as it was except for `mcp_servers.tdflow`, which must be exactly what the action intends. This is checked as a dry run before the user is asked, again before writing, and by reading the file back. Anything else is refused with `CONFIG_UNSUPPORTED` or `CONFIG_INVALID` and nothing is written: invalid TOML, a `tdflow` entry defined as an inline table, with dotted keys or twice, a file that is not UTF-8.
   - The ADR-0015 change discipline applies as it is: a linked file is refused; a backup goes to private storage first; the file is re-checked right before the atomic replace; a concurrent writer is never overwritten; `CONFIG_CHANGED` asks again when the approved action is no longer the same.
   - **Review 23, also applied to Claude Code:** every plan made after the approval, including one made after a failed write, is compared with the approved action before anything is written; and a failed read-back puts the file back only if it is still exactly what TDflowMCP wrote.
   - Only `\n` ends a line (TOML's rule), so other Unicode line separators inside comments and strings are ordinary characters.
   - The text of a replaced table is kept only if it parses to exactly that table; otherwise the replacement is refused.
5. **Ownership.** A `tdflow` table is TDflowMCP's only if its `url` and helper equal the generated ones (port aside) **and** every other key is one of the documented preference keys (an allowlist). Any other key makes the table someone else's: another transport, another credential source (`bearer_token_env_var`, `http_headers`, `env_http_headers`, `auth`, OAuth keys), which Codex gives precedence over the helper, an execution environment, or a key this version does not know.
   - Preferences Codex or the user added to TDflowMCP's table (`enabled`, timeouts, tool lists, per-tool approval modes) are kept on update and go with the table on Disconnect.
   - A table that is not ours is replaced only with the user's approval of a plan that says **REPLACES**; its text is kept in the private connection record and put back on Disconnect. This covers a `tdflow` entry from the earlier manual setup.
6. **Undo is exact.** Disconnect Agent removes the table only if it is still ours. A file that was only extended is byte-identical to the original afterwards, including a missing final newline; a file the connection created is deleted again when nothing else is in it.
7. **Several clients.** Each automatic client has its own connection record, agent and credential. The per-project connect key is shared and stays while any record remains. Under Auto, when several agents can be set up automatically, the user is asked which one.
8. **Discovery** stays read-only and reports one Codex row for the CLI, the editor extensions and the configuration folder, because they share one configuration. The extension's version is read from its folder name; an extension older than the first tested build, with no CLI, keeps the manual fallback. The desktop app is not listed separately: no install marker was verified.
9. **Connected client identity.** A legacy-era `initialize` records the client's self-declared name and version for the Connected Agents line and the `verified` audit record, as the modern era already did per call. It is display text, bounded and allowlisted, never trusted, and only for an authenticated caller.
10. **The manual fallback stays** for Codex where automatic setup is not available (other platforms, an extension that is too old) and says why.

## Security boundaries (what this ADR does not change)
- Everything listed under the same heading in ADR-0015: server-side enforcement, MCP off by default, no reusable secret in the project, the `.tox`, parameters, DATs, instructions, memory, Git, URLs, logs or client files.
- Same-user malware remains out of reach of any local design, as before.
- A process squatting the port gets nothing from the helper, and with Codex it does not even receive a request.
- **Not addressed:** a Codex version that does not know `http_headers_helper` ignores the key and connects without credentials. The real listener answers 401 and discloses nothing. While TouchDesigner is closed, a process squatting the port would be reached by such a Codex without any credential, exactly as by a client that was configured by hand; it receives no TDflowMCP token. TDflowMCP shows `No key sent: update Codex`, and the plan and View Connection Details say that Codex must be updated. Auto cannot read the CLI's version (it never runs or opens anything), and the vendor does not document the minimum version.

## Alternatives
- **Keep the manual fallback** (`bearer_token_env_var`): the owner's request is to remove exactly those steps.
- **Run `codex mcp add`:** it executes a discovered program, is absent for extension-only users and cannot set `http_headers_helper` (its flags are `--url`, `--bearer-token-env-var` and OAuth options).
- **A TOML library that preserves formatting:** a new runtime dependency (ADR needed, invariant: no third-party packages). Text editing with re-parse verification gives the same guarantee for the one table TDflowMCP owns.
- **Re-serialise the file:** loses comments and layout in a file people edit by hand. Rejected.
- **Print `{}` on failure, as for Claude Code:** Codex would then talk to the port without credentials. Rejected for Codex.
- **OAuth 2.1:** still deferred ([security model §2.6](../architecture/security-model.md#26-oauth-21)); no longer needed for Codex on Windows.

## Validation gates
- **Windows, real Codex engines: passed** 2026-11-25 ([validation index](../icm/validation-index.md)). With a disposable client home and the configuration TDflowMCP wrote, used unmodified, the Codex VS Code extension's engine and the Codex CLI's engine list the tools, call `tdflow_whoami` as the Codex agent, create an operator in scope and are refused outside it; Claude Code works next to it with its own identity; revocation, repair without restarting Codex, a TouchDesigner restart and Disconnect behave as decided.
- **Independent security review:** see [review 23](../research/2026-11-25-codex-one-click-connection.md#independent-review-23-security).
- **Owner's real profile and the Codex chat in VS Code: passed** 2026-11-25, **reported by the owner** (no agent executed or observed it; [Issue #3](https://github.com/jlopesmo/TDflowMCP/issues/3#issuecomment-7175371704)). With `1.0.1rc1` in a saved disposable project: OpenAI Codex selected, Connect Agent approved, a Codex chat in VS Code returned a successful `tdflow_whoami` (Builder role, `/project1/**` scopes), TouchDesigner showed `Agent Connected — Ready` and `Codex 0.171.0`, and the agent inspected the network without changing anything.
- **macOS, Linux, the desktop app, remote execution: not validated and not offered.**

## Consequences
- Codex users on Windows need no knowledge of MCP, tokens, TOML or environment variables.
- `agent-integration.md` §3, `security-model.md` §2.6 and §10, `setup-assistant.md` and the install guide describe the new behaviour.
- A new in-TouchDesigner suite drives real Codex engines; it needs Codex installed on the test computer and no Codex sign-in.
