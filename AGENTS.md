# TDflowMCP — agent entry

This file is the entry point for every coding or documentation agent (Claude Code, Codex, Cursor, others). `CLAUDE.md` points here.

## 1. Read before acting

1. [ICM index](docs/icm/README.md), then [context](docs/icm/context.md), [invariants](docs/icm/invariants.md) and [current state](docs/icm/current-state.md).
2. Only the [decision records](docs/decisions/README.md), [architecture](docs/architecture/README.md) and [milestone](docs/milestones/README.md) documents relevant to the task.
3. [RepoInspire register](repoinspire.md) **before** recommending, evaluating, copying from or introducing any external repository, package, framework, MCP server or skill.
4. [Workspace and Git rules](docs/operations/git-workflow.md) before creating branches, worktrees or commits.

## 2. Status discipline

Every statement in this repository has a status. Never promote one silently:

| Status | Meaning | Where it lives |
|---|---|---|
| **Accepted** | Owner-approved decision (**Conditionally Accepted** = binding once its validation gates pass; a failed gate reopens it) | ADR with `Status: Accepted`/`Conditionally Accepted` + owner/date |
| **Proposed** | Assistant/engineer recommendation awaiting owner approval | ADR with `Status: Proposed`, architecture docs |
| **Hypothesis** | Untested claim or expectation | research, milestones |
| **Verified** | Observed through an executed check, with evidence | `docs/icm/current-state.md`, `docs/icm/validation-index.md` |

A design in `docs/architecture/` is **not** an implemented capability. Only `current-state.md` states what exists, and only with evidence.

## 3. ICM maintenance is part of every change (mandatory)

Any change that affects behavior, structure, contracts, security, dependencies or plans **must update the affected ICM files in the same commit**:

| If you change… | Also update |
|---|---|
| Add/move/delete a module, directory or build artifact | `docs/icm/project-map.md` |
| Implemented capability, known limitation or next action | `docs/icm/current-state.md` |
| Tests, validation commands or their results | `docs/icm/validation-index.md` |
| Sequencing, milestones or acceptance criteria | `docs/icm/roadmap.md` and the milestone file |
| A consequential technical choice or dependency | new/updated ADR in `docs/decisions/` and `repoinspire.md` if external |
| Permission, identity, audit, exec or network behavior | `docs/architecture/security-model.md` (+ ADR if it changes a decision) |
| An invariant | `docs/icm/invariants.md` **only** with an owner-accepted ADR |
| MCP tool surface | `docs/architecture/tool-surface.md` |
| Component parameters, states, Setup Assistant, compatibility checks, troubleshooting | `docs/architecture/setup-assistant.md` (+ ADR-0010) |
| Client registry, Skills, "Tell Your Coding Agent" | `docs/architecture/agent-integration.md` (+ ADR-0011) |
| Session logs, checkpoints, handoffs, project memory, `tdflowProject` | `docs/architecture/continuity-and-memory.md` (+ ADR-0012/0013) |
| CI or any check result | `docs/icm/validation-index.md`, with **observed** evidence only (run URL/ID or pasted output) |

`python tools/check_docs.py` must pass before committing. Reviewers should reject code changes that leave ICM stale.

## 4. Hard rules

- **No upstream code copying** from TDMCP, Embody/Envoy or other references unless `repoinspire.md` records a completed license review and an owner-approved `code_reuse` decision. See [ADR-0008](docs/decisions/0008-upstream-reuse-policy.md).
- **Do not alter real TouchDesigner projects** (`.toe`/`.tox` outside this repo). TouchDesigner experiments are authorized (owner, 2026-11-22) **only in isolated, disposable projects** created for the purpose under `.tmp/` or a scratch folder. Do not launch MCP servers or network services except inside such an experiment, when the current milestone calls for it.
- **No unauthorized installations or configuration changes**, neither by the product nor by agents working on it. Ask before installing anything.
- **Security is server-side.** Never rely on client honesty, prompt instructions or the dashboard UI for permission enforcement.
- **No new runtime dependency** without an ADR. TouchDesigner's bundled Python is the default runtime; do not assume network access to PyPI/npm on the work PC.
- Never commit secrets, tokens, user-profile paths or personal details, user media, audit databases, or large binaries. Built `.tox` files are release artifacts, not source (see [source-of-truth rules](docs/architecture/source-of-truth.md)).
- Do not merge to `main`, force-push, rewrite history or deploy without explicit owner approval.

## 5. Ending a task

Report: changed paths, commit SHA, checks run with real output, ICM files updated, limitations, and decisions awaiting owner approval. Never invent test results; mark unrun checks as **not executed**.
