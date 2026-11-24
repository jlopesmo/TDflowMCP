# TDflowMCP 1.0 release candidate: changelog, compatibility and acceptance checklist

**Current candidate: `1.0.0rc4`** (owner D23: the rc3 restart finding explained and fixed, automated acceptance, UX-08/UX-09, review 20; [record](../research/2026-11-24-rc4-restart-and-acceptance.md)). It follows `1.0.0rc3` (one-click agent connection, owner D22, [ADR-0015](../decisions/0015-consent-based-client-connection.md)), whose hands-on owner acceptance passed connection, real agent work and suspension, and found the restart display issue. It replaces `1.0.0rc2` (`132e8c3`, package `TDflowMCP-1.0.0rc2-132e8c3317`, tox `dfabfaca…`); the owner's acceptance of rc2 found that first-time users still handled tokens, environment variables and MCP configuration. Commit and package hashes for rc3 are in the [validation index](../icm/validation-index.md) and [current state](../icm/current-state.md#commit-record).

## Changes in rc3
- **Connect Agent → Approve → start working** for Claude Code (CLI and VS Code extension):
  - a consent dialog in TouchDesigner;
  - one user-scope `tdflow` entry with a fixed `headersHelper`;
  - the token only in the OS-protected store (Windows DPAPI; macOS Keychain, not validated);
  - the helper releases it only after the listener proves the project's key;
  - backup, rollback, Repair Connection and Disconnect Agent.
- **Other agents** (Codex, Cursor, VS Code Copilot): a short manual fallback, with the reason stated.
- **Diagnostics feedback:** Running…, Checks done, Last Check, counts.
- **Status lines** fit the default parameter window (26 characters).
- **Instructions** are client- and platform-specific; there are no Setup-page or contradictory steps.
- **The browser one-click ticket is retired:** pages opened from TouchDesigner pair through a TouchDesigner dialog with number matching.
- Independent security review 19 (0 High, 4 Medium, all addressed) and a usability review.
- See the [record](../research/2026-11-24-one-click-connection.md).

## Changes in rc2
- Automatic and manual diagnostics; four readiness states; four parameter pages; context-aware Next Step.
- Coding Agent menu with Auto; Connect Agent; Copy Token.
- Native management of agents, scopes, tokens and approvals; one-click browser sign-in; dashboard parity (connection column, New token).
- Security review 18 addressed.
- Install guide rewritten for the TouchDesigner-first flow.
- See the [record](../research/2026-11-23-first-run-ux.md).

