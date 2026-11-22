# Phase 0 independent review record

**Date:** 2026-11-22 · **Reviewer:** a separate AI agent instance given the repository and the owner brief, but not the author's conclusions. Read-only. · **Scope:** all Phase 0 Markdown, `tools/check_docs.py`, its tests, and the CI workflow.

This record is a summary of the findings and their disposition. It is not an owner approval.

## High

| # | Finding | Disposition |
|---|---|---|
| H1 | CRUD scopes don't stop code execution: parameter expressions, executable DAT text and executable op creation evaluate Python but were classified as `update`/`create` | **Fixed:** action classification table ([security model §3.3](../architecture/security-model.md#33-action-classification-of-tool-effects-security-critical)), invariant P-2a, tool-surface and Phase 1 acceptance updated |
| H2 | Results recorded as verified before they existed (validation placeholders, "dev on origin" before push) | **Fixed:** `validation-index.md` records actual runs; `current-state.md` states push status separately |
| H3 | Approval replay / TOCTOU, undefined canonical JSON, two redemption paths | **Fixed:** redemption only by identical re-call; targets re-resolved; policy re-evaluated; RFC 8785 JCS; full raw args shown (§4) |
| H4 | Dashboard CSRF across localhost ports; missing Origin accepted everywhere | **Fixed:** exact canonical Origin + CSRF header on `/api`/WS, missing Origin rejected there, single canonical origin; Phase 1 test for a loopback Origin on another port |
| H5 | Time budgets can't be enforced on TD's main thread | **Fixed:** cooperative checks + post-hoc reporting; residual risk documented (§7) |

## Medium

| # | Finding | Disposition |
|---|---|---|
| M6 | Self-protection too narrow (other instances, ancestor flags) | **Fixed:** hard denies by instance tag + ancestor flags/rename/move (§3.4) |
| M7 | Hard-deny lists inconsistent; recursive reads fail when the component is in scope | **Fixed:** unified list in ADR-0009 and the security model; recursive reads filter |
| M8 | Audit written only after execution; unkeyed chain; brute-forceable arg digests | **Fixed:** intent + outcome events, fail closed, keyed checkpoints, HMAC args (§6) |
| M9 | Unauthenticated flood → SQLite write per request | **Fixed:** aggregated `auth_fail_burst`, rate limiting moved to Phase 1 |
| M10 | Phase 1 contradictions (approval surface, undo guarantees, undo permissions); no undo spike | **Fixed:** approval → deny in Phase 1; spike S4; undo acceptance scoped to S4 findings; inverse-action permissions |
| M11 | Extensions placed inside the `td`-free core | **Fixed:** `src/extensions/` via `td_adapter` |
| M12 | CI lacks `jsonschema` | **Fixed:** test-only pin `jsonschema==4.23.0` in ADR-0003 (file created in Phase 1) |
| M13 | Build/compatibility feasibility (base `.toe`, build on min TD, runner licensing, bind on 2025.32440) | **Fixed:** ADR-0007 consequences, spike S2 scope, startup bind verification |
| M14 | Overclaims (johnsabath/TDMCP "proven", "no threads", repoinspire `rejected`, CI secret scanning) | **Fixed:** reworded as hypothesis / planned; repoinspire status `candidate` |
| M15 | Coverage gaps: sessions undefined, no kill switch, no recovery before Phase 3, no concurrency rule, matrix lacked performance/deployment rows, Claude Desktop auth | **Fixed:** sessions defined (§2.3), kill switch (§2.4, P-13), no-save rule (P-11), concurrency rule (§9), matrix rows added, Desktop limitation documented. Recovery beyond undo remains Phase 3 by design |

## Low

| # | Finding | Disposition |
|---|---|---|
| L16 | Local machine paths in docs vs. rule | **Fixed:** the rule now targets user-profile paths/personal details; documenting checkout and TD install paths is allowed |
| L17 | Tool count, "signed manifest", Host "spec MUST" wording | **Fixed** |
| L18 | Plaintext token in client config; linear token lookup | **Fixed:** env-var guidance; `tdf_<id>.<secret>` tokens |
| L19 | `check_docs.py` bugs (underscore slugs, image links, trailing `#`, outside-root links, substring status match, no index-status comparison, md-only privacy scan, missing tests) | **Fixed** and covered by new unit tests. Reference-style and multi-line links remain unsupported (documented limitation) |
| L20 | CI actions pinned by tag; no timeout | **Fixed:** pinned to commit SHAs (checkout v4.4.0, setup-python v5.6.0), `timeout-minutes: 10` |

## Retained strengths (reviewer)
Status discipline and evidence labels; deny-by-default with registry-enforced resolvers; non-blocking approvals; `td`-free core boundary; honest code-execution stance; ADR-0008 and the Embody #194 warning; risk-burning spikes; "not executed" marking.
