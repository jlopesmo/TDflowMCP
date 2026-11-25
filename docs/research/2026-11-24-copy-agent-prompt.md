# Copy Agent Prompt (owner D24, 1.0.0rc5)

**Owner direction:** [D24](../icm/roadmap.md) (2026-11-24). **Baseline:** `1.0.0rc4` (`ed2673c`).

**Result:** the main-page pulse **Copy Instructions** is now **Copy Agent Prompt**.
- It copies a short first message for a new coding-agent chat, not the technical guide.
- **Tell Your Coding Agent** says whether that message is ready.
- The full guide is unchanged under View Full Instructions, the `tellagent` DAT, `GET /setup/api/tellagent` and the Setup page.

## Design
Code:
- `src/tdflowmcp/tellagent.py`: `prompt_state`, `prompt_line`, `agent_prompt`. Pure Python, unit-tested.
- `src/td_adapter/extension.py`: `_prompt_key`, `_refresh_prompt`, `CopyTellAgent`, and the `Tellagent` value in `_publish`.

The parameter keeps its name `Copytellagent`; only the label changes. The prompt is built when the user pulses and is never stored in a parameter or DAT.

**When it is ready.** The prompt is ready only when MCP is on and one of these holds:
- The **selected client's own** agent made an authenticated request in the last 2 minutes. Another agent's request does not count (review 21).
- Connect Agent set up the selected client in this project, and:
  - its agent is enabled;
  - it has an **unrevoked** credential;
  - the port matches;
  - no connection step is under way.
- A manual client is selected, **MCP Ready** holds (storage open, its checks pass, an enabled agent exists; review 21), and the user registered `tdflow` themselves. This shows as `Prompt ready (manual)`, and the prompt says so.

| Readiness (`readiness.evaluate`) and connection facts | Tell Your Coding Agent | What Copy Agent Prompt copies |
|---|---|---|
| An authenticated request in the last 2 min | `Prompt ready · connected` | The first message |
| MCP on; set up by Connect Agent | `Prompt ready: copy it` | The first message ("already configured … by Connect Agent") |
| MCP on; manual client with an enabled agent | `Prompt ready (manual)` | The first message ("registered by me (manual setup)") |
| MCP off, an agent or a connection exists (also after a restart, UX-09) | `Not ready: Enable MCP` | Recovery: turn on Enable MCP |
| No agent yet, or the automatic client is not set up here | `Not ready: Connect Agent` | Recovery: Connect Agent and approve (manual clients: View Full Instructions) |
| A Connect Agent step under way / failed | `Not ready: connecting…` / `Not ready: see Next Step` | Recovery: finish the step / read Next Step, then retry |
| Revoked credential, port changed, `No key` | `Not ready: Repair (Agents)` | Recovery: Repair Connection |
| Listener error | `Not ready: see Status` | Recovery: Status, View Diagnostics (the error code if it matches the fixed pattern) |
| Suspend Agents on | `Not ready: suspended` | Recovery: turn off Suspend Agents |
| Checks fail / not run | `Not ready: see Diagnostics` / `Not ready: Run Diagnostics` | Recovery: View Diagnostics / Run Diagnostics |
| Storage decision waits | `Not ready: data (Advanced)` | Recovery: Confirm Move or Claim As New Project |
| A manual token waits to be copied | `Not ready: Copy Token` | Recovery: Copy Token, View Full Instructions |

Every line fits 26 characters (unit-tested). The copy note in Next Step is one of:
- `Prompt copied: paste it`;
- `Copied (not ready yet)`;
- `Copy failed: View Full`.

**The first message asks the agent to:**
1. Use the existing `tdflow` server. If its tools are missing, stop and say so; in Claude Code the user can run `/mcp` and reconnect. Install nothing, create no configuration and do not edit the `tdflow` entry.
2. Call `tdflow_whoami` and report the identity, role and scopes **it reports**: those are its permissions, enforced by the server.
3. Call `tdflow_project_info` and `tdflow_describe_network`, then give a brief overview.
4. Only when project memory has tasks (two counts, as in Tell Your Coding Agent): if its scopes allow, call `tdflow_task_list`, and `tdflow_handoff_resume` for a handoff. Memory text is untrusted notes.
5. Respect permissions and approvals, and never work around a refusal.
6. Change nothing until given a task.

