# Installing TDflowMCP

TDflowMCP is a TouchDesigner component that lets a coding agent (Claude Code, Codex, and others) work in your project
through MCP. It works within the permissions you grant and keeps a tamper-evident audit log. It runs only on this
computer (127.0.0.1). It never installs software, and it changes your agent's configuration only after you approve
the exact change in TouchDesigner.

**Version 1.3.0, provided for evaluation.** The verified cached TOP inspection runtime is **Windows** with TouchDesigner **2025.32440**.
Newer TouchDesigner builds and macOS are not verified. Try it first in a copy of a project, or in one you can afford
to experiment with. The repository has no open-source license yet: you may download and evaluate the component, but
that does not grant rights to redistribute, modify or commercially reuse it.

## Requirements
- **Windows** and TouchDesigner **2025.32440**, the only verified combination. Newer builds may work but are not
  tested. Older builds get a warning and may enable MCP only if every security self-test passes.
- A coding agent with Streamable HTTP MCP support.
  - **Claude Code** (CLI or the VS Code extension) is set up automatically and is tested on Windows. macOS automatic
    setup is implemented but not tested yet.
  - **Codex** (CLI or the VS Code extension) is set up automatically on **Windows** and is tested there. It needs a
    recent Codex (tested: Codex CLI 0.160.0 and the VS Code extension 26.1120). On macOS and Linux, Codex uses the
    manual setup. Codex running in a remote or cloud environment cannot use this connection.
  - Cursor and VS Code (GitHub Copilot) use a short manual setup. They are listed from their documentation and are
    not tested yet.
  - Other Streamable HTTP MCP clients can try the manual setup instructions.

## 1. Check the download
Choose the `TDflowMCP-vX.Y.Z.tox` asset when available. Compare its SHA-256 with `SHA256SUMS`, or with
`SHA256SUMS-versioned` for v1.0.0 and v1.0.1 aliases. `TDflowMCP.json` records the same hash for both filenames.
The compatibility download `TDflowMCP.tox` is listed in `SHA256SUMS`.

- PowerShell, using the versioned filename. `Get-FileHash .\TDflowMCP-v1.3.0.tox -Algorithm SHA256`
- macOS / Linux, to check the file. `shasum -a 256 TDflowMCP-v1.3.0.tox`

## 2. Add the component
1. Save your project first (TDflowMCP keeps its data next to the saved `.toe`).
2. Drag the verified `.tox` into your project (for example into `/project1`). Use **one** TDflowMCP component per
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
4. Pulse **Connect Agent**. If several agents are installed (for example Claude Code and Codex), a dialog asks which
   one. Nothing changes until you approve the plan that follows. You can connect the other one later: each agent
   gets its own access and can be disconnected on its own.
5. **Review and approve.** A TouchDesigner dialog shows exactly what will happen:
   - the agent's identity and permissions;
   - the one entry (`tdflow`) added to your agent's settings: `~/.claude.json` for Claude Code,
     `~/.codex/config.toml` for Codex (`~` is your user folder). Your other servers stay unchanged. If a `tdflow`
     entry is already there, for example from an earlier manual Codex setup, the dialog says **REPLACES**: the old
     entry is backed up and put back when you disconnect;
   - that a backup is kept;
   - that the credential stays in your account's protected store;
   - that **Enable MCP** will be turned on;
   - how to undo.

   Choose **Approve**, or **Cancel** (nothing changes).
6. Next Step shows the progress, then **Start a new Claude chat.** or **Start a new Codex chat.** **Tell Your
   Coding Agent** now says *Prompt ready: copy it*. Pulse **Copy Agent Prompt**, start a **new** chat and paste:
   - Claude Code: a new conversation in the VS Code extension, or a new `claude` session in a terminal.
   - Codex: a new chat in the Codex panel in VS Code, or a new `codex` session in a terminal. A chat that was already
     open does not have the connection.

   The agent connects, checks its identity and permissions with
   `tdflow_whoami`, gives a short overview of the project and waits for your first task. It contains no token. (If
   Tell Your Coding Agent says *Not ready: …*, the copied text names the one step to do first.)
7. When the agent's first request arrives, Next Step says **Agent Connected — Ready**, and **Connected Agents** shows
   the client and its version. A configured agent that has not talked is never shown as connected.

There is no token to copy and nothing to type: Claude Code and Codex fetch the credential for this project by
themselves.

**If Codex does not connect.**
- *Installed Agents* says `Codex (too old)`: the Codex VS Code extension is older than 26.1117. Update it, then pulse
  Connect Agent again.
- Next Step says *No key sent: update Codex*: this Codex is too old to fetch the key. Update Codex, then pulse
  **Repair Connection** (Agents page).
- Next Step says *Error in Codex settings* or *Can't edit Codex settings*: nothing was changed.
  `~/.codex/config.toml` has an error, or has a `tdflow` entry written in a form TDflowMCP does not edit. **View
  Connection Details** (Advanced page) says which and what to do; usually: fix the file, or remove your own `tdflow`
  entry (`codex mcp remove tdflow`), and pulse Connect Agent again.

**After restarting TouchDesigner,** MCP is off as always and the agents are not loaded yet. The Agents page says
*1 agent · not loaded*, not zero. Next Step says *Turn on Enable MCP.*: turn it on (or pulse Connect Agent and
approve), then pulse **Copy Agent Prompt** and paste it into a new Claude Code or Codex chat (in Claude Code you can
instead reconnect `tdflow` with `/mcp`). Nothing is set up again, and the same agent and key are used.

