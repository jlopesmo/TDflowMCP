# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev` (Phase 0 revision).** This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- Private GitHub repository `jlopesmo/TDflowMCP`. It was empty before Phase 0, and unauthenticated API requests return 404 (consistent with private).
- Phase 0 foundation commits `82b64f1` and `b70000a` were pushed to `origin/dev` (push output observed). The revision commit and `main` creation are recorded under [Phase 0 commit record](#phase-0-commit-record).
- The documentation set exists:
  - ICM, product vision (P1–P17), research records and two independent review records;
  - architecture (system, security, setup, integration, continuity/memory, tools, stack, source rules);
  - ADRs 0001–0013, milestones (Phase 0; Phase 1A/1B/1C), operations, RepoInspire register and agent instructions.
- The owner's original bridge was supplied, reviewed and preserved:
  - the audit and extracted source are committed in `reference/historical/mcp_bridge/` (hashes in its README);
  - the `.tox` is kept git-ignored, and its SHA-256 matches the audit.
- `tools/check_docs.py` and its unit tests pass locally (see [validation-index](validation-index.md)).

## Not implemented (nothing below exists)
- No component, custom parameters, Setup Assistant, compatibility checks, MCP server, policy engine, identity, audit store, Skills bundle, continuity services, `tdflowProject`, dashboard, Code Mode, build pipeline, `.tox` or in-TD tests.
- No TouchDesigner session has been launched. No TD project has been opened or modified. No third-party binary (e.g. TDMCP) has been downloaded.

## Not observed / unverified
- **CI:** no GitHub Actions run has been observed for any commit. There is no `gh` CLI, the repo is private, and a credential-based API read was denied in this environment ([roadmap B3](roadmap.md#open-blockers-and-owner-actions)).
- Cloudflare's codemode/mcp page could not be fetched first-hand (TLS interception).
- TDMCP and TDMCPSkills facts are documentation-level only, from the official repos; nothing was tested hands-on.

## Known gaps / blockers
See [roadmap — open blockers](roadmap.md#open-blockers-and-owner-actions). The main ones:
- TD 2025.33230+ is not installed locally (B1).
- CI observation (B3).
- The GitHub default branch (B4).

## Phase 0 commit record
- `82b64f1`: Phase 0 foundation. `b70000a`: push evidence record. Both are on `origin/dev`.
- The revision commit, its push, and the creation of `main` are reported in the hand-off for this revision. They are recorded here by the follow-up commit.

## Next action
1. The owner reviews the revision and accepts or revises ADR-0010 (needed before 1A).
2. The owner resolves B1 (install TD 2025.33230+).
3. Phase 1A spikes start, in disposable projects only.
