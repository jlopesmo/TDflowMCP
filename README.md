# TDflowMCP

A source-first, AI-powered TouchDesigner development environment: an MCP server packaged as a reusable `.tox`, with server-side permissions, agent identity, audit logging, approvals, a local browser dashboard, safe experimentation and optional Code Mode orchestration.

> **Status: Phase 2A complete; Phase 2B in progress (2026-11-23), development builds only.**
> - The source-built `TDflowMCP.tox` (TouchDesigner 2025.32440+) provides the local-only Setup Assistant (1A), a secure MCP core (1B: scoped, audited tools with verified undo, pairing, agent tokens, a kill switch), agent onboarding with Skills (1C), and project memory (2A: task checkpoints, verified handoffs, restart persistence, and records either inside the project file or as files in a folder of the project).
> - Phase 2B increment 1: a governance dashboard at `/ui/` (activity, approvals, sessions, agents, reports, kill switch). Code-running parameter changes run only after you approve that exact call there. There is no raw Python execution.
> - Keep the TouchDesigner window open while agents work: TouchDesigner pauses while minimized.
> - Nothing is released.
>
> See [current state](docs/icm/current-state.md) and [build and test](docs/operations/build-and-test.md).

**User experience** (built for Claude Code; development builds): drag `TDflowMCP.tox` into a project → the compatibility checks run → pulse **Connect Agent** and approve the plan in TouchDesigner (Claude Code is configured with no token to copy; other agents get a short manual setup) → pulse **Copy Agent Prompt** and paste it as the first message of a new Claude Code chat → work under enforced, audited scopes → resume later from verified handoffs.

## Start here

- **Humans:** [Product vision](docs/product/vision.md) → [System design](docs/architecture/system-design.md) → [Roadmap](docs/icm/roadmap.md) (owner decisions and open blockers).
- **Agents:** [AGENTS.md](AGENTS.md) → [ICM index](docs/icm/README.md).
- **Research:** [Landscape and comparison (2026-11-22)](docs/research/2026-11-22-landscape-and-comparison.md).

## Proposed architecture in one picture

```
MCP client ──Streamable HTTP + bearer token──▶ ┌ TDflowMCP.tox (inside TouchDesigner) ─────────────┐
Browser dashboard ──same origin, admin session─▶│ loopback transport (127.0.0.1) → gate → identity │
                                                │ → MCP layer → tool registry → POLICY ENGINE       │
                                                │ → execution (main thread, undo) → host network    │
                                                │ audit (SQLite, hash-chained) · approvals queue    │
                                                └───────────────────────────────────────────────────┘
```

No separate Node.js server and no third-party Python packages are needed under the proposed design ([ADR-0002](docs/decisions/0002-in-process-mcp-host.md), [ADR-0003](docs/decisions/0003-protocol-implementation.md)).

## Checks

```
python tools/check_docs.py
python -m unittest discover -s tests -t .
```

## License

Private repository. Choosing a public license is deferred (owner decision, 2026-11-22). No reuse rights are granted.
