# TDflowMCP

A source-first, AI-powered TouchDesigner development environment: an MCP server packaged as a reusable `.tox`, with server-side permissions, agent identity, audit logging, approvals, a local browser dashboard, safe experimentation and optional Code Mode orchestration.

> **Status: Phase 0 (foundation, revised 2026-11-22).** This repository contains research, architecture and project documentation only. **No component, Setup Assistant, server or dashboard is implemented yet.** See [current state](docs/icm/current-state.md).

**Planned user experience** (design, not built): drag `TDflowMCP.tox` into a project → pulse **Open Setup** → the browser Setup Assistant runs compatibility checks and guides pairing, agent tokens and enabling MCP → copy **Tell Your Coding Agent** into Claude Code → work under enforced, audited scopes → resume later from verified handoffs.

## Start here

- **Humans:** [Product vision](docs/product/vision.md) → [System design](docs/architecture/system-design.md) → [Roadmap](docs/icm/roadmap.md) (owner decisions and open blockers).
- **Agents:** [AGENTS.md](AGENTS.md) → [ICM index](docs/icm/README.md).
- **Research:** [Landscape and comparison (2026-11-22)](docs/research/2026-11-22-landscape-and-comparison.md).

## Proposed architecture in one picture

```
MCP client ──Streamable HTTP + bearer token──▶ ┌ TDflowMCP.tox (inside TouchDesigner) ─────────────┐
Browser dashboard ──same origin, admin session─▶│ Web Server DAT (127.0.0.1) → gate → identity      │
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
