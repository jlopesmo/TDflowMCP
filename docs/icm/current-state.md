# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev`. Phase 1A has started (S5 executed; TD-independent preparation in progress).** This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- Private repository `jlopesmo/TDflowMCP`. `main` was created at `066dbd0` (Phase 0 foundation) and was **not changed** after that. Development continues on `dev`.
- **Phase 0 docs:** ICM, vision P1–P17, research and review records, architecture, ADRs 0001–0013, milestones, operations and the RepoInspire register. The original bridge is preserved in `reference/historical/`.
- **ADR status:** ADR-0010 (Setup Assistant) is **Conditionally Accepted** for Phase 1A (owner, 2026-11-22). The startup rule is now unambiguous ([security model §8.2](../architecture/security-model.md#82-bind-verification-gate)): no application request is served before the effective loopback-only binding is verified, failure closes the listener immediately, and builds without `localaddress` never activate a listener.
- **Spike S5 executed on TouchDesigner 2025.32440** in a disposable, text-generated project only ([findings](../research/2026-11-22-s5-compatibility-2025.32440.md)).
  - **Pass:**
    - build identification; Python 3.11.10; required libraries including `jsonschema` 4.23.0;
    - custom-parameter APIs, the `ui.clipboard` text round-trip, and `onStart` firing on load (the pre-save hook exists, but its firing is untested);
    - callbacks run on the main thread;
    - the OS listener-table reader (cross-checked against `netstat` in-session);
    - the bind gate's fail-closed behaviour.
  - **Fail (security):** the Web Server DAT has **no `localaddress`** and binds `0.0.0.0`, so **2025.32440 cannot meet TDflowMCP's security requirements**.
  - **Unknown on this build:** browser launch end-to-end, HTTP header/body/latency behaviour, and port-conflict behaviour (they need a verified listener, so they move to S1).
  - **Deviation:** one unsafe test step (a second listener after the gate had closed) was recorded and corrected.
- **TD-independent Phase 1A preparation (code exists, unit-tested; not integrated with TouchDesigner):**
  - `src/tdflowmcp/net/bindverify.py`: capability check (step 0), verdict, and the `VERIFYING → SERVING`/`CLOSED` state machine;
  - `src/tdflowmcp/net/oslisteners.py`: OS listener-table parser and reader;
  - `src/tdflowmcp/gate.py`: per-route gate;
  - `src/tdflowmcp/compat.py`: compatibility checks;
  - `src/setup/troubleshooting.json`.
  - **76 unit tests pass** on CPython 3.11.8 **and** on TouchDesigner's bundled Python 3.11.10 (1 test skipped: non-Windows only). This includes the fixes from [review 3](../research/2026-11-22-s5-review.md): terminal `CLOSED`, `CLOSING`/`STUCK` close confirmation, path validation, header and body hardening, a required PID filter, and the fact contract. Boundary tests prove that `src/tdflowmcp` imports neither `td` nor process or installer modules.
- The doc checker passes (see [validation-index](validation-index.md)).

## Not implemented (nothing below exists)
- No TD adapter, component shell, custom-parameter build, Setup Assistant page, listener integration, `.tox` builder or in-TD test runner.
- No MCP protocol, policy engine, identity, audit store, Skills, continuity services, `tdflowProject` or dashboard.
- No production `.tox`.
- No third-party binary has been downloaded.

## Not observed / unverified
- **CI:** no GitHub Actions run has been observed for any commit. No GitHub integration is available in this environment (no `gh`, no GitHub connector; a credential-based API read was denied earlier), and the repository is private ([B3](roadmap.md#open-blockers-and-owner-actions)).
- Everything that depends on TouchDesigner **2025.33230+** (S1–S4, and S5's remaining unknowns), because that build is not installed ([B1](roadmap.md#open-blockers-and-owner-actions)).
- Cloudflare's codemode/mcp page could not be fetched first-hand (TLS interception).
- TDMCP and TDMCPSkills facts are documentation-level only; nothing was tested hands-on.

## Commit record
- `82b64f1`, `b70000a`, `066dbd0`, `380d317`: Phase 0, as recorded previously. `main` = `066dbd0`.
- The S5/Phase 1A-prep commit is reported in this change's hand-off and recorded by a follow-up commit on `dev`.

## Next action
1. **Testing gate:** S1–S4 and 1A acceptance need TouchDesigner 2025.33230+ (B1). Work stops there until it is available.
2. Until then, the only safe TD-independent next step is the **TD adapter interface design + fakes** for items 9–10 (no TouchDesigner), and only with owner go-ahead.
