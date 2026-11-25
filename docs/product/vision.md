# TDflowMCP — Product vision and functional architecture

**Status:**
- **Mission and pillars P1–P8:** Accepted (owner brief, 2026-11-22).
- **Feature additions P9–P17:** Accepted as requirements (owner revision, 2026-11-22).
- **Phasing, success criteria and designs:** Conditionally Accepted / Proposed, as recorded in [ADR-0001](../decisions/0001-product-scope.md) and ADRs [0010](../decisions/0010-setup-assistant-and-component-parameters.md)–[0013](../decisions/0013-project-memory-model.md).

## Mission

TDflowMCP is a **source-first, AI-powered TouchDesigner development environment**. It combines MCP, Code Mode, a locally hosted browser experience, enforceable permissions, audit logging, and intelligent agent workflows with continuity between sessions.

## Who it serves

- **The TouchDesigner developer (owner/operator).** Drops one `.tox` into a project and is guided through setup. Controls what agents may touch, sees what they did, undoes it, and resumes work across sessions and agents.
- **AI coding agents** (Claude Code first; Codex, Cursor and others as verified). They need a reliable tool surface, exact connection instructions, matching Skills, and trustworthy handoffs from previous agents.
- **Reviewers and collaborators.** They read audit reports, verified handoffs and project memory as text instead of opaque `.toe` binaries.

## Functional pillars

| # | Pillar | What it means for the user | Proposed delivery |
|---|---|---|---|
| P1 | Reusable `.tox` component | Drag `TDflowMCP.tox` into any project. It is self-contained: no installs, no external files required at runtime | 1A (dev build shell), 1B (MCP), 1C (release packaging) |
| P2 | Standard MCP + optional Code Mode | Any header-capable MCP client connects. Code Mode is optional, policy-checked scripting | MCP 1B; Code Mode 3 |
| P3 | Local browser experience | **Setup Assistant** (minimal, available before MCP is enabled) and a separate **Governance Dashboard** (monitoring, audit, approvals) | Setup 1A; Dashboard 2B |
| P4 | Server-side CRUD permissions, scopes, identity | Each agent has an identity and may only create/read/update/delete/execute within approved operator scopes | 1B |
| P5 | Monitoring, audit, reporting, approvals | Every call recorded; risky calls wait for approval; exportable reports | Audit 1B; approvals/reports 2B |
| P6 | Safe execution, experiments, undo, recovery | Undoable changes; sandboxed experiments; snapshots | Undo 1B; experiments/snapshots 3 |
| P7 | Source-first dev, packaging, testing | Repo text is truth; `.tox` built and tested automatically | 1A onward |
| P8 | Extensibility | Plug-in tools for visual analysis, debugging, optimization, multi-agent work | Contract 1B; modules 4 |
| **P9** | **Essential custom parameters** | Status, port, Open Setup, Enable MCP, Suspend agents, compatibility summary, Tell Your Coding Agent — on the component itself | 1A/1B |
| **P10** | **Embedded Setup Assistant** | A browser page served by the component, usable before MCP access exists, that walks the user through checks, pairing, agent creation and enabling MCP | 1A (read-only), 1B (actions) |
| **P11** | **Compatibility checks** | TD build, bundled Python, bundled dependencies, port/conflicts, data directory, supported coding agents, with evidence per check | 1A |
| **P12** | **Guided troubleshooting** | Each failed check explains the cause and the fix. TDflowMCP never installs software and never changes TouchDesigner, OS or coding-agent configuration; the user performs any such fix themselves | 1A |
| **P13** | **MCP + agent Skills integration** | Client-aware connection instructions and Skills bundles, with versioned, hash-tracked, confirmed installation and update detection | 1C |
| **P14** | **"Tell Your Coding Agent"** | A dynamic parameter/page section with exact setup, connection, repair and work-resumption instructions for the user's agent, never containing secrets | 1A (setup/repair), 1C (connection), 2A (resume) |
| **P15** | **Session logs, task checkpoints, verified handoffs** | Persistent per-session history; agents checkpoint tasks; handoffs separate server-verified facts from agent claims | Logs 1B; checkpoints/handoffs 2A |
| **P16** | **Optional `tdflowProject` COMP** | Project-level ICM, decisions, roadmap, tasks and continuity records living with the project | 2A |
| **P17** | **Shared project-memory model** | Exactly one authoritative home per project's memory (Git repo **or** embedded COMP); the other is derived; conflicts detected, never silently merged | Model ADR now; implementation 2A |

## Non-goals (proposed)

- Not a cloud service; core makes no non-loopback network calls.
- Not a fork or redistribution of Derivative TDMCP or Embody/Envoy ([ADR-0008](../decisions/0008-upstream-reuse-policy.md)).
- Not an installer: TDflowMCP never installs Python packages, TouchDesigner builds or client software, and never edits client permission settings.
- No security based on prompts, Skills or client settings — enforcement lives in the server. Skills improve agent behaviour; they are not controls.

## Success criteria (proposed, measurable)

1. A fresh disposable project gains a working Setup Assistant by dragging in one `.tox` and pulsing **Open Setup**, with no extra installs.
2. The Setup Assistant reports every compatibility check with pass/warn/fail/unknown and evidence, and every fail has remediation text.
3. An agent whose token only allows reading `/project1/scene` cannot modify or read outside that scope; tests prove the denial; audit records it.
4. Following only the "Tell Your Coding Agent" text, Claude Code connects and completes a scoped edit.
5. A second agent resumes from a verified handoff and can distinguish server-verified facts from the previous agent's claims.
6. A project's memory cannot hold two diverging authoritative copies: a stale write is rejected with `MEMORY_CONFLICT`.

## Functional architecture summary

See [system design](../architecture/system-design.md). In short:

```
                     ┌──────────────── TDflowMCP.tox (inside TouchDesigner) ────────────────┐
 Custom parameters ─▶│ Component controller (state: Off / Setup / Enabled / Suspended)      │
 Browser ─/setup────▶│ transport 127.0.0.1:9480 → gate → Setup Assistant | Dashboard       │
 MCP client ─/mcp───▶│   → identity → MCP layer → tool registry → POLICY ENGINE → execution │
                     │ Compatibility · Agent integration · Continuity · Audit (SQLite)      │
                     └─────────────┬────────────────────────────────────────────────────────┘
                                   └─▶ optional tdflowProject COMP  ⇄  (or) project Git repo
                                       (exactly one is the authoritative project memory)
```
