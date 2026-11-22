# TDflowMCP — Roadmap

**Status:** Phase 0 revision is in progress on `dev`. The sequencing below is **Conditionally Accepted** (owner, 2026-11-22, [ADR-0001](../decisions/0001-product-scope.md)) and binds only when the validation gates pass.

Principles (owner): put a **minimal setup experience first**, keep **security-first execution**, keep the **setup page separate from the governance dashboard**, and defer **advanced orchestration until enforcement is proven**.

| Phase | Goal | Key outputs | Acceptance (summary) | Detail |
|---|---|---|---|---|
| **0 Foundation** | Research, architecture, ICM, rules | Docs, ADRs 0001–0013, doc checker, CI, `main` created from the verified foundation | Docs check, independent review addressed, CI evidence recorded honestly | [phase-0](../milestones/phase-0-foundation.md) |
| **1A Setup shell** | A usable component before MCP exists | Spikes S1–S5, gate, compatibility checks, troubleshooting, essential parameters, read-only Setup Assistant, "Tell Your Coding Agent" (setup/repair) | Drop-in → Setup page; bind self-test; no installs or config changes | [phase-1](../milestones/phase-1-core-server.md#phase-1a--spikes-component-shell-minimal-setup-assistant-no-mcp) |
| **1B Secure core** | Enforcement proven | Policy, identity, kill switch, protocol, registry, audit, 14 core tools, session logs, paired Setup actions | Scoped denial incl. expression-as-execute; tamper/fail-closed; independent security review | [phase-1](../milestones/phase-1-core-server.md#phase-1b--secure-mcp-core-enforcement-first) |
| **1C Agent onboarding** | Agents connect correctly | Client registry, connection instructions, Skills install/update/version mgmt, `.tox` packaging | Claude Code connects from the generated text alone; safe Skills round-trip | [phase-1](../milestones/phase-1-core-server.md#phase-1c--agent-onboarding) |
| **2A Continuity + project memory** | Work survives sessions and agents | Task checkpoints, verified handoffs, resume section of "Tell Your Coding Agent", `tdflowProject` COMP, single-authority memory | False claims tagged `contradicted`; stale write → `MEMORY_CONFLICT`; mode migration preserves hashes | [continuity](../architecture/continuity-and-memory.md) |
| **2B Governance dashboard** | Observe and decide | `/ui` monitoring, audit search, reports, approvals, policy/token management | Approval executes only on identical re-call with re-check; events ≤ 1 s | [ADR-0005](../decisions/0005-dashboard.md) |
| **3 Safe experimentation, recovery, Code Mode** | Work fast, safely | Experiment COMPs, snapshots/restore, approval-only save, Code Mode facade, raw exec (off) | Discarded experiment leaves host unchanged; Code Mode benchmark measured | [ADR-0004](../decisions/0004-code-mode-strategy.md) |
| **4 Extensions + multi-agent** | Intelligence | Visual analysis, debugging, optimization hints, scope leases, host-project source-first (TDXN interop evaluation) | Each extension behind policy with tests | [system design §5](../architecture/system-design.md#5-extension-contract) |
| **Release 1.0** | Distribute | sha256 manifest, docs, tested compatibility matrix | Owner approval; tested on the declared minimum build | [release](../operations/verification-and-release.md) |

## Owner decisions (2026-11-22)

| # | Decision | Owner direction | Status |
|---|---|---|---|
| D1 | ADRs 0001–0007, 0009 | **Conditionally approved**, subject to revisions and recorded validation gates | Applied |
| D2 | Minimum TD build | **2025.33230+ provisional target**. Test older-build feasibility (S5) without bypassing security | Applied; see blocker B1 |
| D3 | License | **Defer** choosing a public license | Applied (ADR-0008) |
| D4 | Visibility | **Keep private** | Applied. Anonymous API returns 404, consistent with private |
| D5 | Original bridge | **Preserve as historical reference** | Applied: audit + source committed under `reference/historical/`; binary hash-recorded (B2) |
| D6 | `main` | **Create from the approved foundation after corrections and verification**; future development stays on `dev` | Performed after this revision's local checks and review; the observed result is recorded in [current state](current-state.md) |
| D7 | TD experiments | **Authorized only in isolated disposable projects**; never modify existing user projects | Applied (Phase 1 experiment rule) |
| D8 | Port | **9480 with conflict detection** | Applied |

## Open blockers and owner actions

| # | Item | Why it matters | Needed from owner |
|---|---|---|---|
| B1 | TD **2025.33230+** is not installed locally (only 2025.32440 and a legacy Python 3.9 build) | S1–S4 and the 1A acceptance criteria target 2025.33230+. The work PC may lack admin rights to install | Install 2025.33230+ (or name a machine that has it) before Phase 1A spikes |
| B2 | Original `mcp_bridge.tox` embeds unreviewed historical `textport_log` rows | The audit and source are preserved in Git; the binary is kept git-ignored (`.tmp/references/`) | Decide whether to commit the binary after reviewing its log rows (non-blocking) |
| B3 | CI runs not observable from this environment (no `gh`; credential-based API access denied) | CI evidence can't be recorded by the agent | Check the Actions tab and paste the result, or authorize a read-only method |
| B4 | GitHub default branch is `dev` (first push) | `main` should be default for releases | Switch the default branch to `main` in GitHub settings (UI only) |
| B5 | Accept or revise Proposed ADRs 0010–0013 | 1A builds on 0010. 1B needs 0010 (pairing), 0012 (session logs) and 0013 (`tdflowProject` hard deny). 1C needs 0011. 2A needs the rest of 0012/0013 | **0010 before 1A**; **0012, 0013 before 1B**; **0011 before 1C** |
