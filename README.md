# TDflowMCP

A source-first, AI-powered TouchDesigner development environment: an MCP server packaged as a reusable `.tox`, with server-side permissions, agent identity, audit logging, approvals, a local browser dashboard, safe experimentation and optional Code Mode orchestration.

> **Status: TDflowMCP 1.0.0rc6 — verified release candidate; v1.0.0 publication pending.**
> - The 1.0 feature set is frozen. Tests passed on Windows with TouchDesigner **2025.32440**; newer builds and macOS are **not yet verified**.
> - Claude Code on Windows supports consent-based Connect Agent without copying credentials; other documented clients use a manual setup fallback.
> - MCP binds to `127.0.0.1` only, enforces agent scopes and approvals, and requires an intact durable security audit.
> - No official GitHub Release asset has been published yet. The generated `.tox` is deliberately excluded from source Git history.
> - Release preparation: [PR #1](https://github.com/jlopesmo/TDflowMCP/pull/1), [RC6 acceptance](docs/operations/release-candidate-1.0.md).

## Download and first run

**Once v1.0.0 is published:** open [GitHub Releases](https://github.com/jlopesmo/TDflowMCP/releases), download the attached `TDflowMCP.tox` (and its SHA-256 manifest), verify the hash, then drag it into a saved TouchDesigner project. There is no binary to download from the `main` branch yet; the tested asset will be attached to the release, not committed as a build output.

1. Open the `TDflowMCP` parameter page; automatic diagnostics run, and **Run Diagnostics** remains available manually.
2. Leave **Coding Agent** on Auto, pulse **Connect Agent**, and approve the displayed plan. The approved Claude Code setup configures the local client securely.
3. Pulse **Copy Agent Prompt** and paste it into a new Claude Code chat. The agent verifies access with `tdflow_whoami` and awaits your task.
4. Use **Open Dashboard → Capabilities** to review or change supported agent permissions.

The browser dashboard is optional for initial setup. Keep TouchDesigner open and unminimized while an agent modifies the project. [Installation and security details](docs/operations/install.md).

**User experience** (Windows / Claude Code, verified on RC6): drag `TDflowMCP.tox` into a project → the compatibility checks run → pulse **Connect Agent** and approve the plan in TouchDesigner (Claude Code is configured with no token to copy; other agents get a short manual setup) → pulse **Copy Agent Prompt** and paste it as the first message of a new Claude Code chat → work under enforced, audited scopes (see and change what each agent may do on the dashboard's **Capabilities** page; ask the agent "What can you do?") → resume later from verified handoffs.

## Start here

- **Humans:** [Product vision](docs/product/vision.md) → [System design](docs/architecture/system-design.md) → [Roadmap](docs/icm/roadmap.md) (owner decisions and open blockers).
- **Agents:** [AGENTS.md](AGENTS.md) → [ICM index](docs/icm/README.md).
- **Research:** [Landscape and comparison (2026-11-22)](docs/research/2026-11-22-landscape-and-comparison.md).

## Architecture at a glance

```
MCP client ──Streamable HTTP + bearer token──▶ ┌ TDflowMCP.tox (inside TouchDesigner) ─────────────┐
Browser dashboard ──same origin, admin session─▶│ loopback transport (127.0.0.1) → gate → identity │
                                                │ → MCP layer → tool registry → POLICY ENGINE       │
                                                │ → execution (main thread, undo) → host network    │
                                                │ audit (SQLite, hash-chained) · approvals queue    │
                                                └───────────────────────────────────────────────────┘
```

No separate Node.js server or third-party Python packages are required for the embedded server ([ADR-0002](docs/decisions/0002-in-process-mcp-host.md), [ADR-0003](docs/decisions/0003-protocol-implementation.md)).

## Checks

```
python tools/check_docs.py
python -m unittest discover -s tests -t .
```

## License

This repository is publicly readable, but a public software license has not yet been selected. Public access alone does not grant rights to redistribute, modify, or commercially reuse the source. The owner intends to make an official binary available for evaluation; broader licensing requires an explicit decision.
