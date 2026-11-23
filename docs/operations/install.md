# Installing TDflowMCP

TDflowMCP is a TouchDesigner component that lets a coding agent (Claude Code, and others) work in your
project through MCP, with permissions you grant and a tamper-evident audit log. It runs only on this
computer (127.0.0.1) and never installs software or changes your agent's configuration for you.

**This is a development build** (version `.dev`). Use it in projects you can afford to experiment with.

## Requirements
- TouchDesigner **2025.32440** or newer (older builds are refused).
- A coding agent with Streamable HTTP MCP support. Claude Code is tested; Codex CLI, Cursor and VS Code
  (GitHub Copilot) are listed from their documentation and are not tested yet.

## 1. Check the download
Compare the SHA-256 of `TDflowMCP.tox` with `SHA256SUMS` (and with `TDflowMCP.json`):
- PowerShell: `Get-FileHash .\TDflowMCP.tox -Algorithm SHA256`
- macOS / Linux: `shasum -a 256 TDflowMCP.tox`

## 2. Add the component
1. Save your project first (TDflowMCP keeps its data next to the saved `.toe`).
2. Drag `TDflowMCP.tox` into your project (for example into `/project1`). Use **one** TDflowMCP
   component per project.
3. Nothing listens yet. On the component's **TDflowMCP** page, pulse **Open Setup**. Your browser opens
   the local Setup page.

## 3. Pair the browser and create a token
1. On the Setup page, click **Request pairing**. A short code appears.
2. In TouchDesigner, check that **Pairing Request** shows the same code, then pulse **Approve Pairing**.
3. On the Setup page, create an agent token (name, role, scope inside your project). **It is shown once.**

## 4. Give the token to your agent (never in a file)
Set it as the `TDFLOW_TOKEN` environment variable in the terminal that starts your agent:
- bash / zsh: `read -rs TDFLOW_TOKEN && export TDFLOW_TOKEN` (paste, press Enter)
- PowerShell (5.1 and 7): `$env:TDFLOW_TOKEN = [Net.NetworkCredential]::new('', (Read-Host -AsSecureString 'TDflowMCP token')).Password`

## 5. Connect the agent
Pulse **Copy Tell Agent** (or **View Agent Instructions**) and give the text to your agent, or follow its
"Connect your coding agent" section yourself. For Claude Code, for example:

```
claude mcp add --transport http --scope user tdflow http://127.0.0.1:9480/mcp --header 'Authorization: Bearer ${TDFLOW_TOKEN}'
```

Keep the single quotes: the configuration then stores the text `${TDFLOW_TOKEN}`, not your token.

## 6. Turn MCP on
Toggle **Enable MCP**. It turns itself off again with a reason if a precondition fails (for example no
token exists yet, or the audit log fails verification). Ask your agent to call `tdflow_whoami` first.

## Optional: Skills
On the Setup page (Agents and access → Skills) you can install the TDflowMCP Skills for Claude Code, in
your user folder or in this project. You see a preview first; files you changed are never overwritten.

## Safety controls
- **Suspend Agents** (kill switch) stops every agent request at once, including queued ones.
- Every agent call is recorded before and after it runs. If a change cannot be recorded, agents are
  suspended automatically.
- `tdflow_undo` reverts an agent's own last change only if nothing else changed since.

## Where data is stored
- Project folder: `.tdflowmcp/<project name>/` next to the `.toe`: the audit log, sessions and DAT
  snapshots. This folder is git-ignored automatically (it contains a `.gitignore` with `*`).
- Private per-user storage (`%LOCALAPPDATA%\TDflowMCP` on Windows): credentials, the audit key and the
  keyed checkpoints. They never go into the project folder.
- Unsaved projects, read-only folders, network drives and cloud-synced folders (OneDrive, Dropbox, …)
  automatically use private storage instead; **Data Status** on the **Advanced** page says why.
- Moved, renamed or copied a project? MCP stays off until you choose on the **Advanced** page:
  **Confirm Move** (verified copy of the audit log; the old copy is left untouched) or
  **Claim As New Project** (a new identity and an empty log). Save the project afterwards.

## Removing TDflowMCP
Delete the component. Your project's `.tdflowmcp/` folder and the private storage remain until you
delete them yourself (they hold the audit history). Remove agent configuration with your agent's own
commands (for Claude Code: `claude mcp remove tdflow`).
