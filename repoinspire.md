# TDflowMCP repository inspiration register

**Project:** jlopesmo/TDflowMCP · **Project ID:** `tdflowmcp` · **Last register review:** 2026-11-22

## Purpose and authority

This is the project-local record of external repository references: evaluation status, license review and decisions. It follows the owner's RepoInspire convention used in sibling projects. **A listed reference is not permission to install, run, copy from, or connect to it.** Adoption requires an ADR, and code reuse also requires [ADR-0008](docs/decisions/0008-upstream-reuse-policy.md) conditions. Licensing notes are research observations, not legal clearance.

**Status vocabulary:** `candidate` · `evaluating` · `planned` (needs an explicit project decision) · `actively_using` (needs evidence) · `rejected` · `retired`. **Use types:** `inspiration`, `code_reuse`, `dependency`.

Evidence for all entries: [landscape research 2026-11-22](docs/research/2026-11-22-landscape-and-comparison.md). No compatibility tests were run. All "user-decided priority" fields are **not set**.

### Original TDflowMCP predecessor: `mcp_bridge.tox` (historical)
- **Location:** supplied by the owner on 2026-11-22. The audit and extracted source are committed in [reference/historical/mcp_bridge](reference/historical/mcp_bridge/README.md). The original `.tox` (SHA-256 `4386bfe5…`) is kept git-ignored at `.tmp/references/`, because it embeds unreviewed historical log rows ([ADR-0008](docs/decisions/0008-upstream-reuse-policy.md))
- **Review:** [bridge review](docs/research/2026-11-22-mcp-bridge-review.md)
- **Why relevant:** the owner's original bridge and its audit; requirements and lessons for TDflowMCP
- **Adoption status:** `retired` as an implementation (superseded by TDflowMCP). Preserved as a **historical reference** by owner decision (2026-11-22) · **Use type:** `inspiration` (historical)
- **License:** owner's own work. **Constraints:** never built, loaded into real projects or treated as source

### TouchDesigner/TDMCP
- **URL:** https://github.com/TouchDesigner/TDMCP · **Added:** 2026-11-22
- **Why relevant:** Official in-TD MCP server: tool set, OAuth consent flow, undo wrapping, loopback/Origin posture
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` (documentation-level only) · **Selected use:** undecided
- **Reported license:** TDMCP Shared Use License + Tool Terms of Use (non-OSI, revocable, TD-only use, AI-provider restrictions). **License review:** `documentation_reviewed` 2026-11-22
- **Constraints:** Do not copy code, skills or prompts, and do not bundle its `.tox` (ADR-0008). No Python source in the repo. Requires TD 2025.33230+
- **Hands-on testing rule (owner, 2026-11-22):** binaries are downloaded only when hands-on testing requires them. The official GitHub release goes into git-ignored `reference/third_party/tdmcp-<version>/`, with version, URL, SHA-256 and license terms recorded in `docs/research/`. It is tested only in a disposable TouchDesigner project, never mixed into `src/`, and nothing from it is incorporated
- **Upstream aliases / forks:** none verified

### TouchDesigner/TDMCPSkills
- **URL:** https://github.com/TouchDesigner/TDMCPSkills · **Added:** 2026-11-22
- **Why relevant:** Official agent Skills for TDMCP: 19 skills, repo-wide SemVer (1.4.3), `.tdmcp-skills.json` install manifest, multi-client install paths, Claude Code plugin marketplace
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` (structure and coexistence only) · **Selected use:** undecided
- **Reported license:** the TDMCP Shared Use License + Tool Terms of Use, identical to TDMCP's. **License review:** `documentation_reviewed` 2026-11-22
- **Constraints:** Do not copy skill content. The `td-` prefix is reserved by Derivative, so TDflowMCP uses `tdflow-` and never touches `td-*` skills or their manifest
- **Research evidence:** [bridge review §5](docs/research/2026-11-22-mcp-bridge-review.md#5-evaluation-tdmcp-as-foundation-vs-own-core)

### dylanroscover/Embody (incl. Envoy, Convoy)
- **URL:** https://github.com/dylanroscover/Embody · **Added:** 2026-11-22
- **Why relevant:** Best-in-class externalization (TDXN), in-TD MCP via SDK + stdio bridge, in-TD test runner, release self-update with sha256
- **Adoption status:** `candidate` · **Proposed use:** `inspiration`. TDXN interop to be evaluated in Phase 4 · **Selected use:** undecided
- **Reported license:** MIT. **License review:** `documentation_reviewed` 2026-11-22
- **Constraints:** **Never install into this repository** (open issue #194: may delete `.git/`). No auth by default. Fast release cadence. Requires TD 2025.33230+

### 8beeeaaat/touchdesigner-mcp
- **URL:** https://github.com/8beeeaaat/touchdesigner-mcp · **Added:** 2026-11-22
- **Why relevant:** Versioned server↔component API contract; separate-Node-server reference architecture
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` · **Reported license:** MIT (`documentation_reviewed`)

### Pantani/tdmcp
- **URL:** https://github.com/Pantani/tdmcp · **Added:** 2026-11-22
- **Why relevant:** Opt-in bridge token and exec-disable flag; embedded operator reference
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` · **Reported license:** MIT (`documentation_reviewed`)
- **Constraints:** README recommends `exec(urlopen(...))` installation. Do not follow that pattern

### johnsabath/touchdesigner-mcp
- **URL:** https://github.com/johnsabath/touchdesigner-mcp · **Added:** 2026-11-22
- **Why relevant:** Dependency-free MCP Streamable HTTP inside a Web Server DAT, closest to ADR-0002
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` · **Reported license:** **none detected** (all rights reserved). Do not copy

### twozero-team/twozero-td-mcp
- **URL:** https://github.com/twozero-team/twozero-td-mcp · **Added:** 2026-11-22
- **Why relevant:** Multi-instance routing in a commercial plugin
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` · **Reported license:** none detected. Do not copy

### r-spring/touchdesigner-mcp-server (formerly bottobot/touchdesigner-mcp-server)
- **URL:** https://github.com/r-spring/touchdesigner-mcp-server · **Added:** 2026-11-22
- **Why relevant:** Offline operator/Python documentation corpus as MCP (docs-only)
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` · **Reported license:** MIT (`documentation_reviewed`)

### cloudflare/agents — packages/codemode
- **URL:** https://github.com/cloudflare/agents/tree/main/packages/codemode · **Added:** 2026-11-22
- **Why relevant:** Code Mode pattern: typed API generation, search/describe/execute, approve/reject/rollback, pluggable executor
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` ([ADR-0004](docs/decisions/0004-code-mode-strategy.md)) · **Reported license:** MIT (README only; npm returned 403)
- **Constraints:** JS, Workers-bound default executor. The primary docs page could not be fetched (TLS interception), so re-verify

### modelcontextprotocol/python-sdk
- **URL:** https://github.com/modelcontextprotocol/python-sdk · **Added:** 2026-11-22
- **Why relevant:** Reference implementation for protocol conformance
- **Adoption status:** `candidate` · **Proposed use:** `inspiration` (conformance reference). Not proposed as an in-process dependency ([ADR-0003](docs/decisions/0003-protocol-implementation.md), Proposed; becomes `rejected` for that use only if the owner accepts ADR-0003) · **Reported license:** MIT
- **Reason for proposed non-adoption:** heavy compiled dependencies not bundled with TD; PyPI access not assured; threading model conflict
