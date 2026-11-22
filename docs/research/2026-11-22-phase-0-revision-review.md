# Phase 0 revision — independent review record (review 2)

**Date:** 2026-11-22
**Reviewer:** a separate AI agent instance. It was given the repository, the revision diff and the owner's revision requirements, but not the author's conclusions. It worked read-only.
**Scope:** all Markdown, the tooling, CI, ignore rules and historical references.

The reviewer's own checks: `check_docs.py` reported only the two expected missing-link problems, the unit tests passed (12), no personal data was found in `reference/historical`, and `reference/third_party/` is ignored.

## High

| # | Finding | Disposition |
|---|---|---|
| H1 | The "missing Origin rejected" rule would break the Setup page's own same-origin GETs, and it was unclear whether `/setup/api` counts as `/api` | **Fixed.** Added a per-route gate table ([security model §8.1](../architecture/security-model.md#81-per-route-gate-table)): reads need Host + an `X-TDflow-Request` header (forces a CORS preflight, which is never granted); mutations need exact Origin + session + CSRF |
| H2 | Pairing code stored in a saved parameter; entropy and attempt limits unspecified | **Fixed.** Reverse pairing: the browser holds the secret, TD shows a non-secret match code, and the user approves with a pulse. One pending request at a time, 2-minute expiry, rate limits ([§2.5](../architecture/security-model.md#25-dashboard-and-setup-admin-sessions)) |
| H3 | Generated `--header "…${TDFLOW_TOKEN}"` lets the shell expand the token into the client config (or produces an empty one in PowerShell) | **Fixed.** Single-quoted literal placeholders, user/local scope only, client runtime expansion marked [verify in 1C], and a 1C test that no config contains the token |

## Medium

| # | Finding | Disposition |
|---|---|---|
| M1 | `main` claimed as created before it was | **Fixed.** Worded as pending, with observed evidence recorded in current-state |
| M2 | Contradiction on writing client config | **Fixed.** Client connection is instruction-only; only Skills files are ever written (ADR-0011, security §10, setup §5) |
| M3 | 1A needs a `.tox`, but packaging was in 1C | **Fixed.** 1A uses an S2 dev build; 1C is release packaging (vision P1, Phase 1) |
| M4 | ADR-0009 gates cited the wrong items | **Fixed** |
| M5 | Proposed ADRs needed earlier than B5 said | **Fixed.** 0010 before 1A; 0012/0013 before 1B; 0011 before 1C |
| M6 | Pins and verifications tied to 2025.32440 rather than the target; S5 artifact unclear | **Fixed.** Pins marked provisional and re-pinned in S1/S5; S5 uses a separate 32440 dev build (ADR-0003/0006/0007, stack) |
| M7 | Memory authority can be forked (Save As, copies) or rewound; project key undefined | **Fixed.** Fork/rewind detection with `FORKED`/`REWOUND` read-only states plus "Claim authority"; project key defined ([continuity §2](../architecture/continuity-and-memory.md#2-authority-model-avoids-conflicting-authoritative-records)) |
| M8 | "Tell Your Coding Agent" and parameters leaked environment text and paths | **Fixed.** Allowlisted fields; probe text excluded; `which` reports present/absent only; unexpanded `%LOCALAPPDATA%` display; resume section reference-only by default |
| M9 | Probe could block TD and is spoofable; `/.well-known` leaked project info; bind test could give false results | **Fixed.** ≤200 ms non-blocking untrusted probe; `/.well-known` returns `{product, version}` only; bind test fails closed when ambiguous, with S1 defining the evidence |
| M10 | Assisted write not hardened | **Fixed.** Registry-listed targets only; rejects symlinks, junctions and `..`; atomic writes; bundled hashes verified |
| M11 | `tdflowProject` ancestors unprotected; tags spoofable; memory actions incomplete | **Fixed.** Ancestor protection; protected set by registered `op.id` (tags used for discovery only, reserved tags unchangeable by agents); memory read/create/update/delete with delete denied by default |

## Low

| # | Finding | Disposition |
|---|---|---|
| L1 | Phase label drift ("2b", generic "Phase 1") | **Fixed.** Secret scanning assigned to 1A item 6 |
| L2 | Stale "bridge absent" text | **Fixed** |
| L3 | Bridge-derived tools missing from the tool surface; skill-version wording | **Fixed** |
| L4 | Checker conflated "Conditionally Accepted" with "Accepted"; no tests for new required files | **Fixed.** Distinct status token plus 2 new tests (14 total) |
| L5 | Vision P12 implied confirmed installs were allowed | **Fixed.** TDflowMCP never installs |
| L6 | `tdflow_project_info` leaked top-level names | **Fixed.** Filtered by grant |
| L7 | CI trigger wording; zip not diffable or scanned | **Fixed.** Source extracted as text under `reference/historical/mcp_bridge/source/` (byte-preserved, never run) |

## Retained strengths (reviewer)
- CI recorded honestly as "not observed".
- No implementation claims.
- ADR status consistency.
- Port and build-target consistency.
- Third-party separation and the hands-on-testing rule.
- The bridge preserved with hashes.
- The TDMCP-companion rejection reasoned out.
- Setup kept separate from the dashboard.
- 1B enforcement gates Phases 3–4.