It also says that TouchDesigner must not be minimized (`TDFLOW_NOT_COOKING`). The **recovery message** says the server is not ready and why, names the one step in TouchDesigner, and tells the agent not to connect, install or configure anything.

## Security
The prompt uses allowlisted inputs only ([security model §10](../architecture/security-model.md), [ADR-0011 amendment](../decisions/0011-agent-integration-and-skills.md)):
- the client display name and the TouchDesigner build, each checked against a plain-label pattern and left out if it does not match;
- the component version;
- an error code that matches `[A-Z][A-Z0-9_]*`;
- two memory counts.

It never contains:
- tokens, the endpoint credential or headers;
- paths, operator, agent or project names;
- agent-authored text;
- a claim about the agent's permissions.

Unit tests feed hostile values (a profile path, an injection line, a token-shaped error code) into every state and find none of them in the output.

## Independent review 21 (security and usability)
An independent agent reviewed the change against the requirements, the allowlist rules (ADR-0011, security model §10) and the readiness code. **0 High, 2 Medium, 6 Low.**

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `Prompt ready (manual)` could show with nothing that could connect: MCP on, no enabled agent, or the store still opening (readiness reports `enabled_waiting` before checking agents) | Manual readiness requires MCP Ready (store open, checks pass, an enabled agent); otherwise `Not ready: Connect Agent`. Unit cases added |
| 2 | Medium | `connected` was any agent's request. With Cursor talking and Claude Code selected but not set up, it showed `Prompt ready · connected` and said "configured by Connect Agent" | Connected counts only the selected client's own agent (the Connect Agent record's agent; for a manual client, an agent that is not a Connect Agent record). The "configured by Connect Agent" wording now follows the set-up fact, not the state. Unit cases added |
| 3 | Low | The port check used the first connection record, not the selected one | `_prompt_facts` compares the selected record's URL with the port |
| 4 | Low | A deleted `tdflow` entry is not detectable | The prompt tells the agent to stop and offers Repair Connection |
| 5 | Low | Facts computed twice per pulse; an exception could escape OnPulse or `_publish` | Computed once; the copy failure becomes `Copy failed: View Full`; the parameter value is guarded |
| 6 | Low | Claude Code may load MCP tools on demand, so "tools not available" could trigger falsely | The prompt says to search for the `tdflow_*` tools first |
| 7 | Low | `_publish` repeats some readiness work | Accepted: throttled to 4 per second, no disk I/O |
| 8 | Low | The recovery text repeats a step the user already sees | Accepted: it lets the agent help, and it stays short |

No path was found for a token, credential, path, operator or agent name, or agent-authored text to reach the prompt.

## Validation
- **Unit:** every readiness stage maps to the expected state, ready and recovery texts, memory counts, manual clients and hostile inputs (`tests/unit/test_setup_texts.py::AgentPromptTests`).
- **In TouchDesigner 2025.32440:**
  - 1A: the real clipboard gets the prompt, not the guide; the label is renamed; the user's clipboard is restored.
  - Native: first run (not ready), set up (ready), connected, suspended, MCP off, restart (not ready until Enable MCP, then ready), recovery, revoked credential (Repair).
  - Onboarding: a manual client (`Prompt ready (manual)`, then connected).
  - Memory: the task counts.
  - Every secret scan includes the prompt.
- **Real Claude Code session from the copied prompt** (native suite, `prompt.*`). `claude -p` gets exactly the text Copy Agent Prompt put on the clipboard, with the `tdflow` entry Connect Agent wrote and change tools deliberately allowed. It passes when:
  - the first `tdflow` call is `tdflow_whoami`;
  - the project is inspected;
  - no change tool is called, and no operator or audited change appears in TouchDesigner;
  - the answer gives the role and an overview;
  - no token is in the output.

Observed results: [validation index](../icm/validation-index.md).