**Status:** candidate for the owner's acceptance (owner D20, D21). This is **not a release**: nothing is merged to `main`, tagged or published. The release steps that remain are the owner's ([verification and release §4](verification-and-release.md#4-release-proposed)).

## Changelog (from the Phase 0 foundation on `main`, `066dbd0`)
- **Component (1A):**
  - a drop-in `.tox` with a Setup Assistant;
  - the bind-verification gate: exclusive `127.0.0.1` bind, positive OS evidence, and re-verification, also in the background while the window is minimized;
  - compatibility checks and troubleshooting.
- **Secure MCP core (1B):**
  - MCP over Streamable HTTP, protocol eras 2025-06-18 and 2026-07-28;
  - per-agent tokens and roles (observer, builder, operator);
  - scoped grants, hard denies, a creation allowlist and a parameter denylist;
  - expressions, references and code classified as `execute`;
  - a kill switch;
  - a mandatory, hash-chained security audit with keyed checkpoints, durable before execution and outcome;
  - verified undo.
- **Onboarding (1C):**
  - generated connection instructions (token always `${TDFLOW_TOKEN}`);
  - a consent-based Skills install with version checks;
  - the audit worker (`synchronous=FULL`);
  - project-local storage with private credentials.
- **Continuity (2A):**
  - optional project memory: task checkpoints and verified handoffs, embedded or in a project folder;
  - the `memory:repo:write` capability (D20).
- **Governance (2B):**
  - approvals that run only on the identical re-call;
  - the `/ui/` dashboard: approvals, live activity, audit search, reports, sessions, agents, tokens, grant editing, settings, kill switch, leases;
  - staged code-running changes: code-DAT text, and Execute DATs created switched off.
- **Phase 3:**
  - Code Mode (`tdflow_execute_script`): off by default, always approval of the exact text, not a sandbox;
  - experiments, inert snapshots and promote;
  - approval-only project save.
- **Phase 4:**
  - `tdflow_describe_network` with hints;
  - `tdflow_capture_top`;
  - `tdflow_lease_scope`;
  - the Code Mode benchmark.
- **Tool surface:** 29 tools, `tool_surface_version` 1 (all additions additive). Skill `tdflow-core` 1.5.
- **First-run UX (rc2):** diagnostics, readiness, native administration, one-click sign-in (above).

## Compatibility matrix (tested only)
| Component | Tested | Result | Evidence |
|---|---|---|---|
| TouchDesigner (Windows 11) | **2025.32440** (declared minimum) | All in-TD suites pass | [validation index](../icm/validation-index.md) |
| TouchDesigner newer builds (2025.33230+) | **not tested** (not installed) | — | — |
| macOS / Linux | **not tested** | — | — |
| Claude Code CLI (Windows) | **2.1.289** | Automatic connection through the written user-scope entry (`claude mcp list` ✔ Connected); live session; restart, revoke, repair, disconnect | validation index |
| Claude Code VS Code extension (Windows) | engine **2.1.330** (the extension's bundled binary) | Connects through the same written entry (`mcp list` ✔ Connected). **The chat UI itself: owner acceptance step, not executed by an agent** | validation index |
| Claude Code on macOS | **not tested** | Automatic setup implemented from documentation only | — |
| Codex CLI, Cursor, VS Code Copilot | manual fallback instructions only, **no live test** | — | [current state](../icm/current-state.md#not-observed--unverified) |
| Python (component) | TouchDesigner's bundled 3.11.10 | Unit tests pass | validation index |
| Python (tools, tests) | CPython 3.11.8 | Unit tests pass | validation index |

No other compatibility is claimed.

## Acceptance checklist for the owner
- [x] Unit tests, doc checker and secret scan pass on the candidate commit.
- [x] `.tox` built reproducibly from source (two builds, equal source digest).
- [x] All in-TD suites (1A, 1B, gate, memory, onboarding) pass on 2025.32440.
- [x] rc2: the package is built from a clean worktree of the candidate commit (`132e8c3`, `dist/TDflowMCP-1.0.0rc2-132e8c3317`, tox SHA-256 `dfabfaca7a048601…`, source digest `5b014335…`), and every suite (1A, 1B, gate, memory, onboarding, native first run with a live agent) passes on the **packaged** `.tox` (validation index). (rc1: `348360a`, package `TDflowMCP-1.0.0rc1-348360a56b`, tox `394e10439ed3f3cf…`.)
- [x] Independent reviews for every security-sensitive increment (reviews 14–18), findings addressed.
- [x] rc2: the TouchDesigner-only first run (drop-in → diagnostics → Connect Agent → Copy Token → Enable MCP → a live Claude Code session → Connected → Suspend/Resume → restart) passes on the candidate build (validation index).
- [x] Real coding-agent dogfooding of the new tools (round 3 in the validation index; its one friction point fixed).
- [x] rc3: the package is built from a clean worktree of the candidate commit (`f033b72`, `dist/TDflowMCP-1.0.0rc3-f033b72d48`, tox SHA-256 `ffbb3ea1fa404d66…`, source digest `b137a685…`, zip `6d094b8b…`). Every suite (1A, 1B, gate, memory, onboarding, native one-click connection with live clients) and the visual review pass on the **packaged** `.tox` (validation index).
- [x] rc3: the one-click connection on the candidate build. The real Claude Code CLI and the VS Code extension's engine connect using only the configuration TDflowMCP wrote, a live session works with no token step, and restart, revocation, repair, a failed setup and Disconnect all pass (validation index).
- [x] rc3: independent security review 19 and the usability review, findings addressed (record).
- [x] rc4: the full acceptance sequence A–G is automated with real TouchDesigner processes and real Claude Code clients, including restart, a second restart, an abrupt kill and recovery ([matrix](../research/2026-11-24-rc4-restart-and-acceptance.md#acceptance-matrix-automated)).
- [x] rc3 owner acceptance (hands-on, 2026-11-24): connection, `tdflow_whoami` in the VS Code chat, operators created, Suspend/Resume. The restart finding was a display issue (rc4).
- [ ] **rc4 owner check on the real profile** (optional; the parts automation cannot do, because they need the owner's own approval in TouchDesigner and the VS Code chat UI; **not executed by an agent**):
  1. Replace the component in a copy of the rc3 test project with the rc4 `.tox`, or reopen the project with rc4 installed. Expected: Next Step `Turn on Enable MCP.`, Agents `not loaded (MCP off)` (that project predates the rc4 summary).
  2. Turn on Enable MCP; in VS Code start a new Claude Code chat (or `/mcp` → reconnect `tdflow`) and call `tdflow_whoami`.
  3. Save, restart TouchDesigner and VS Code, repeat step 2.
  4. Optionally Disconnect Agent and check that `~/.claude.json` no longer has `tdflow`.

- [ ] **macOS:** automatic setup not validated (no Mac).
- [ ] **CI evidence:** no GitHub Actions run has been observed (B3). Release procedure step 1 requires observed CI.
- [ ] **Latest TD build:** not tested (release procedure step 1 asks for the minimum and the latest build).
- [ ] Owner acceptance; then the owner's PR `dev → main`, tag, clean build from the tag and publication (not done by agents).

## Known limits carried into 1.0
These are listed in their records ([Code Mode](../research/2026-11-23-phase-3-code-mode.md#limits), [experiments](../research/2026-11-23-phase-3-experiments.md#limits), [Phase 4](../research/2026-11-23-phase-4-extensions.md#limits), [minimized window](../research/2026-11-23-phase-2b-increment-2.md#limits)):
- in-process Python is not a sandbox, so approval is the control;
- MCP calls take about 80–150 ms each (durable audit off the main thread);
- Windows-only minimized-window detection;
- leases do not survive a restart;
- snapshots live in the project;
- not built: file snapshots outside the project, host-project source-first (TDXN), raw Python execution, and ADR-0014 stage 1.
