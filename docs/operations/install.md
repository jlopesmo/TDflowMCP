# Installing TDflowMCP

TDflowMCP is a TouchDesigner component that lets a coding agent (Claude Code, and others) work in your project
through MCP. It works within the permissions you grant and keeps a tamper-evident audit log. It runs only on this
computer (127.0.0.1) and never installs software or changes your agent's configuration for you.

**This is a release candidate.** Use it in projects you can afford to experiment with.

## Requirements
- TouchDesigner **2025.32440** or newer. Older builds get a warning and may enable MCP only if every security
  self-test passes.
- A coding agent with Streamable HTTP MCP support. Claude Code is tested. Codex CLI, Cursor and VS Code (GitHub
  Copilot) are listed from their documentation and are not tested yet; any other MCP client works with the generic
  instructions.

## 1. Check the download
Compare the SHA-256 of `TDflowMCP.tox` with `SHA256SUMS` (and with `TDflowMCP.json`):
- PowerShell: `Get-FileHash .\TDflowMCP.tox -Algorithm SHA256`
- macOS / Linux: `shasum -a 256 TDflowMCP.tox`

## 2. Add the component
1. Save your project first (TDflowMCP keeps its data next to the saved `.toe`).
2. Drag `TDflowMCP.tox` into your project (for example into `/project1`). Use **one** TDflowMCP component per
   project.
3. The component checks the environment by itself. The **TDflowMCP** page shows the result in **System Check**
   (for example `Ready · 8 pass · 3 later`) and what to do in **Next Step**.
   - These checks never start a listener, create files, install anything or start an agent.
   - Checks that need a listener or a connection (the loopback-only binding, the security self-test, the agent
     connection) show as *later*. They run when you turn on Enable MCP.
   - Pulse **Run Diagnostics** at any time to check again. **View Diagnostics** on the **Advanced** page shows
     every check with its fix.

## 3. Connect your coding agent (all in TouchDesigner)
On the **TDflowMCP** page:
1. **Coding Agent:** leave **Auto (Recommended)**. TDflowMCP looks up the known agents on this computer (it
   never runs them) and tailors the instructions. You can also pick one explicitly.
2. **Agent Access:**
   - **Read Only**: the agent can look.
   - **Builder**: it can also change operators in its scope.
   - **Operator**: it can also *ask you* to approve code-running changes.
3. **Allowed Scope:** the operators it may work on, for example `/project1/**`.
4. Pulse **Connect Agent**. TDflowMCP creates the agent's credentials. **Enable MCP is not turned on for you.**
5. Pulse **Copy Token** within two minutes. The token goes to the clipboard (kept out of Windows Clipboard History and
   Cloud Clipboard) and is never stored in the project; the clipboard is cleared after a minute.
6. In the terminal that starts your agent, paste it when asked:
   - bash / zsh: `read -rs TDFLOW_TOKEN && export TDFLOW_TOKEN`
   - PowerShell (5.1 and 7): `$env:TDFLOW_TOKEN = [Net.NetworkCredential]::new('', (Read-Host -AsSecureString 'TDflowMCP token')).Password`
7. Pulse **Copy Instructions** (or **View Full Instructions**) and follow the "Connect your coding agent" part,
   or give the text to your agent. For Claude Code:

   ```
   claude mcp add --transport http --scope user tdflow http://127.0.0.1:9480/mcp --header 'Authorization: Bearer ${TDFLOW_TOKEN}'
   ```

   Keep the single quotes: the configuration then stores the text `${TDFLOW_TOKEN}`, not your token.
8. Turn on **Enable MCP**. Each time, it re-verifies that the listener is reachable only from this computer. If a
   precondition fails, it turns itself off again and gives the reason.
9. Start your agent. **Connected Agents** shows it once its first authenticated request arrives, and Next Step
   says so (for example `Claude Code connected. Ready.`). A registered agent that has not talked is never shown
   as connected.

