# ADR-0011 — Agent integration, Skills and "Tell Your Coding Agent"

**Status:** Conditionally Accepted (owner, 2026-11-22), subject to its validation gates. **Decision 3 is amended by [ADR-0015](0015-consent-based-client-connection.md)** (owner D22, 2026-11-24): for Claude Code, connection is consent-based automatic setup; other clients stay instruction-only. The requirements (P13, P14) are owner-approved. Implemented in Phase 1C ([record](../research/2026-11-22-audit-worker-and-phase-1c.md)).

## Context
The owner requires MCP and agent Skills integration with client-aware installation and version management, and a dynamic "Tell Your Coding Agent" parameter covering setup, connection, repair and work resumption. Reference projects auto-write client configs and permission postures; Envoy writes `.claude/settings.local.json` with auto-approve defaults ([research](../research/2026-11-22-landscape-and-comparison.md)). That conflicts with TDflowMCP's server-side enforcement and its no-unauthorized-changes requirement.

## Decision
Adopt [agent-integration.md](../architecture/agent-integration.md) and [setup-assistant §6](../architecture/setup-assistant.md#6-tell-your-coding-agent):
1. A client registry with explicit support levels. A client counts as `verified` only with recorded test evidence.
2. Skills are authored in `src/skills/` and versioned with the tool surface. They are guidance, not enforcement.
3. **MCP client connection is instruction-only:** shell-specific commands keep `${TDFLOW_TOKEN}` literal and use user/local scope. TDflowMCP never writes client configuration. **Skills** default to instruction mode. **Assisted write** of Skills files needs a paired session, a registry-listed target, a preview and explicit confirmation, and it records a hash manifest. It rejects symlinks and `..`, writes atomically, never overwrites modified files, and never writes tokens.
4. Version management uses `tdflow_whoami`, Skills frontmatter and manifest comparison.
5. "Tell Your Coding Agent" is generated from allowlisted state fields and never contains secrets or environment-derived free text. Its resume section is reference-only by default, and agent-authored content is available only through `tdflow_handoff_resume`, fenced and labelled as untrusted.

**Amendment (owner D24, 2026-11-24): Copy Agent Prompt.** The main-page pulse that copied the full text is renamed
**Copy Agent Prompt** and copies a short first message for a new chat instead ([setup assistant §6.1](../architecture/setup-assistant.md#61-copy-agent-prompt-owner-d24)).
- When the connection is ready, it asks the agent to use the existing `tdflow` server, call `tdflow_whoami`, give an overview, check memory when relevant and authorized, respect approvals, and change nothing until given a task.
- Otherwise it is a recovery message that never claims a connection.
- It follows rule 5: allowlisted fields only, memory as counts only, and no permission claims (`tdflow_whoami` reports those).
- The full text stays under View Full Instructions.

## Alternatives
- Automatic config writing (Envoy-like): rejected.
- Shipping no Skills: weaker agent behaviour and higher token use.
- Copying TDMCP Skills: barred by ADR-0008.

## Validation gates
Phase 1C acceptance: Claude Code connects using only the generated text, and a Skills install/update/uninstall round-trip passes on a disposable target directory.
- **Live connection: passed** (Claude Code 2.1.289, the JSON entry taken from the generated text, `--strict-mcp-config`, token only in the environment; a scoped edit audited). The first attempt found missing MCP 2026-07-28 caching hints, which are now fixed.
- **Skills round-trip: passed** on a disposable home and project folder. Preview/confirm, stale preview refused, modified file never overwritten, uninstall keeps modified files, unlisted target refused, audited. Junction rejection is unit-tested on Windows.
- **Version mismatch: surfaced** on the Setup page and to the agent (`expected_skill_version`).
- Codex CLI, Cursor and VS Code Copilot remain `documented` (no live test).

## Amendment (owner D25, 2026-11-25): agents report their own capabilities
`tdflow_capabilities` lets an agent report its own effective capabilities and send the user to the dashboard's Capabilities page through Open Dashboard; Skill 1.6, the full instructions and the Copy Agent Prompt say so ([ADR-0016](0016-capabilities-page-and-permission-editing.md)). The generated texts still contain no secret and no claim about the agent's permissions: the server reports them.
