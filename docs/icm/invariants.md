# TDflowMCP — Invariants

## Owner-confirmed (owner brief and owner revision, 2026-11-22)

1. **Permissions are enforced server-side.** CRUD permissions, operator scopes and agent identity are checked by TDflowMCP itself, never delegated to client settings, prompts or the dashboard UI.
2. **Every agent action is attributable and auditable.** Session monitoring, audit history, reporting and approvals are core capabilities, not add-ons.
3. **Source-first.** The repository's text source is the truth; packaging and automated testing derive from it.
4. **Upstream implementations are not modified or redistributed without confirmed license permission** ([ADR-0008](../decisions/0008-upstream-reuse-policy.md)).
5. **Do not assume a separate Node.js server is necessary.** Frameworks and dependencies are selected only after evaluating alternatives (recorded in ADRs).
6. **Documented ≠ implemented.** No capability is claimed as implemented because it appears in architecture or plans. Only evidenced items enter `current-state.md`.
7. **Development must not alter real TouchDesigner projects.** Tests and experiments use isolated, disposable projects created for the purpose. Existing user projects are never modified (owner, 2026-11-22).
8. **ICM files are maintained alongside code changes** ([AGENTS.md §3](../../AGENTS.md#3-icm-maintenance-is-part-of-every-change-mandatory)).
9. **No unauthorized installations or configuration changes.** Setup, compatibility checks and troubleshooting diagnose and instruct. Installs never happen, and file writes only with explicit, previewed user confirmation (owner revision, 2026-11-22).
10. **No conflicting authoritative records.** Project memory has exactly one authoritative home: a Git repository **or** the embedded `tdflowProject` COMP (owner revision, 2026-11-22).
11. **Security before orchestration.** Advanced orchestration (Code Mode, experiments, multi-agent) starts only after the Phase 1B enforcement gates pass. The minimal setup experience stays separate from the governance dashboard (owner revision, 2026-11-22).
12. **The repository stays private**, and no public license is chosen until the owner decides (owner, 2026-11-22).

## Engineering guardrails

P-1 to P-13 come from ADRs 0002–0009, which are Conditionally Accepted. They bind once their validation gates pass, and a failed gate reopens them. P-14 comes from ADR-0010 (Conditionally Accepted). P-15 to P-17 come from Proposed ADRs 0011–0013.

- P-1 Bind to `127.0.0.1` by default; any non-loopback exposure requires TLS and a new ADR ([0002](../decisions/0002-in-process-mcp-host.md)).
- P-2 Validate `Origin` (MCP spec MUST) and `Host` (defence in depth) on every request, before any TouchDesigner access. Dashboard requests require an exact Origin plus a CSRF token.
- P-2a Effects that cause TD to evaluate Python (parameter expressions/binds, executable DAT text, executable op creation, script pulses) are classified as `execute`, never as plain `update`/`create` ([security model §3.3](../architecture/security-model.md#33-action-classification-of-tool-effects-security-critical)).
- P-3 Deny by default; explicit deny wins; all targets must pass; non-overridable hard denies on every TDflowMCP instance and its ancestors' flags ([0009](../decisions/0009-permission-model.md)).
- P-4 Raw Python execution is disabled by default, approval-gated, and labelled as policy-bypassing ([0004](../decisions/0004-code-mode-strategy.md)).
- P-5 Every mutating tool runs inside an undo block and records an undo label in audit.
- P-6 The audit log is append-only and hash-chained; secrets are redacted before storage ([0006](../decisions/0006-audit-store.md)).
- P-7 `src/tdflowmcp/` (core logic) never imports `td`; TD access only via `src/td_adapter/` ([0007](../decisions/0007-source-first-build.md)).
- P-8 No runtime dependency beyond TouchDesigner's bundled Python packages without an ADR ([0003](../decisions/0003-protocol-implementation.md)).
- P-9 Tokens, policies containing secrets, audit databases and logs never enter Git, the `.toe` or the `.tox`.
- P-10 No TDflowMCP code touches operators off TouchDesigner's main thread.
- P-11 Agents cannot save the host `.toe` unless an approval-gated grant allows it (not offered in Phase 1).
- P-12 Audit writes an intent event before any mutation; mutations fail closed if audit is unavailable.
- P-13 A kill switch suspends all agents immediately.
- P-14 The component never listens before a user action, and never activates a listener on a build whose Web Server DAT lacks `localaddress` (S5). No application request is served until the effective loopback-only binding is verified, and failed or ambiguous verification closes the listener immediately ([ADR-0010](../decisions/0010-setup-assistant-and-component-parameters.md), [§8.2](../architecture/security-model.md#82-bind-verification-gate)).
- P-15 Custom parameters, generated agent instructions and project memory never contain tokens or other long-lived secrets.
- P-16 Agent-authored memory and handoff text is labelled with its author and treated as untrusted data. Only server-derived facts are labelled verified ([ADR-0012](../decisions/0012-session-continuity.md)).
- P-17 Raw operator tools are hard-denied on the `tdflowProject` COMP. Memory changes go through memory tools with revision checks ([ADR-0013](../decisions/0013-project-memory-model.md)).
