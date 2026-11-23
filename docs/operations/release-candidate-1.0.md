# TDflowMCP 1.0 release candidate: changelog, compatibility and acceptance checklist

**Status:** candidate for the owner's acceptance (owner D20). This is **not a release**: nothing is merged to `main`, tagged or published. The release steps that remain are the owner's ([verification and release §4](verification-and-release.md#4-release-proposed)).

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

## Compatibility matrix (tested only)
| Component | Tested | Result | Evidence |
|---|---|---|---|
| TouchDesigner (Windows 11) | **2025.32440** (declared minimum) | All in-TD suites pass | [validation index](../icm/validation-index.md) |
| TouchDesigner newer builds (2025.33230+) | **not tested** (not installed) | — | — |
| macOS / Linux | **not tested** | — | — |
| Claude Code | **2.1.289** | Live onboarding and dogfood sessions pass | validation index |
| Codex CLI, Cursor, VS Code Copilot | documented instructions only, **no live test** | — | [current state](../icm/current-state.md#not-observed--unverified) |
| Python (component) | TouchDesigner's bundled 3.11.10 | Unit tests pass | validation index |
| Python (tools, tests) | CPython 3.11.8 | Unit tests pass | validation index |

No other compatibility is claimed.

## Acceptance checklist for the owner
- [x] Unit tests, doc checker and secret scan pass on the candidate commit.
- [x] `.tox` built reproducibly from source (two builds, equal source digest).
- [x] All in-TD suites (1A, 1B, gate, memory, onboarding) pass on 2025.32440.
- [ ] The package is built from a clean worktree of the candidate commit, and the suites pass on the **packaged** `.tox` (see the validation index entry for the candidate).
- [x] Independent reviews for every security-sensitive increment (reviews 14–17 in this phase), findings addressed.
- [x] Real coding-agent dogfooding of the new tools (round 3 in the validation index; its one friction point fixed).
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
