# Installing TDflowMCP

TDflowMCP is a TouchDesigner component that lets a coding agent (Claude Code, and others) work in your project
through MCP. It works within the permissions you grant and keeps a tamper-evident audit log. It runs only on this
computer (127.0.0.1). It never installs software, and it changes your agent's configuration only after you approve
the exact change in TouchDesigner.

**This is a release candidate.** Use it in projects you can afford to experiment with.

## Requirements
- TouchDesigner **2025.32440** or newer. Older builds get a warning and may enable MCP only if every security
  self-test passes.
- A coding agent with Streamable HTTP MCP support.
  - **Claude Code** (CLI or the VS Code extension) is set up automatically and is tested on Windows. macOS automatic
    setup is implemented but not tested yet.
  - Codex, Cursor and VS Code (GitHub Copilot) use a short manual setup. They are listed from their documentation and
    are not tested yet.
  - Any other MCP client works with the generic instructions.

## 1. Check the download
Compare the SHA-256 of `TDflowMCP.tox` with `SHA256SUMS` (and with `TDflowMCP.json`):
- PowerShell: `Get-FileHash .\TDflowMCP.tox -Algorithm SHA256`
- macOS / Linux: `shasum -a 256 TDflowMCP.tox`

## 2. Add the component
1. Save your project first (TDflowMCP keeps its data next to the saved `.toe`).
2. Drag `TDflowMCP.tox` into your project (for example into `/project1`). Use **one** TDflowMCP component per
   project.
3. The component checks the environment by itself. The **TDflowMCP** page shows the result in **System Check**
   (for example `Ready · 8 ok · 3 pending`), when they ran in **Last Check**, and what to do in
   **Next Step**.
   - These checks never start a listener, create files, install anything or start an agent.
   - Checks that need a listener or a connection (the loopback-only binding, the security self-test, the agent
     connection) show as *pending*. They run when MCP is turned on, and are never counted as passed.
   - Pulse **Run Diagnostics** at any time to check again. It shows *Running diagnostics…*, then the result.
     **View Diagnostics** on the **Advanced** page shows every check with its fix.

## 3. Connect your coding agent
On the **TDflowMCP** page:
1. **Coding Agent:** leave **Auto (Recommended)**. TDflowMCP looks for installed agents. It only looks at names and
   folders and never runs them.
2. **Agent Access:**
   - **Read Only**: the agent can look.
   - **Builder**: it can also change operators in its scope.
   - **Operator**: it can also *ask you* to approve code-running changes.
3. **Allowed Scope:** the operators it may work on, for example `/project1/**`.
4. Pulse **Connect Agent**. If several agents are installed, choose which one.
5. **Review and approve.** A TouchDesigner dialog shows exactly what will happen:
   - the agent's identity and permissions;
   - the one entry added to Claude Code's settings (`~/.claude.json`); your other servers stay unchanged;
   - that a backup is kept;
   - that the credential stays in your account's protected store;
   - that **Enable MCP** will be turned on;
   - how to undo.

   Choose **Approve**, or **Cancel** (nothing changes).
6. Next Step shows the progress, then **Start a Claude Code chat.** Start a new conversation in the
   VS Code extension or a new `claude` session in a terminal (or run `/mcp` and reconnect `tdflow`).
7. When the agent's first request arrives, Next Step says **Agent Connected — Ready**, and **Connected Agents** shows
   the client and its version. A configured agent that has not talked is never shown as connected.

There is no token to copy and nothing to type: Claude Code fetches the credential for this project by itself.

**After restarting TouchDesigner,** MCP is off as always and the agents are not loaded yet. The Agents page says
*1 agent · not loaded*, not zero. Next Step says *Turn on Enable MCP.*: turn it on (or pulse Connect Agent and
approve), then start a new Claude Code chat or reconnect `tdflow` with `/mcp`. Nothing is set up again, and the same
agent and key are used.

**Other agents (Codex, Cursor, VS Code Copilot, others).** These read their credential from an environment variable
when they start, so they need one paste:
1. Set **Coding Agent** to your agent, then pulse Connect Agent.
2. On the **Agents** page, pulse **Copy Token** (within two minutes; kept out of Windows Clipboard History and cleared
   after a minute). On macOS Copy Token is not available; use Open Setup (Web page) to create and copy a token.
3. Follow **View Full Instructions**: one command in the terminal that starts the agent, and one to register the
   server.
4. Turn on **Enable MCP**.

**Undo:** Agents page, select the agent, then **Disconnect**. It removes the entry (or restores the one it replaced),
deletes the stored credential and revokes the agent's tokens. **Repair Connection** gives it a new credential if
something went wrong.

## 4. Manage agents without a browser
Pulse **Manage Agents** (or open the **Agents** page):
- pick an agent;
- **Add Scope** / **Remove Scope**;
- **Enable Agent** / **Disable Agent**;
- **Repair Connection** / **Disconnect** (agents set up by Connect Agent);
- **New Token** and **Copy Token** (manual setup; New Token revokes its older tokens);
- **Revoke Tokens**;
- **View Agents**.

Requests waiting for your approval appear under **Pending Approvals**. **View Approval** shows the complete
request, then **Approve Request** or **Deny Request**.

## 5. The web pages (optional)
On the **Web** page (or with **Open Dashboard** on the main page):
- **Open Setup** and **Open Dashboard** open the page in your browser. TouchDesigner then asks you to approve the
  sign-in and shows a code; approve only if the page shows the same code. Nothing secret travels in the address.
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
  keyed checkpoints. The connection credential is Windows-protected (DPAPI) there; on macOS it goes to the Keychain.
  None of this goes into the project folder, a parameter or a DAT. Backups of an agent configuration file that
  Connect Agent changed are kept there too (the last five).
- **Fallback:** unsaved projects, read-only folders, network drives and cloud-synced folders (OneDrive, Dropbox, …)
  automatically use private storage instead. **Data Status** on the **Advanced** page says why.
- **Moved, renamed or copied a project?** MCP stays off until you choose on the **Advanced** page:
  - **Confirm Move**: a verified copy of the audit log; the old copy is left untouched.
  - **Claim As New Project**: a new identity and an empty log.

  Save the project afterwards.

## Removing TDflowMCP
First pulse **Disconnect** for each connected agent (Agents page). That removes the Claude Code entry and the
stored credential. Then delete the component. Your project's `.tdflowmcp/` folder and the private storage remain
until you delete them yourself, because they hold the audit history. Manual connections are removed with the agent's
own commands (for example `codex mcp remove tdflow`).
