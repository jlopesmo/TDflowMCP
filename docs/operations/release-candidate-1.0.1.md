# TDflowMCP 1.0.1 release candidate: changelog, compatibility and acceptance checklist

**Candidate: `1.0.1rc1`**, on `dev` only (owner D27, GitHub Issue #3). **This is not a release:** nothing is merged to `main`, tagged or published, and the published [v1.0.0](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.0) is unchanged. Releasing it is the owner's decision after the acceptance check below ([verification and release §4](verification-and-release.md#4-release-proposed)).

## Changes from 1.0.0
- **One-click connection for OpenAI Codex on Windows** ([ADR-0017](../decisions/0017-codex-one-click-connection.md), [record](../research/2026-11-25-codex-one-click-connection.md)):
  **Coding Agent: Codex → Connect Agent → Approve → new Codex chat → connected.**
  - No token to copy, no TOML to edit, no environment variable.
  - TDflowMCP adds one table, `[mcp_servers.tdflow]`, to `~/.codex/config.toml`. Everything else in the file stays byte for byte; a backup is kept; **Disconnect Agent** removes exactly that table.
  - The key stays in Windows' protected storage. Codex fetches it through a fixed helper command, only after TouchDesigner proves it is this project.
  - **Repair Connection** gives Codex a new key; a running Codex picks it up on its next call.
  - The Codex CLI, the Codex VS Code extension and (per OpenAI's documentation) the desktop app share that file.
- **Codex and Claude Code can both be connected** to one project. Each has its own agent, key, permissions and audit records, and disconnecting one does not touch the other. With Coding Agent on Auto and both installed, Connect Agent asks which one.
- **Connected Agents** shows the Codex client and its version (for example `Codex 0.160.0`).
- **Clearer wording, also for Claude Code:** Next Step says `Start a new Claude chat.` / `Start a new Codex chat.`; a refused settings file says `Error in … settings` or `Can't edit … settings`, and View Connection Details says that nothing was changed, why, and what to do; Installed Agents fits the parameter window with any mix of agents (`Auto: Claude, Codex +1`).
- **Hardening from security review 23, also for Claude Code:** a settings file that another program changes at the moment of writing can no longer turn an approved "adds" into a replacement; a failed read-back no longer undoes another program's write; the plan shows only the file name of a replaced program and plain server names; without a dialog, a second pulse no longer approves a plan after Coding Agent was changed.
- The registry name of the client is `Codex` (was `OpenAI Codex CLI`); the Coding Agent menu still says `OpenAI Codex`.
- **Unchanged:** the 30 tools, tool surface `1`, Skill 1.6, permissions, approvals, the audit format, and the Claude Code connection (its helper command is byte-for-byte the same, so existing connections keep working).

## Compatibility matrix (tested only)
| Component | Tested | Result | Evidence |
|---|---|---|---|
| TouchDesigner (Windows 11) | **2025.32440** | All in-TD suites pass | [validation index](../icm/validation-index.md) |
| TouchDesigner newer builds, macOS, Linux | **not tested** | — | — |
| Codex CLI (Windows) | **0.160.0** | Its engine connects through the written `config.toml`: tools listed, `tdflow_whoami` as the Codex agent, restart, revoke, repair without restart, disconnect | validation index (Codex suite) |
| Codex VS Code extension (Windows) | **26.1120.50957** (engine codex-cli 0.171.0-alpha.13.2) | The same, plus an operator created in scope and a refusal outside it. **The chat window itself and a model session: owner acceptance step, not executed by an agent** | validation index (Codex suite) |
| Codex desktop app | **not tested** (not installed) | Shares the configuration per OpenAI's documentation | — |
| Codex on macOS or Linux; Codex in a remote or cloud environment | **not offered** | Manual setup, or not possible | [ADR-0017](../decisions/0017-codex-one-click-connection.md) |
| Claude Code CLI (Windows) | **2.1.289** | Unchanged; also verified connected next to Codex | validation index |
| Claude Code VS Code extension (Windows) | engine **2.1.332** | Unchanged | validation index |
| Cursor, VS Code Copilot | manual fallback instructions only, **no live test** | — | [current state](../icm/current-state.md#not-observed--unverified) |

No other compatibility is claimed. The minimum Codex version for the header helper is not documented by OpenAI; older versions keep the manual setup.

## Acceptance checklist
- [x] Codex capability verified on the installed clients before any claim: both engines run `http_headers_helper`; how they run it was observed ([record](../research/2026-11-25-codex-one-click-connection.md#what-codex-documents-and-what-was-observed)).
- [x] Unit tests (537), doc checker and secret scan pass on the candidate tree (validation index).
- [x] Every in-TD suite, the Codex suite with real engines (55/55 + 64/64), the visual review and the browser checks pass on the candidate build `b37e1d94…` (validation index).
- [x] The same on the **packaged** `.tox`, built from a clean worktree of the candidate commit `2bb48e5` (`dist/TDflowMCP-1.0.1rc1-2bb48e5390`, tox SHA-256 `95e53f84ec961f21a771eac3b261c5bcfd98ebaf0816d775ce0c6e14de92329e`, the same source digest as the tested candidate) (validation index).
- [x] Independent security review 23 (0 High; 1 Medium and 8 Low fixed, 1 Low accepted) and a usability review (High and Medium addressed) ([record](../research/2026-11-25-codex-one-click-connection.md#independent-review-23-security)).
- [x] GitHub CI, Windows and Ubuntu: [run #62](https://github.com/jlopesmo/TDflowMCP/actions/runs/45702971854) on `4d28dd9` passed. (Runs #60 and #61 failed on Ubuntu in one new unit test that used a Windows path; the test was fixed, no product code changed. Validation index.)
- [ ] **Owner: Codex in VS Code on the real profile** (**not executed by an agent**: it needs your approval in TouchDesigner and a Codex chat). About five minutes, in a copy of a project or one you can experiment with:
  1. Drag the 1.0.1rc1 `TDflowMCP.tox` into a saved project (or replace the 1.0.0 component in a copy).
  2. On the TDflowMCP page set **Coding Agent** to **OpenAI Codex**, pulse **Connect Agent**, read the plan and choose **Approve**.
  3. In VS Code open the Codex panel. `tdflow` should now be in its MCP servers list. Start a **new** Codex chat.
  4. Pulse **Copy Agent Prompt** in TouchDesigner and paste it as the first message. Expected: Codex calls `tdflow_whoami`, reports its role and scope and gives a short overview; TouchDesigner shows **Agent Connected — Ready** and `Codex <version>` under Connected Agents.
  5. Optional: Agents page, select the Codex agent, **Disconnect Agent**. `tdflow` should be gone from Codex's list, and `~/.codex/config.toml` should be as it was before step 2.

  If step 3 shows `tdflow` as failed: check that TouchDesigner is open (not minimized) and Enable MCP is on, then View Connection Details (Advanced page).
- [ ] **Owner decision:** release 1.0.1 (PR `dev → main`, version `1.0.1`, tag, package from the tag, GitHub Release), or ask for changes.

## Known limits added by this candidate
- Automatic Codex setup is for Windows only.
- TDflowMCP edits `config.toml` only in the layouts Codex itself writes; hand-written inline or dotted `mcp_servers` forms are refused with `Can't edit Codex settings`, and nothing is changed.
- If Codex is started with a different `CODEX_HOME` than TouchDesigner sees, it reads another file.
