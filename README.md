# TDflowMCP

A source-first, AI-powered TouchDesigner development environment: an MCP server packaged as a reusable `.tox`, with server-side permissions, agent identity, audit logging, approvals, a local browser dashboard, safe experimentation and optional Code Mode orchestration.

> **Status: TDflowMCP 1.0.0 — released 2026-11-25 for evaluation. [Download v1.0.0](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.0).**
> - Version 1.0.0 is the frozen 1.0.0rc6 feature set with the final version label. Tests passed on Windows with TouchDesigner **2025.32440**, the only verified combination; newer builds and macOS are **not verified**.
> - Claude Code on Windows supports consent-based Connect Agent without copying credentials; other documented clients use a manual setup fallback.
> - MCP binds to `127.0.0.1` only, enforces agent scopes and approvals, and requires an intact durable security audit.
> - The `.tox` is a release asset built from the `v1.0.0` tag. It is deliberately excluded from source Git history.
> - Release record: [changelog, compatibility and checklist](docs/operations/release-candidate-1.0.md).
> - **Version 1.0.1 is being released.** It adds the same one-click connection for **OpenAI Codex on Windows** (Coding Agent: Codex → Connect Agent → Approve → new Codex chat): [1.0.1 release record](docs/operations/release-candidate-1.0.1.md). Until its download is published and verified, the download below is v1.0.0, which does not have it.

## Download and first run

1. Open the [v1.0.0 release](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.0) and download [`TDflowMCP.tox`](https://github.com/jlopesmo/TDflowMCP/releases/download/v1.0.0/TDflowMCP.tox) (and `SHA256SUMS` if you want the checksum file).
2. Check the download. In PowerShell: `Get-FileHash .\TDflowMCP.tox -Algorithm SHA256` must print

   `466a1b5e577c695e5c924e031116173b68af4f8172799317e9c9d103931d5358`
3. Save your TouchDesigner project, then drag `TDflowMCP.tox` into it. Use Windows with TouchDesigner 2025.32440, and try it first in a copy of a project.

There is no binary in the `main` branch: the tested `.tox` is built from the `v1.0.0` tag and attached to the release, not committed as a build output. The download is for evaluation ([License](#license)).

Then, in TouchDesigner:

1. Open the `TDflowMCP` parameter page; automatic diagnostics run, and **Run Diagnostics** remains available manually.
2. Leave **Coding Agent** on Auto, pulse **Connect Agent**, and approve the displayed plan. The approved Claude Code setup configures the local client securely.
3. Pulse **Copy Agent Prompt** and paste it into a new Claude Code chat. The agent verifies access with `tdflow_whoami` and awaits your task.
4. Use **Open Dashboard → Capabilities** to review or change supported agent permissions.

The browser dashboard is optional for initial setup. Keep TouchDesigner open and unminimized while an agent modifies the project. [Installation and security details](docs/operations/install.md).

**User experience** (Windows / Claude Code, verified): drag `TDflowMCP.tox` into a project → the compatibility checks run → pulse **Connect Agent** and approve the plan in TouchDesigner (Claude Code is configured with no token to copy; other agents get a short manual setup) → pulse **Copy Agent Prompt** and paste it as the first message of a new Claude Code chat → work under enforced, audited scopes (see and change what each agent may do on the dashboard's **Capabilities** page; ask the agent "What can you do?") → resume later from verified handoffs.

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

This repository is publicly readable, but a public software license has not yet been selected. Public access alone does not grant rights to redistribute, modify, or commercially reuse the source. The official `TDflowMCP.tox` attached to a GitHub Release may be downloaded and used for evaluation; broader licensing requires an explicit decision by the owner.
