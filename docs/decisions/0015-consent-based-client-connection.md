# ADR-0015 — Consent-based coding-agent connection and OS-protected credentials

**Status:** Conditionally Accepted (owner D22, 2026-11-24), subject to the validation gates below. It amends [ADR-0011](0011-agent-integration-and-skills.md) decision 3 ("MCP client connection is instruction-only") for clients that support safe automatic setup. It also replaces the rc2 one-click browser ticket of [security model §2.5](../architecture/security-model.md#25-dashboard-and-setup-admin-sessions).

## Context
The owner's acceptance of `1.0.0rc2` found first-run setup unacceptable as the default. Users had to understand agent tokens, clipboard expiry, environment variables, MCP configuration files, HTTP transport, Claude Code's MCP settings, and how VS Code differs from the CLI. The owner directed (D22) the workflow **drop the `.tox` → check ready → Connect Agent → approve → start working**. TDflowMCP may configure a coding agent **only after explicit approval inside TouchDesigner**, while every existing security guarantee stays.

What the vendors document, and what was observed on 2026-11-24 ([client audit](../research/2026-11-24-one-click-connection.md#client-audit)):
- **Claude Code:**
  - the CLI and the VS Code extension share one configuration (user scope: `~/.claude.json` top-level `mcpServers`);
  - a `headersHelper` command produces request headers on every connection, and again after a 401;
  - user-scope helpers need no project trust prompt.
  - Observed: on Windows the helper runs through `cmd.exe`, for CLI 2.1.289 and for the extension's bundled 2.1.330.
- **Codex, Cursor, VS Code Copilot:** there is no helper mechanism. Credentials are either an environment variable that must exist when the client starts, or OAuth. TDflowMCP does not implement OAuth.

## Decision
1. **Connect Agent is the guided workflow.**
   - Steps: environment check → agent (Auto: read-only discovery; a choice when several are found) → a plan the user reviews → approval → configure → turn on MCP only if the approved plan said so → wait for the agent's first authenticated request.
   - Each stage is shown in Next Step. **Connected** means an authenticated request arrived after the configuration, never a written file or a created token.
2. **Consent.** Nothing outside TDflowMCP changes before the user approves, in a TouchDesigner dialog. Without a dialog, a second Connect Agent pulse within two minutes approves. The plan shows:
   - the agent and the interfaces found;
   - the file (without the user name) and the exact change;
   - what is preserved;
   - the identity (access level, scope);
   - where the credential goes;
   - whether Enable MCP will be turned on;
   - how to undo.

   Rules:
   - The plan expires after two minutes.
   - Applying it re-reads the file. If the approved change is no longer the same one (for example, someone else's `tdflow` entry appeared), it stops with `CONFIG_CHANGED` and asks again.
   - Nothing outside TouchDesigner can start, approve or apply a connection.
3. **Claude Code (automatic on Windows and macOS).**
   - The entry: one user-scope entry `mcpServers.tdflow` = `{type: http, url: http://127.0.0.1:<port>/mcp, headersHelper: <fixed command>}`.
   - The helper reads `/.well-known/tdflowmcp` on loopback. The new `connect` field is the project's non-secret storage id, so the helper picks **that project's** credential.
   - **Mutual authentication (review 19):** before releasing the token, the helper sends a fresh random nonce to `/.well-known/tdflowmcp-proof/<nonce>`. The listener answers with HMAC-SHA256(connect key, `tdflowmcp-proof|<project id>|<port>|<nonce>`). The per-project connect key is 256-bit, kept in TDflowMCP's private store and next to the token in the OS-protected store.
     - Only the TouchDesigner instance holding that key can answer. A process squatting the port while TouchDesigner is closed gets `{}`, whichever project id it names.
     - Otherwise the helper prints `{}`. No token is in the file, a command line or the environment.
   - **Windows:**
     - PowerShell 5.1, by absolute path, using `-Command`, so the execution policy does not apply.
     - `PSModulePath` is pinned to Windows PowerShell's own modules, because a PowerShell 7 parent breaks autoloading.
     - The header is written with `[Console]::Out.Write`, which transcription and module logging do not capture.
     - In Constrained Language Mode the HMAC check is unavailable, so the helper fails closed.
   - **macOS** (not validated): `/bin/sh`, `curl --noproxy`, `security`. Perl's `Digest::SHA` receives the key through its environment, not its command line.
4. **Credentials.** The token goes from the admin backend straight into the user's protected store, on a background thread. It never reaches a parameter, a DAT, the clipboard, a log, the audit or the client file.
   - **Windows:** a DPAPI current-user blob file under the private per-user root (`%LOCALAPPDATA%\TDflowMCP\connect\<client>\<project id>.dpapi`).
   - **macOS:** the login Keychain, written through `security -i` with the command on stdin, never on argv.
   - Other platforms: no automatic setup.
   - Each project has its own agent identity and credential. Rotation (Repair Connection) and revocation (Revoke Tokens, Disconnect) stay as before.
5. **Change discipline.**
   - Exactly one key changes; other servers and settings are preserved. A file that is not valid JSON or not UTF-8 is never touched.
   - Ownership: an entry is TDflowMCP's only if it equals the generated template apart from the port, so a user's own `tdflow` helper is never treated as ours.
   - The file is re-checked right before it is replaced, with retries; a concurrent writer is never overwritten silently. A linked file (dotfiles) is refused.
   - Temporary files are created exclusively, without following links, owner-only; the replaced file keeps its mode. A failed read-back restores the file.
   - A backup goes to private storage first (the last five).
   - The write is atomic and is read back to validate. The entry is re-checked a few seconds later, in case the client rewrote its file.
   - **Undo (Disconnect), review 19:**
     1. Revokes the tokens, disables the agent and deletes the credential first, checking every step.
     2. Then removes the entry if it is still TDflowMCP's and no other project still has a credential for it (the entry is shared). It restores the entry it had replaced, kept in the private record so pruned backups cannot lose it, and removes an `mcpServers` key it created.
     3. A partial result is audited as `partial` and reported.
   - **Rollback:** a failure at any step after the credential exists revokes it, deletes the stored key and removes the entry that this run wrote.
6. **Other clients keep a minimal manual fallback** (Agents page > Copy Token, one environment-variable command, one registration command or entry). The precise limitation is stated per client. No insecure workaround is built: no plaintext token in their files, no persistent user environment variable.
7. **Audit.** `client_connect` and `client_disconnect` are recorded:
   - intent first (durable), then `ok`, `failed`, `cancelled` or `verified`;
   - the existing `create_token` / `rotate_token` / `revoke_token` records come from the same backend.

   A failure after the credential was created revokes it again (rollback).
8. **Browser sign-in.** The rc2 fragment ticket is retired; its route is no longer served.
   - Why: it was visible on the browser's command line and stayed redeemable if the real page never loaded.
   - A page opened from TouchDesigner carries no secret (`#pair`) and requests pairing at once.
   - For 90 s after the user's Open pulse, the request pops a TouchDesigner dialog with the match code, and Approve decides only that code.
   - **Number matching (review 19):** the dialog offers the real code among two random decoys, and the user picks the code the page shows. A wrong pick denies the request.
   - No dialog opens while another TouchDesigner dialog is open; the request then waits on the Web page.
   - Typed URLs pair through the Web page as before.
9. **Visible text** (owner D22 visual review): TouchDesigner's default parameter window shows about 26 characters of a value. Next Step, System Check, Connected Agents and Installed Agents lines fit that, which is unit-tested. Details are in the viewers, `connect_view` (cleared after 2 minutes, because it is saved with the project) and View Diagnostics.
10. **Discovery stays read-only:**
   - names on PATH (`shutil.which`) and extension/configuration folder names;
   - nothing is run, no configuration is read during discovery, no paths are reported.

   The only programs the connection workflow starts are the fixed macOS `/usr/bin/security` commands, from the TD adapter: a read-only lookup while the plan is made, and the store after approval. The core never starts processes.

## Security boundaries (what this ADR does not change)
- Server-side enforcement, roles, scopes, approvals, hard denies, the kill switch and the bind-verification gate are unchanged.
- MCP stays off by default, and Enable MCP is never turned on silently: only the explicit approval of a plan that says so.
- P-9 and P-15 are kept. No reusable secret is in the `.toe`, the `.tox`, parameters, DATs, generated instructions, project memory, Git, URLs, logs or client files. DPAPI blobs and Keychain items live in the private per-user store.
- Same-user malware remains out of reach of any local design. It could run the helper itself, or read DPAPI/Keychain data as the user, exactly as it could read any other credential the user's clients hold. The design closes the exposures that do not need code execution as the user: plaintext in config files that get shared or synced, command lines, environment inheritance and the clipboard.
- **A process squatting the port** receives nothing from the helper: it cannot answer the proof (review 19, finding 1). It still sees whatever a client sends it directly, which is no TDflowMCP token. A process that had obtained a token some other way could replay it, and the bind gate and `PORT_IN_USE` handle the real listener's side.

## Alternatives
- **A literal token in `~/.claude.json`:** simplest, but plaintext in a file users paste and sync. Rejected.
- **A persistent user environment variable** (HKCU) plus `${TDFLOW_TOKEN}`: inherited by every process, not available to an already-running VS Code, and plaintext in the registry. Rejected. Also, Claude Code reads credential-named variables as empty in remote headers.
- **OAuth 2.1 (DCR + PKCE)** on loopback: supported by every client, but it needs an authorization server, a browser round-trip and a client-side "Authenticate" step. Deferred ([security model §2.6](../architecture/security-model.md#26-oauth-21)); it would be the route to automatic setup for Codex, Cursor and Copilot.
- **Running `claude mcp add-json`:** it executes a discovered program and is absent for extension-only users. Direct, minimal editing was chosen.
- **Keeping the browser ticket** with warm-up and race detection: its residual risk was documented. Pairing approved in TouchDesigner has none of it.

## Validation gates
- **Windows, real clients: passed** 2026-11-24 ([validation index](../icm/validation-index.md)). On a disposable client home, with the configuration TDflowMCP wrote used unmodified:
  - Claude Code CLI 2.1.289 and the VS Code extension's engine 2.1.330 connect (`claude mcp list` ✔ Connected);
  - a live session inspects, creates an operator in scope and is denied outside;
  - restart reconnects without setup; revocation shows as a client failure (HTTP 401); repair reconnects;
  - Disconnect removes exactly the entry;
  - no token was found in any file.
- **Owner's real profile and the VS Code chat UI: pending.** This is the owner's acceptance step: it requires the owner's own approval in TouchDesigner and a new chat in the VS Code extension.
- **macOS: not validated.** Implemented from documentation and not claimed as working until tested on a Mac.
- **Independent security and usability review:** see [review 19](../research/2026-11-24-one-click-connection.md#independent-review-19-security).

## Consequences
- The default path for Claude Code users needs no knowledge of MCP, tokens, JSON or environment variables.
- `security-model.md` §2.5 and §10, `agent-integration.md` §1/§3 and `setup-assistant.md` §2 describe the new behaviour. Invariant 9 already permits previewed, confirmed writes, so it is unchanged.
