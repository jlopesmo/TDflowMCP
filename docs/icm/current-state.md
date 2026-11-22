# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev` (Phase 0).** This file describes branch evidence, not `main` or any release.

## Verified
- GitHub repository `jlopesmo/TDflowMCP` existed and was **empty** (no refs) before Phase 0. The local checkout `F:\dev\TDflowMCP` contained only `.git` with `origin` configured.
- Phase 0 documentation set exists: ICM, product vision, research and comparison, proposed architecture, ADRs 0001–0009, milestones, operations procedures, RepoInspire register, agent instructions.
- `tools/check_docs.py` and its 12 unit tests exist and passed locally on 2026-11-22 (see [validation-index](validation-index.md)).
- An independent review was performed and its findings were addressed ([record](../research/2026-11-22-phase-0-independent-review.md)).
- Phase 0 commit and push: see [Phase 0 commit record](#phase-0-commit-record) below.

## Phase 0 commit record
- Foundation commit `82b64f1` pushed to `origin/dev` on 2026-11-22 (new branch; push output confirmed). This record was added by the follow-up commit.
- `main` does not exist on `origin` (owner decision D6). GitHub may therefore show `dev` as the default branch.
- CI on this push: **not observed** (no `gh` CLI; check the Actions tab).

## Not implemented (nothing below exists)
- No MCP server, protocol layer, policy engine, identity, audit store, dashboard, Code Mode, experiments, build pipeline, `.tox` or in-TD tests.
- No TouchDesigner session was launched, and no TD project was opened or modified.
- No CI run has been observed.
- Code Mode details from Cloudflare's codemode/mcp page are **unverified first-hand** (TLS interception blocked the fetch).

## Known gaps
- Original `mcp_bridge.tox` and its audit were **not found** on this machine (owner decision D5).
- Minimum TD build undecided (D2). The local build 2025.32440 predates the 2025.33230 minimum documented by TDMCP and Embody.
- No license (D3). `main` branch does not exist (D6).

## Next action
Owner reviews Phase 0 on `dev` and decides D1–D8 in the [roadmap](roadmap.md#owner-decisions-pending). Then Phase 1 starts with spikes S1–S3 ([plan](../milestones/phase-1-core-server.md)).