## 4. Manage agents without a browser
Pulse **Manage Agents** (or open the **Agents** page):
- pick an agent;
- **Add Scope** / **Remove Scope**;
- **Enable Agent** / **Disable Agent**;
- **New Token** (revokes its older tokens; then Copy Token);
- **Revoke Tokens**;
- **View Agents**.

Requests waiting for your approval appear under **Pending Approvals**. **View Approval** shows the complete
request, then **Approve Request** or **Deny Request**.

## 5. The web pages (optional)
On the **Web** page (or with **Open Dashboard** on the main page):
- **Open Setup** and **Open Dashboard** open the page in your browser **already signed in**. TouchDesigner hands it
  a one-time sign-in that expires after 30 seconds and cannot be reused. For its first five seconds the page can show
  information but not change anything.
- The dashboard shows the same agents, permissions, approvals and activity, with searchable logs and reports.
  Changes made there and in TouchDesigner are the same changes.
- If you type the Setup address into a browser yourself, that browser must pair: compare the code under
  **Pairing Request** and pulse **Approve Pairing**.
- **End Browser Session** signs every browser out.

## Keep the TouchDesigner window open while agents work
It may sit behind other windows, but do not minimize it: TouchDesigner runs no frames while minimized.
- **While minimized:**
  - Agent calls are refused at once with `TDFLOW_NOT_COOKING`, and nothing in the project changes.
  - TDflowMCP keeps two safety checks running in the background: it re-verifies every 10 s that the listener is
    reachable only from this computer (it closes the listener if not), and it applies the idle stop.
  - It cannot apply parameter changes (including **Suspend Agents**) until the window is restored. The
    dashboard's kill switch works even then.
- **After you restore the window,** agents continue; MCP stays on.

## Optional: Skills
On the Setup page (Agents and access → Skills) you can install the TDflowMCP Skills for Claude Code, in your user
folder or in this project. You see a preview first, and files you changed are never overwritten.

## Optional: project memory
Pulse **Create Project Memory** (Advanced page) to let agents record task checkpoints and handoffs in a
`tdflowProject` component.

On the Setup page (Agents and access → Project memory) you can instead keep those records as files in a folder of
your project, for example its Git repository. You see a preview first, and you can move them back the same way.
Agents then need a token created with "May write task records into the linked repository folder". TDflowMCP writes
the files; it never runs Git.

## Safety controls
- **Suspend Agents** (main page) stops every agent request at once, including queued ones.
- Every agent call is recorded before and after it runs. If a change cannot be recorded, agents are suspended
  automatically.
- `tdflow_undo` reverts an agent's own last change only if nothing else changed since.
- Code-running changes (expressions, code DATs, Execute DATs, Code Mode scripts) always wait for your approval.

## Where data is stored
- **Project folder:** `.tdflowmcp/<project name>/` next to the `.toe` holds the audit log, sessions and DAT
  snapshots. This folder is git-ignored automatically (it contains a `.gitignore` with `*`).
- **Private per-user storage** (`%LOCALAPPDATA%\TDflowMCP` on Windows) holds credentials, the audit key and the
  keyed checkpoints. They never go into the project folder, a parameter or a DAT.
- **Fallback:** unsaved projects, read-only folders, network drives and cloud-synced folders (OneDrive, Dropbox, …)
  automatically use private storage instead. **Data Status** on the **Advanced** page says why.
- **Moved, renamed or copied a project?** MCP stays off until you choose on the **Advanced** page:
  - **Confirm Move**: a verified copy of the audit log; the old copy is left untouched.
  - **Claim As New Project**: a new identity and an empty log.

  Save the project afterwards.

## Removing TDflowMCP
Delete the component. Your project's `.tdflowmcp/` folder and the private storage remain until you delete them
yourself, because they hold the audit history. Remove agent configuration with your agent's own commands (for
Claude Code: `claude mcp remove tdflow`).
