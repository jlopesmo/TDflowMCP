# Architecture

These documents describe **designs, not implemented behavior** (see [current-state](../icm/current-state.md)). Each one's authority comes from the ADR it cites in [docs/decisions](../decisions/README.md), whether Conditionally Accepted or Proposed.

| Document | Covers | Backing ADRs |
|---|---|---|
| [system-design.md](system-design.md) | Component diagram, responsibilities, request lifecycle, approvals, extensions, threading, deployment | 0002–0007, 0009 |
| [security-model.md](security-model.md) | Threat model, identity, sessions, kill switch, CRUD+execute scopes, approvals, audit, network, setup/integration and memory safety | 0005, 0006, 0009, 0010–0013 |
| [setup-assistant.md](setup-assistant.md) | Component states, essential custom parameters, compatibility checks, troubleshooting, "Tell Your Coding Agent", `/setup/` | 0010 |
| [agent-integration.md](agent-integration.md) | Client registry, Skills bundle, consent-first installation, version management | 0011 |
| [continuity-and-memory.md](continuity-and-memory.md) | State homes, single-authority project memory, `tdflowProject`, session logs, checkpoints, verified handoffs | 0012, 0013 |
| [tool-surface.md](tool-surface.md) | MCP tools per phase and conventions | 0009 |
| [source-of-truth.md](source-of-truth.md) | Source vs build artifacts, historical references, planned layout, module boundaries | 0007, 0008, 0013 |
| [technology-stack.md](technology-stack.md) | Stack and alternatives considered | all |