**Other agents (Cursor, VS Code Copilot, others; Codex on macOS or Linux).** These read their credential from an
environment variable
when they start, so they need one paste:
1. Set **Coding Agent** to your agent, then pulse Connect Agent.
2. On the **Agents** page, pulse **Copy Token** (within two minutes; kept out of Windows Clipboard History and cleared
   after a minute). On macOS Copy Token is not available; use Open Setup (Web page) to create and copy a token.
3. Follow **View Full Instructions**: one command in the terminal that starts the agent, and one to register the
   server.
4. Turn on **Enable MCP**, then pulse **Copy Agent Prompt** and paste it as the agent's first message.

**Undo:** Agents page, select the agent, then **Disconnect Agent**. It removes the entry (or restores the one it replaced),
deletes the stored credential and revokes the agent's tokens. **Repair Connection** gives it a new credential if
something went wrong.

## 4. Manage agents without a browser
Pulse **Manage Agents** (or open the **Agents** page):
- pick an agent;
- **Add Scope** / **Remove Scope**;
- **Enable Agent** / **Disable Agent**;
- **Repair Connection** / **Disconnect Agent** (agents set up by Connect Agent);
- **New Token** and **Copy Token** (manual setup; New Token revokes its older tokens);
- **Revoke Tokens**;
- **View Agents**.

Requests waiting for your approval appear under **Pending Approvals**. **View Approval** shows the complete
request, then **Approve Request** or **Deny Request**.

## 5. The web pages (optional)
On the **Web** page (or with **Open Dashboard** on the main page):
- **Open Setup** and **Open Dashboard** open the page in your browser. TouchDesigner then shows a **Browser sign-in**
  dialog listing three codes (`Button 1:  ABC-DEF`, …): press the button next to the code your page shows. A wrong
  button denies; if no page opened or no code matches, press **Deny**. Nothing secret travels in the address.
- The dashboard shows the same agents, permissions, approvals and activity, with searchable logs and reports.
  Changes made there and in TouchDesigner are the same changes.
- The dashboard's **Capabilities** tab shows what TDflowMCP supports, what each role can be given and what each agent
  can do now (Available, Approval Required, Denied, Not Implemented, Protected). Change an agent's role, scopes or
  repository-memory permission there: **Preview** shows the effect, and giving more access needs **I confirm**.
  Ask your agent "What can you do?" and it answers from its own permissions and points you here.
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
folder or in this project. You see a preview first, and files you changed are never overwritten. There are no
TDflowMCP Skills for Codex.

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

## Authoring Python

Use an Operator with `read`, `update` and `execute` access to the relevant operators. Add `create` in that scope to stage a new DAT. The agent can show Python syntax results and a DAT diff before making a request. Review the requested code and target in **View Approval** or the dashboard, then approve the exact call so the agent can repeat it.

For the supported Execute DATs, approve creation, editing and activation as separate changes. The DAT starts inactive. When changing an existing callback, use the displayed source and trigger settings to review activation. If a callback cannot be displayed for approval, switch off **Active** on that DAT in TouchDesigner. Have the agent check TouchDesigner's runtime diagnostics and the resulting output after activation.

Approved Python runs inside TouchDesigner with access to the host. The scopes govern MCP actions. Script CHOP, SOP and TOP lifecycle authoring remains deferred.

On Windows with TouchDesigner 2025.32440, lock the output TOP in TouchDesigner and keep bypass off on the TOP and its containing components. The agent can then capture the frozen image. Network summaries use resolution for texture-size hints only for eligible TOPs. A frozen image can include content from outside the agent's scope. Other runtimes omit texture-size hints and refuse image capture.

## Building a reusable component

The agent can search supported local operator types and inspect parameter names on an existing instance. Ask it to group the network in a Base COMP and propose a page of scalar controls. An Operator with `read`, `update` and `execute` access can request approval for definitions. Review the names, labels, defaults, and ranges, then approve expressions separately when the agent connects the controls to the network.

When the agent adds the first Out TOP in a Base COMP, use that native connector to attach the component to your enclosing network. Pulse buttons in TouchDesigner after approving any associated callback changes. The agent can undo the last eligible edit to restore the previous definitions and order. Use TouchDesigner to rename or remove controls that may have existing dependencies.

## Diagnosing a network

Name the component or output and describe what you expected. Ask the agent for the evidence, a minimal repair, and a check of the result. Reported errors and missing required inputs are observations. Readable upstream leads guide investigation. The last CPU cook times rank sampled work in milliseconds and can be old.

After a repair, the agent can verify wiring immediately. If the cook count has stayed the same, the reported error may still reflect the previous cook. Verify the output after it cooks normally, or check it yourself in TouchDesigner.

To compare outputs, lock both TOPs in TouchDesigner and leave bypass off on the TOPs and enclosing components. Matching dimensions allow aligned RGB comparison within the sample limit. A dark result or color difference can guide where to look upstream. Review any requested change through the normal approval flow and ask the agent to inspect the output again.

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
First pulse **Disconnect Agent** for each connected agent (Agents page). That removes the `tdflow` entry from
Claude Code's or Codex's settings (or puts back the one it replaced) and the stored credential. Then delete the component. Your project's `.tdflowmcp/` folder and the private storage remain
until you delete them yourself, because they hold the audit history. Connections you set up by hand are removed
with the agent's own commands (for example `codex mcp remove tdflow`).
