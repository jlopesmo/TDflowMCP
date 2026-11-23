---
name: tdflow-core
description: Use when working in a TouchDesigner project through the TDflowMCP MCP server (tools named tdflow_*). Covers checking identity and scope first, reading before changing, small audited changes, verified undo, the kill switch, denials and what never to do without the user.
metadata:
  tdflow-skill-version: "1.0"
---

# Working in TouchDesigner with TDflowMCP

TDflowMCP skill version: 1.0 (tool surface 1). This file is guidance. The TDflowMCP server enforces
every permission itself; nothing here grants access.

## 1. Start every session with `tdflow_whoami`
- It returns your role, your scopes (which operator paths you may read or change, and which actions),
  the project root, `server_version`, `tool_surface_version` and `expected_skill_version`.
- If `expected_skill_version` differs from this skill's version (1.0), tell the user: "The TDflowMCP
  Skills on this computer are version 1.0 but the component expects <version>. Update them from the
  component's Setup page." Then continue carefully, trusting the server's answers over this file.
- Work only inside your scopes. Do not probe paths outside them: denials are recorded in the audit log.

## 2. Read first, then change in small steps
- Orient with `tdflow_project_info`, `tdflow_list_operators`, `tdflow_get_operator`,
  `tdflow_get_parameters`, `tdflow_get_dat_text` and `tdflow_get_errors`.
- Change one thing at a time with `tdflow_set_parameters`, `tdflow_create_operator`,
  `tdflow_connect`/`tdflow_disconnect`, `tdflow_set_dat_text` or `tdflow_delete_operator`, then read the
  result back and check `tdflow_get_errors`.
- Every call is audited before and after it runs. A successful change returns `audit_seq` and, when it
  can be undone, `undo_label`.
- Set constant values. Expressions, Python-style parameters, executable operator types (Execute DATs,
  scripts) and text of DATs that may run as code need approval, which is not available in this version:
  expect `DENIED` or `APPROVAL_UNAVAILABLE` and explain to the user instead of looking for another way.
- Text DATs are writable only when they are data: a Table DAT, or a Text DAT the user tagged `tdflowdata`.

## 3. Undo
- `tdflow_undo` undoes your own newest change, and only while TouchDesigner's undo history is exactly as
  it was right after that change. Check `verified` in the answer.
- `UNDO_CONFLICT` means something else changed since (often the user). Do not try to force it: describe
  the situation and ask the user what they want.

## 4. Errors that mean "stop and tell the user"
- `SUSPENDED`: the user switched on Suspend Agents. Stop; do not retry until the user says so.
- `AUDIT_UNAVAILABLE`, `OUTCOME_UNRECORDED`, `OUTCOME_UNKNOWN`: TDflowMCP could not record a change
  durably. For the last two the change may have happened; agents are suspended. Tell the user; do not
  repeat the change.
- `PRECONDITION_CHANGED`: the project changed between the check and the change; nothing ran. Read again,
  then retry once if it still makes sense.
- HTTP 401: the token is wrong, revoked or missing. Ask the user to create a new token on the Setup page
  and set `TDFLOW_TOKEN`. Never ask the user to paste a token into chat or a file.
- `MCP_DISABLED` (HTTP 503): the user has not turned on Enable MCP. Ask them to.

## 5. Never without the user's explicit request
- Install software, change TouchDesigner preferences, operating-system, firewall or coding-agent
  configuration, or end processes.
- Write the token into any file, command history, commit or chat message.
- Save, close or reload the project, or touch operators the server keeps hidden from you
  (the TDflowMCP component itself, system areas, project memory).

## 6. Keep the user informed
- Before a larger edit, say what you will change. Afterwards, summarise what changed with the audit
  sequence numbers, and leave notes for later with `tdflow_session_note` (they are redacted and audited).
- `tdflow_session_list` shows your recent sessions if you need to pick up earlier work.
