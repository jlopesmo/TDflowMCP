# One-click agent connection and final onboarding redesign (2026-11-24)

**Owner direction D22** ([roadmap](../icm/roadmap.md#owner-decisions-2026-11-22)). The owner's acceptance of `1.0.0rc2` found that a first-time user still had to understand tokens, clipboard expiry, environment variables, MCP configuration files, HTTP transport and the differences between client interfaces. Target: **drop the `.tox` → check ready → Connect Agent → approve → start working**. Decision: [ADR-0015](../decisions/0015-consent-based-client-connection.md). Version **1.0.0rc3**.

## Client audit
Sources: the vendors' documentation, fetched 2026-11-24; local read-only checks (names only, never values).
- Claude Code: code.claude.com/docs/en/mcp, mcp-quickstart, vs-code, settings.
- Codex: learn.chatgpt.com/docs/extend/mcp and config-reference; developers.openai.com/codex now redirects there.
- Cursor: cursor.com/docs/context/mcp.
- VS Code Copilot: code.visualstudio.com/docs/agents/reference/mcp-configuration and copilot/customization/mcp-servers.

"Observed" means executed on this Windows 11 machine.

| Client / interface | Detection (read-only) | Configuration location | Transport | Credential support | Automation | Reload | Verification | Windows / macOS | Rollback |
|---|---|---|---|---|---|---|---|---|---|
| **Claude Code CLI** | `claude` on PATH | `~/.claude.json` top-level `mcpServers` (user scope; `$CLAUDE_CONFIG_DIR/.claude.json` when set). Local scope: `projects[path].mcpServers`. Project: `.mcp.json` (trust prompt) | Streamable HTTP | `headersHelper`: runs on each connection and after a 401; user scope has no trust prompt; observed through `cmd.exe` on Windows. `${VAR}` in headers: credential-named variables read as empty for remote servers (docs) | **Yes (ADR-0015)** | New session, or `/mcp` → Reconnect | `claude mcp list` (✔ Connected / ✘ Failed); the server sees an authenticated request | Windows **observed** (2.1.289). macOS: implemented (sh + Keychain), **not validated** | Disconnect: remove the entry (or restore the replaced one), delete the credential, revoke the tokens |
| **Claude Code VS Code extension** | `~/.vscode/extensions/anthropic.claude-code-*` (folder name). The extension also installs into Cursor/Windsurf hosts | **Same** user-scope file as the CLI (docs: "the dialog and the terminal command save to the same MCP configuration") | same | same helper | **Yes** (same entry) | "Conversations you start afterwards" (docs) | The bundled engine `resources/native-binary/claude.exe` (2.1.330) runs `mcp list` against the written file: **observed** ✔. The chat UI: owner acceptance step | Windows **observed** (engine). macOS: not validated | same |
| **OpenAI Codex** (CLI, IDE extension, desktop share `~/.codex/config.toml`, `CODEX_HOME`) | `codex` on PATH; `openai.chatgpt-*` extension folder | `[mcp_servers.tdflow]` `url`, `bearer_token_env_var` (or `http_headers`, `env_http_headers`); `codex mcp add tdflow --url … --bearer-token-env-var …` (local `codex-cli 0.160.0` help) | Streamable HTTP | An env var present when Codex starts, static headers (plaintext), or OAuth (`codex mcp login`, keyring) | **No:** no secure automatic option without OAuth. Manual fallback | IDE: Restart extension; CLI: new session | not live-tested | no OS-specific difference documented | remove the table / `codex mcp remove` |
| **Cursor** | `cursor` on PATH or `~/.cursor` | `~/.cursor/mcp.json` (all projects), `.cursor/mcp.json` | HTTP (`url`, `headers`) | `${env:NAME}` in headers, or OAuth (PKCE, DCR) | **No** (same reason). Manual fallback | Restart Cursor or toggle the server | not live-tested | — | remove the entry |
| **VS Code GitHub Copilot** | `github.copilot*` extension folder under `.vscode` | Copilot harness: `~/.copilot/mcp-config.json` (`COPILOT_HOME`, shared with the Copilot CLI). Deprecated editor format: profile `mcp.json` with `inputs` (password prompt, "securely stored"), removal planned Feb 2027 | HTTP | `${NAME}` env var (must be in VS Code's environment at start), the deprecated `${input:…}` prompt, or OAuth | **No.** Manual fallback | Autostart on the next chat; trust prompt for non-workspace sources | not live-tested | — | remove the entry |
| Other MCP client | — | client-specific | Streamable HTTP + Authorization header | client-specific | **No** | — | — | — | — |

**Findings that shaped the design:**
- An environment variable set in PowerShell is **not** visible to a running VS Code extension. That is the rc2 friction.
- Claude Code reads credential-named variables as empty in remote headers, so `${TDFLOW_TOKEN}` is fragile there anyway.
- Only Claude Code offers a credential mechanism that needs no secret in a file or the environment.
- Grok remains unlisted: no compatible client and configuration method is verified.

## Delivered
- **Connect Agent** runs the whole workflow ([ADR-0015](../decisions/0015-consent-based-client-connection.md) §1–7):
  - Auto discovery, with a chooser when several agents are found (here: Claude Code, Codex, Cursor and Copilot folders);
  - a plan in a TouchDesigner dialog, with details in the `connect_view` DAT;
  - approval → identity and credential → DPAPI/Keychain → one user-scope `tdflow` entry with `headersHelper` → Enable MCP (only if approved) → wait for the first authenticated request → **Agent Connected — Ready**.
  - Visible stages (each fits the default parameter window): `Preparing connection…`, `Approve in the dialog.`, `Configuring Claude Code…`, `Turning on MCP…`, `Start a Claude Code chat.`, `Agent Connected — Ready`.
  - Failures give a short reason and a recovery step.
- **Restart:** after TouchDesigner restarts (MCP off, as always), Next Step says `Turn on Enable MCP.`. Connect Agent then asks only to turn on MCP; nothing is set up again.
- **Agents page:**
  - Installed Agents (what was found, automatic or manual);
  - Repair Connection (new credential, plan shown first);
  - Disconnect (asks first; rollback);
  - Copy Token / New Token / Revoke Tokens as the manual fallback.

  **Advanced:** View Connection Details (file, entry, helper, credential store, backups; no secrets).
- **Diagnostics (UX-01):**
  - Run Diagnostics shows `Running diagnostics…`, then `Checks done hh:mm:ss`;
  - System Check shows the result and the non-zero counts; View Diagnostics lists passed, warnings, failed and pending;
  - new **Last Check** (time, automatic or manual);
  - pending checks are never counted as passed.
- **Instructions (UX-02, 03, 05, 06, 07):**
  - client- and platform-specific; Claude Code has no token step;
  - the manual fallback is 4 short steps in this platform's shell;
  - no "open the Setup page" step;
  - authorized configuration is distinguished from silent modification;
  - Next Step lines ≤ 26 characters (unit-tested; see the visual review).
- **Browser (§8 of the brief):** the fragment ticket is retired. Pages opened from TouchDesigner pair through a TouchDesigner dialog showing the match code (ADR-0015 §8).
- **Connected Agents** shows the client's own name and version (`Claude Code 2.1.289 · 3 s`), allowlisted characters only.

## Real-world and simulated tests
See the [validation index](../icm/validation-index.md) for runs and counts.

**Real-client validation** (`tests/td/run_td_native_tests.py`): a disposable project and a disposable client home (`TDFLOW_CLIENT_HOME`) that holds another MCP server, unrelated settings and an installed-extension folder. The real user profile is never read or written.
1. The configuration TDflowMCP wrote, unmodified, is used by:
   - the Claude Code CLI (`CLAUDE_CONFIG_DIR` = that home, so the file is its user-scope configuration);
   - the binary bundled in the installed VS Code extension;
   - a live `claude -p` session given the same entry, with no token anywhere in its environment.
2. The session runs whoami, lists `/project1/fx`, creates `probe1` there and is refused outside the scope (`RESULT role=builder created=yes outside=denied`).
3. Suspend refuses and resume works.
4. Restart from the saved project: reconnects with the same credential and no new setup.
5. Revoke Tokens: the client reports `HTTP 401` from the helper's header; Repair Connection reconnects with a new credential.
6. An invalid client file fails cleanly and is left untouched.
7. Disconnect removes exactly the entry. Claude Code had meanwhile rewritten its own state in the file, and that was kept.
8. No plaintext token was found in any file the run produced, including the saved `.toe`.

**Simulated configuration tests** (`tests/unit/test_connect.py`, temporary directories):
- add/update/replace/unchanged and undo;
- a concurrent client rewrite (kept) versus a concurrent `tdflow` change (refused);
- invalid JSON untouched; bounded backups; `CLAUDE_CONFIG_DIR`;
- DPAPI blob not plaintext; macOS secret only on stdin;
- discovery reports no paths.

The real Windows helper, run through `cmd.exe`, is tested against a loopback stand-in for this and another project and for malformed answers. A copy of the owner's real `~/.claude.json` round-trips byte-identically through add and undo; it was copied to a temporary folder, never modified, and only booleans were printed.

**Not executed:**
- the owner's real profile with the VS Code chat UI (owner acceptance step);
- macOS (no Mac);
- live Codex/Cursor/Copilot connections (manual fallback, documented only).

## Independent review 19 (security)
A separate agent reviewed the uncommitted tree read-only. It ran the unit tests and ran the generated helper through `cmd.exe` against a stub listener with a fake token. It found **no plaintext token at rest, on a command line or in the environment**, and 0 High findings.

| # | Severity | Finding | Fix (verified by) |
|---|---|---|---|
| 1 | Medium | The helper released the token of whichever project the listener named. A process squatting the port while TouchDesigner was closed could harvest every connected project's token (the helper re-runs after a 401) | **Mutual authentication:** a per-project 256-bit connect key (private store + vault). The helper sends a fresh nonce to `/.well-known/tdflowmcp-proof/<nonce>` and releases the token only for a correct HMAC (`test_a_squatter_gets_nothing`: no key, wrong proof, other key; native `connect.LISTENER_PROVES_THE_PROJECT_KEY`; live clients connect through it) |
| 2 | Medium | The header went through PowerShell's pipeline, so GPO transcription or module logging could record the token | `[Console]::Out.Write` (not a cmdlet, not transcribed); the token is never passed to a cmdlet (unit test asserts the form) |
| 3 | Medium (availability) | Started from a PowerShell 7 terminal, Windows PowerShell autoloaded the wrong modules and the helper printed `{}` (reproduced) | `$env:PSModulePath=$PSHOME+'\Modules'` first (`test_works_when_started_from_powershell_7`) |
| 4 | Medium | Disconnect edited the client file before revoking, so a file error left a working credential; API statuses were not checked | Revoke, disable and credential delete first, each checked; the file afterwards, best effort; `partial` audited and reported |
| 5 | Low–Med | A failure after the record or audit step, or an unconfirmed write, could leave a working credential or an orphan entry | Everything after token creation is in the rollback: credential, key file and the entry this run wrote; an unconfirmed write restores the file |
| 6 | Low–Med (macOS) | Temporary files with umask permissions; the replaced file lost its mode; backups readable by other macOS users | Exclusive, no-follow, `0600` temporary files; the original mode is kept; private writes are owner-only. **Pre-existing, recorded:** on macOS the private root is `~/TDflowMCP` with default permissions (not validated on a Mac) |
| 7 | Low | The replaced entry could be lost once its backup was pruned | The replaced entry is kept in the private connection record and restored from there (unit test with the backups deleted) |
| 8 | Low | Ownership was a substring test, so a user's own helper mentioning TDflowMCP could be deleted; the entry is shared by projects | Ours means equal to the generated template apart from the port. Disconnect keeps the entry while another project still has a credential |
| 9 | Low | `_ps_quote` missed Unicode quote look-alikes; `!` and `^` are risky under cmd | Paths with `%`, `"`, `!`, `^`, line breaks or U+2018–201B are refused (unit test) |
| 10 | Low | Lone surrogates written by the client's encoder made writing fail; the rewrite was not mentioned | ASCII-escape fallback (unit test); the consent says when the file is rewritten |
| 11 | Low | Lost update between read and replace | The file's hash is re-checked right before `os.replace`, with re-plans and retries; a short retry on Windows sharing violations |
| 12 | Low | A linked `~/.claude.json` (dotfiles) became a regular file | Refused (`CONFIG_IS_LINK`, "Settings file is a link"); manual setup |
| 13 | Low | The 90-s pairing window could pop an attacker's code right after the user's click; one shared dialog | **Number matching** (the real code among two random decoys; a wrong pick denies); no pairing dialog over another open dialog |
| 14 | Low | `connect_view` (saved in the `.toe`) could keep the user-name path or a replaced entry's URL query | Cleared after approval or cancel and 2 minutes after display; paths shown as `%LOCALAPPDATA%`; replaced URLs without query or user info (unit test) |
| 15 | Note (macOS) | `security -i` status; curl proxies; `has()` runs before approval | `put` verifies with `has`; `--noproxy 127.0.0.1`, `-q`; the documentation now says a read-only lookup runs while planning |
| 16 | Note | Disconnect without a dialog had no confirmation | A second pulse within 2 minutes confirms, with the details shown |
| 17 | Note | The enable-only approval was audited asynchronously | Durable record on a background thread before Enable MCP |
| 18 | Note | The security model still described `/.well-known` as `{product, version}` | Route table updated (connect id, proof route) |

## Usability review
A separate agent reviewed every user-visible text and flow. Main findings and changes:
- **Auto showed a chooser for nearly everyone** (`code` on PATH counted as Copilot). Fixed:
  - Copilot is detected only by its extension folder.
  - With one agent that can be set up automatically, Auto recommends it without asking and names the others in the plan ("Also found: … manual setup").
  - The generated text covers only the agent being connected.
- **"Agent Connected — Ready" reverted** to "start a new chat" after 2 idle minutes. Now:
  - `Connected · idle N min`;
  - "none recently" under Connected Agents;
  - after 90 s without a request: `No request: /mcp reconnect`;
  - unauthenticated attempts: `No key: Repair Connection`;
  - a changed Port: `Port changed: pulse Repair`.
- **A failed step stuck and hid the reason.** Failures clear once MCP turns on or the agent talks, and the specific refusal note is shown first.
- **The plan expired under an open dialog.** No expiry while it is open; the no-dialog path opens the viewer.
- **The consent dialog was long and technical.** It now gives:
  - who gets access, the access level and scope;
  - the file, the one server added, the servers kept and the backup;
  - where the key is kept;
  - that Enable MCP is TURNED ON;
  - "Then start a new Claude Code chat", and how to undo.
  The technical lines are in View Connection Details.
- **Lines too long, and texts that still sent users to the Setup page.** Shortened and corrected, including controller messages and `troubleshooting.json`. The generated text now:
  - has no duplicated Off sentence;
  - uses "(manual setup)" headers with the reason on their own line, and an explicit Coding Agent selection;
  - checks "TouchDesigner open, Enable MCP on" first when `tdflow` fails.
- **Labels:** Disconnect Agent; View Agents next to the agent selector.
- **Not changed:**
  - The main page order follows the owner's brief (System, Connection, Agent Handoff, Management).
  - Writing repository memory still needs a token made on the Setup page (documented; optional feature).

## Visual review (TouchDesigner 2025.32440)
`tests/td/run_td_ux_screens.py` captures this process's windows; the images are git-ignored under `.tmp/`.
- **The default parameter window shows about 26 characters of a value.** Lines like `MCP ready. Save the projec…` and `Ready · 11 ok · 0 warn · 0…` were clipped. Every persistent line now fits 26 characters, unit-tested over all stages and failure lines:
  - Next Step: `Ready: pulse Connect Agent`, `Approve in the dialog.`, `Configuring Claude Code…`, `Start a Claude Code chat.`, `Agent Connected — Ready`, `Claude settings damaged`, …
  - System Check: `Ready · 8 ok · 3 pending` (zero warn/fail are left out; all counts are in View Diagnostics);
  - Connected Agents: `Claude Code 2.1.289 · 5 s`;
  - Installed Agents: `Claude Code (auto) +3 more`.
- **The TouchDesigner dialog was not opening.** The first visual run showed it, because a test stub also applied to the screens run. A probe confirmed the product's call (`op.TDResources.PopDialog.OpenDefault`) and its callback (`{'buttonNum': 1, …}`). The screens run now opens the real dialog, keeps TouchDesigner responsive (a pulse works while it is open) and approves through its real button handler.

## Limits
- Automatic setup covers Claude Code only. Codex, Cursor and Copilot need the manual fallback until TDflowMCP offers OAuth (an owner decision; [security model §2.6](../architecture/security-model.md#26-oauth-21)).
- macOS automatic setup is implemented but not validated.
- One `tdflow` entry per Claude Code configuration. The helper picks the credential of whichever project is listening on the port, so several projects work one at a time, each with its own identity.
- Claude Code reads its MCP configuration when a conversation starts: an already open chat needs `/mcp` → Reconnect or a new chat.
- Same-user malware can run the helper or read DPAPI/Keychain data as the user. That is not new, and is listed in ADR-0015.
