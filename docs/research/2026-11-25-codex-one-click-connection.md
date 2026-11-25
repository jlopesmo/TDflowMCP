# One-click OpenAI Codex connection (owner D27, GitHub Issue #3, 1.0.1rc1)

**Status:** implemented on `dev` and verified on Windows with TouchDesigner 2025.32440 and real Codex engines. Decision: [ADR-0017](../decisions/0017-codex-one-click-connection.md). Evidence: [validation index](../icm/validation-index.md). Not released: `main` and the published v1.0.0 are unchanged.

The goal: **Coding Agent: Codex → Connect Agent → Approve → new Codex chat → connected**, with the same consent, credential and undo rules as Claude Code ([ADR-0015](../decisions/0015-consent-based-client-connection.md)).

## What Codex documents, and what was observed

### Documentation (read 2026-11-25)
- [Config reference](https://learn.chatgpt.com/docs/config-file/config-reference), quoted: "`mcp_servers.<id>.http_headers_helper` | `string (command)` | Local command that prints a JSON object of HTTP header names and values. Supported only for locally connected HTTP MCP servers. Explicit bearer tokens and OAuth credentials take precedence over helper-provided Authorization headers."
- Same page, quoted: "User-level configuration lives in `~/.codex/config.toml`."
- The [MCP page](https://learn.chatgpt.com/docs/extend/mcp) was fetched, but only a summary of its helper section was available, so nothing is quoted from it. The summary agreed with what was observed below.
- **Not stated by the vendor:** how the command is executed, its environment, its timeout, a minimum Codex version, and an example.

### Installed clients (inspected read-only)
- Codex CLI **0.160.0** (npm package `@openai/codex`).
- Codex VS Code extension **26.1120.50957**, whose bundled engine reports `codex-cli 0.171.0-alpha.13.2`. Two older installed builds (26.1117, 26.1120.41243) report `0.171.0-alpha.13` and `.13.2`.
- Both engine binaries contain the key name `http_headers_helper`.
- No Codex desktop app was found on this computer, so it was not tested and Auto has no marker for it.
- The owner's real `~/.codex/config.toml` was inspected by key names only: it has no `mcp_servers` table. It was never written; every test checks its hash before and after.

### Observed behaviour (isolated `CODEX_HOME`, a stand-in MCP server that logs requests)
The engines were driven through `codex app-server`, the process the VS Code extension itself runs. Its `mcpServerStatus/list` makes the engine connect to its MCP servers, and `mcpServer/tool/call` makes it call one tool. Neither needs a Codex sign-in or a model.

| Question | Observed, CLI 0.160.0 and extension engine 0.171.0-alpha.13.2 |
|---|---|
| How is the helper started? | `"C:\windows\system32\cmd.exe" /Q /D /C "<command>"`. cmd.exe strips the outer quotes; inner quotes and `%SystemRoot%` work. `$env:…` and single quotes are not interpreted |
| Working directory | The Codex home (extension engine); another folder (CLI). Not relied on |
| Environment | 20 variables: `APPDATA`, `COMSPEC`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `PATH`, `PATHEXT`, `PROGRAMDATA`, `PROGRAMFILES`, `PROGRAMFILES(X86)`, `PROGRAMW6432`, `PROMPT`, `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `TMP`, `USERDOMAIN`, `USERNAME`, `USERPROFILE`, `WINDIR`. No `CODEX_HOME`, no `PSModulePath`, nothing from the parent's own environment |
| When does it run? | Once per connection; the headers are cached |
| After an HTTP 401 | It runs once more. If the headers changed, the request is retried and succeeds; if not, the call fails with the 401 |
| A new key while Codex runs | The next call gets a 401, the helper runs, the retry works. No restart |
| Helper prints `{}` | The engine sends `initialize` **without an Authorization header** |
| Helper exits with status 1 | "MCP HTTP headers helper exited with status exit code: 1". **No request is sent** |
| Helper prints text that is not JSON | "MCP HTTP headers helper must output a JSON object of strings". No request is sent |
| Helper writes to stderr and exits 0 | Ignored; the connection works |
| Client identity | `clientInfo` name `codex-mcp-client`, title `Codex`, its version; protocol `2025-06-18`; `User-Agent: codex-mcp-client/<version>` |
| The engine's own files | It writes databases and logs into its home, and does not change `config.toml` |

The probe scripts were throwaway files under `.tmp/`; the durable versions of these checks are the unit tests and the Codex suite below.

## What was built
- **Core** (`src/tdflowmcp/connect.py`, no `td`):
  - `codex_entry`: the table's two keys. The helper is `helper_command(..., fail="exit")`: the ADR-0015 command with `catch{exit 1}` instead of `catch{[Console]::Out.Write('{}')}`. The Claude Code command is unchanged.
  - `plan_codex`, `apply_codex`, `remove_codex`, `codex_still_configured`: the file is edited as text and verified by re-parsing ([ADR-0017](../decisions/0017-codex-one-click-connection.md) decision 4).
  - `codex_is_ours`: ownership (decision 5).
  - `discover`: one Codex row (CLI, extensions with their version from the folder name, configuration folder); automatic on Windows.
  - `client_entry`, `client_config_path`, `plan_client`, `apply_client`, `remove_client`, `client_still_configured`: one interface for both clients.
- **Workflow** (`src/td_adapter/connection.py`): a `codex` credential slot; failure lines per client (`Codex settings damaged`, `Settings need manual setup`); the Next Step line follows the client being connected and counts only that client's agent while waiting; View Connection Details names the cause when Codex cannot use the helper.
- **Client identity** (`protocol.py`, `engine.py`): a legacy `initialize` records the client's name and version for display.
- **Text** (`tellagent.py`, `clients.json`): Codex instructions for the automatic path, and the manual fallback with its reason; the registry entry is `Codex` (was `OpenAI Codex CLI`, which did not fit the 26-character status lines).
- **Not changed:** the Skill (it is client-neutral), the tool surface, the capability list, permissions, the audit format.

## Tests
- **Unit** (`tests/unit/test_connect_codex.py`, 36 tests): discovery; the entry and its command; ownership; add, update, replace and remove on a realistic file with LF and CRLF, with and without a byte-order mark and a final newline; undo is byte-identical; header look-alikes inside strings and arrays; six layouts that are refused; invalid files; concurrent writers, also at the last moment before the replace; Unicode line separators; a linked file; `CODEX_HOME`; the consent text and the short plan per action. **The real helper** runs through `cmd.exe /Q /D /C` with Codex's 20-variable environment against a stand-in listener and a real DPAPI vault: the right project gets its token; a squatter, a wrong proof, another key, another project, a bad answer and a missing credential all give status 1 and no output.
- **In TouchDesigner with real engines** (`tests/td/run_td_codex_tests.py`, two launches, a disposable client home and project):
  1. Codex selected: review, cancel (nothing changed), approve. The table is appended and every other byte kept; the credential is in its own slot; nothing of Claude Code is touched.
  2. **The extension's engine and the CLI's engine**, each with `CODEX_HOME` set to the client home: they list the `tdflow` tools through the written configuration, `tdflow_whoami` returns the Codex agent with role Builder and scope `/project1/fx/**`, an operator is created in scope, and one outside is refused with `DENIED` and audited. `codex mcp list` shows the server and no secret.
  3. TouchDesigner shows `Agent Connected — Ready` and `Codex <version>`; the audit has `verified`.
  4. Claude Code is connected next to it (its own file, agent and key); `claude mcp list` reports Connected and both agents answer under their own identity.
  5. The Codex key is revoked: the running engines' next call fails, Claude Code stays connected. Repair Connection: the **same engine processes** work again without a restart.
  6. Launch 2, the saved project in a new TouchDesigner process: both agents are known but not loaded; Enable MCP alone; newly started engines reconnect with the stored key; no duplicate agent or key; the audit chain continues.
  7. Damaged and unsupported settings are refused and left as they were.
  8. Disconnect Agent for Codex: the file is byte-identical to the original; its key is deleted and revoked; Claude Code is not touched and still connects.
  9. No token is in any file, including the engines' own databases and logs, the saved project and the engines' output. The owner's real Codex configuration is unchanged.
- **Visual review** (`run_td_ux_screens.py`): the real chooser and the real Codex plan dialog are captured.
  - **Found and fixed:** the chooser's `Claude Code` button was cut off on both sides (the buttons show about seven characters); it now says `Claude`, and the text carries the full names.
  - The Codex plan dialog has 18 lines, as many as the Claude Code one, with both buttons visible.
  - The capture script was not DPI-aware: on a display scaled to 125 % it captured windows magnified and clipped, which had hidden exactly this kind of defect. It now reads sizes in real pixels.
- **Simulated versus observed:** the unit tests simulate the configuration file and the listener. The suite observes real Codex engines and a real Claude Code CLI. **Not observed by any agent:** the chat window of the VS Code extension, and a model deciding to call the tools. That is the owner's acceptance step ([checklist](../operations/release-candidate-1.0.1.md)).

## Independent review 23 (security)
A separate agent reviewed the uncommitted change read-only, with experiments against temporary directories. It did not run TouchDesigner, Codex, Claude Code or the helper. **0 High, 1 Medium, 9 Low, 2 Info.** It found no path that leaks the token, makes the Codex helper print or exit 0 on failure, or changes content outside `[mcp_servers.tdflow]` without failing closed.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | A retry inside `apply_codex` (and `apply_claude`, older) re-planned after a failed write without comparing the new plan with the approved one. A foreign `tdflow` table that appeared at that moment was replaced although the user had approved "adds" | **Fixed** in both. After a failed write nothing is re-planned in place; the next pass sees the changed file and runs the same approved-action check as before. Tests with a writer injected right before the replace |
| 2 | Low | The saved text of a replaced table was not checked against the table. A multi-line string ending in a `#` line gave a saved text that could not be put back | **Fixed.** At plan and apply, the removed text must parse to exactly the table it replaces, else `CONFIG_UNSUPPORTED` |
| 3 | Low | `str.splitlines` also splits on U+0085, U+2028 and U+2029, which TOML allows in comments and strings. Part of a user comment outside the table could be deleted without the re-parse noticing | **Fixed.** Only `\n` ends a line. Tests: connect, repair and disconnect leave such files character for character |
| 4 | Low | After a failed read-back, the file was put back without checking that it was still what TDflowMCP wrote, so another program's write could be lost | **Fixed** in both clients: put back only if the file still hashes to what was written |
| 5 | Low | The plan showed the folder of a replaced `command` (it can hold the user name) and server names from the file verbatim (a name with line breaks could forge lines of the plan) | **Fixed** for both clients: only the program's file name; server names shown only in a short plain form. A URL's path is still shown without query and user information, as accepted in review 19 |
| 6 | Low | A Codex that does not run the helper connects without credentials. Auto cannot know the CLI's version, and the newest extension hides an older one in another editor | **Accepted, documented** ([ADR-0017](../decisions/0017-codex-one-click-connection.md#security-boundaries-what-this-adr-does-not-change)). The real listener answers 401 and discloses nothing; a process squatting the port would be reached without a credential, as with any manually configured client. The plan and Next Step tell the user to update Codex |
| 7 | Low | Ownership was a list of forbidden keys | **Fixed.** It is an allowlist: the two own keys plus the documented preference keys; any other key makes the table someone else's |
| 8 | Low | Without a dialog, a second Connect Agent pulse approved the pending plan even after Coding Agent was changed to another agent | **Fixed.** Such a pulse drops the plan and makes one for the current selection. Verified in TouchDesigner |
| 9 | Low | Record facts across Repair: an older replaced entry won over a newer one; `added_newline` and `created_file` stuck from an earlier run; a failed update removed the whole table; a kill after a replace lost what to put back | **Fixed.** The facts come from the run that put the entry there; an updated entry is not removed by a rollback; the pending record already holds the replaced text |
| 10 | Low | Disconnecting one client while another was being configured could clear the shared connect key and lose a record | **Fixed.** One background change of the records at a time; the other action says `Busy: pulse again` |
| 11 | Info | A file ending in blank lines lost one after connect and disconnect; an updated line loses a trailing comment; a restored table is appended at the end; a file containing `nan` is always refused; five backups are kept | The first is **fixed** (the separating blank line is always added and exactly that one is removed). The others are accepted |
| 12 | Info | `_safe_version` accepted non-ASCII letters; the self-declared name reaches the `verified` audit detail unredacted (rendered as text) | Version characters are now ASCII only. The rest is accepted: display only |

Test findings, addressed: the Claude Code helper is pinned by the SHA-256 of the released command (the test had compared it with itself); the race and rollback tests above; a disabled agent and a revoked token in the handshake test; revocation and disconnection must show 401, not any error; the harness text now says that the real configuration is hashed for a before/after comparison.

**Not verified by the reviewer:** behaviour inside TouchDesigner, real Codex behaviour beyond the observations above, and the helper's execution. Those are covered by the unit helper tests and the Codex suite.

## Usability review
A separate agent read the rendered text. **5 High, 7 Medium, 3 Low; all High and Medium addressed** unless noted.
- **Failures now say what happened.** Next Step: `Error in Codex settings`, `Can't edit Codex settings` (also for Claude Code). View Connection Details: "Codex: not connected. Nothing was changed.", why, and what to do. "Damaged" was dropped: it could be read as damage done by TDflowMCP.
- **No pointer to a manual setup that cannot be reached** for Codex on Windows; the text names the real action (update Codex, then Repair Connection).
- **Lines per client:** `No request: new Codex chat` (not `/mcp`, a Claude Code command) and `No key sent: update Codex` (Repair cannot fix a Codex that does not run the helper).
- **Auto with several agents:** the prompt and instructions follow the agent that is connected; with several connected, the prompt names none. The chooser offers only the agents with automatic setup, so that its buttons fit, says that nothing changes yet, and marks a connected agent.
- **After connecting:** `Start a new Codex chat.` / `Start a new Claude chat.`; the plan says to pulse Copy Agent Prompt, start a **new** chat and paste, and that an open chat does not have the connection.
- **The short plan** says what each action does: `REPLACES "tdflow" in <file>`, `Updates "tdflow" in`, and for an unchanged file no backup is claimed.
- **Installed Agents** fits 26 characters with any mix (`Auto: Claude, Codex +1`, `Manual: Codex (too old)`); **Connected Agents** drops a pre-release suffix (`Codex 0.171.0 · 3 s`).
- The Skills hint is not shown to clients TDflowMCP has no Skills for.
- **Not changed:** the line `Full plan: the viewer.` (a second line would push the dialog past the length verified for its buttons); the menu label `OpenAI Codex`.
- **Not verified by the reviewer or by any agent:** whether a new chat in the VS Code Codex panel is enough without reloading the window. It is part of the owner's acceptance step.

## Limits
- Windows only. macOS and Linux keep the manual fallback: how Codex runs the helper there was not observed.
- Codex in a remote or cloud execution environment cannot use the connection (vendor documentation).
- The minimum Codex version is not documented. Tested: CLI 0.160.0 and extension 26.1117 onward. A version that ignores the key connects without credentials and gets 401; TDflowMCP then shows `No key: Repair Connection`, and View Connection Details names the likely cause.
- TDflowMCP finds the configuration through `CODEX_HOME` as TouchDesigner sees it. If Codex is started with a different `CODEX_HOME`, it reads another file.
- A project-scoped `.codex/config.toml` or a plugin that also defines a server named `tdflow` is not detected.
- Text editing covers the layouts Codex and `codex mcp add` write. Hand-written inline or dotted forms of `mcp_servers` are refused, not converted.
