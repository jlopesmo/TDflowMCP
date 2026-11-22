# TDflowMCP — Roadmap

**Status:** Phase 0 is in progress on branch `dev`. All later phases are **Proposed** sequencing pending owner approval.

| Phase | Goal | Key outputs | Acceptance (summary) | Detail |
|---|---|---|---|---|
| **0 Foundation** | Research, architecture, ICM, repository rules | Docs, ADRs, doc checker, CI | Docs check passes, independent review addressed, pushed to `dev` | [phase-0](../milestones/phase-0-foundation.md) |
| **1 Secure core server** | Minimal MCP server in a built `.tox` with enforced policy and audit | Spikes S1–S4, protocol layer, identity + kill switch, policy engine, registry, 14 core tools, SQLite audit, undo, build + test harness | Scoped denial (incl. expression-as-execute) proven by tests; live Claude Code session; `.tox` built from source; unit + in-TD tests green | [phase-1](../milestones/phase-1-core-server.md) |
| **2a Dashboard (read-only)** | Observe | Admin pairing, sessions (audit grouping), agents, live event stream, audit search, report export | Events visible within 1 s; export verifies against checkpoint | [milestones](../milestones/README.md) |
| **2b Governance** | Decide | Approval queue + `tdflow_approval_status`, policy/token management, dashboard kill switch | Pending call executes only on identical re-call after approval, with targets re-resolved and policy re-checked; single-use | [milestones](../milestones/README.md) |
| **3 Safe experimentation, recovery + Code Mode** | Work fast safely | Experiment COMPs, pre-mutation snapshots/restore (recovery beyond in-memory undo), approval-only project save, Code Mode facade, search/describe API, raw exec (off by default) | Discarded experiment leaves host unchanged; snapshot restore after TD restart; Code Mode token benchmark measured | [milestones](../milestones/README.md) |
| **4 Extensions** | Intelligence | Visual analysis (TOP capture), debugging (cook trace), optimization hints, multi-agent leases, host-project source-first (TDXN interop evaluation) | Each extension behind policy with tests | [milestones](../milestones/README.md) |
| **Release 1.0** | Distribute | sha256 release manifest (signing via future ADR), docs, tested compatibility matrix | Owner approval; tested on declared minimum TD | [release procedure](../operations/verification-and-release.md) |

## Owner decisions pending

| # | Decision | Recommendation | Blocks |
|---|---|---|---|
| D1 | Accept ADR-0001 phasing and ADRs 0002–0007, 0009 | Accept as written, or annotate changes | Phase 1 merge to `main` |
| D2 | **Minimum TouchDesigner build.** Upstream projects require 2025.33230+; local install is 2025.32440 | Target 2025.32440+ for development, then decide after spike S1 whether 2025.33060+ (loopback-bind fix noted by TDMCP) is required; update local TD if so | Phase 1 S1 |
| D3 | TDflowMCP **license** (MIT / Apache-2.0 / proprietary) | Decide before first public release; Apache-2.0 if open, for its patent grant | Public release |
| D4 | Repository visibility (public/private) | Keep private until Phase 1 security review | — |
| D5 | Provide the original **`mcp_bridge.tox` and its audit** (not found on this machine) or confirm it is retired | Provide if available; its audit may add requirements | Phase 1 planning completeness |
| D6 | Creating `main`: the GitHub repo was empty, and only `dev` was pushed, so GitHub may show `dev` as default | Create `main` from the approved Phase 0 commit and set it as default | Release flow |
| D7 | Authorization to launch TouchDesigner for Phase 1 spikes (disposable test project only) | Approve for a scratch `.toe` under `.tmp/` (git-ignored) | Phase 1 S1–S4 |
| D8 | Default port | 9480 (avoids TDMCP 13316, Envoy 9870, 8beeeaaat 9981, Pantani 9980, johnsabath 9988) | Phase 1 |
