# TDflowMCP

A source-first, AI-powered TouchDesigner development environment: an MCP server packaged as a reusable `.tox`, with server-side permissions, agent identity, audit logging, approvals, a local browser dashboard, safe experimentation and optional Code Mode orchestration.

> **TDflowMCP 1.2.0 was released for evaluation on 2026-11-26. [Download TDflowMCP-v1.2.0.tox](https://github.com/jlopesmo/TDflowMCP/releases/download/v1.2.0/TDflowMCP-v1.2.0.tox).**
> - v1.2 adds local operator discovery, approval-gated custom controls and native TOP outputs on reusable Base COMPs. The owner connects external consumers in TouchDesigner. Approved Python runs in TouchDesigner with access to the host.
> - The supported release configuration is **Windows with TouchDesigner 2025.32440**. Image capture requires the owner to lock the output TOP and keep bypass off on the TOP and its enclosing components.
> - Claude Code and Codex on Windows support consent-based Connect Agent without copying credentials. Codex was tested with the Codex CLI 0.160.0 and the Codex VS Code extension 26.1120; the Codex desktop app is not verified. Other documented clients use a manual setup fallback.
> - MCP binds to `127.0.0.1` only, enforces agent scopes and approvals, and requires an intact durable security audit.
> - The `.tox` is built from the `v1.2.0` tag. [v1.2 development and release record](docs/milestones/v1.2-component-authoring.md)
> - Earlier releases remain available. [v1.1.0](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.1.0) · [v1.0.1](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.1) · [v1.0.0](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.0.0)

## Download and first run

1. Download the versioned component. [TDflowMCP-v1.2.0.tox](https://github.com/jlopesmo/TDflowMCP/releases/download/v1.2.0/TDflowMCP-v1.2.0.tox) · [Byte-identical compatibility download](https://github.com/jlopesmo/TDflowMCP/releases/download/v1.2.0/TDflowMCP.tox)
2. Run `Get-FileHash .\TDflowMCP-v1.2.0.tox -Algorithm SHA256` in PowerShell and compare the result with the value below. [Published checksums](https://github.com/jlopesmo/TDflowMCP/releases/download/v1.2.0/SHA256SUMS)

   `330c1822cb6ef85c1f0751749ce00ef96ca5a047161e09800b0e6465833f3f9e`
3. Save your TouchDesigner project, then drag the verified `.tox` into it. Use Windows with TouchDesigner 2025.32440, and try it first in a copy of a project.

GitHub-hosted Windows and Ubuntu runners verified both binary downloads, the manifest, the checksums and the installation guide. [Download verification](https://github.com/jlopesmo/TDflowMCP/actions/runs/45859185563) · [Release notes and installation guide](https://github.com/jlopesmo/TDflowMCP/releases/tag/v1.2.0)

Then, in TouchDesigner:

1. Open the `TDflowMCP` parameter page; automatic diagnostics run, and **Run Diagnostics** remains available manually.
2. Leave **Coding Agent** on Auto (with Claude Code and Codex both installed, a dialog asks which one), or choose **Claude Code** or **OpenAI Codex**. Pulse **Connect Agent** and approve the displayed plan. The approved setup configures the local client securely.
3. Pulse **Copy Agent Prompt** and paste it into a **new** Claude Code or Codex chat. The agent verifies access with `tdflow_whoami` and awaits your task.
4. Use **Open Dashboard → Capabilities** to review or change supported agent permissions.

The browser dashboard is optional for initial setup. Keep TouchDesigner open and unminimized while an agent modifies the project. [Installation and security details](docs/operations/install.md).

**User experience** (Windows with Claude Code or Codex, verified): drag the TDflowMCP `.tox` into a project → the compatibility checks run → pulse **Connect Agent** and approve the plan in TouchDesigner (Claude Code and Codex are configured with no token to copy; other agents get a short manual setup) → pulse **Copy Agent Prompt** and paste it as the first message of a new chat → work under enforced, audited scopes (see and change what each agent may do on the dashboard's **Capabilities** page; ask the agent "What can you do?") → resume later from verified handoffs.

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
