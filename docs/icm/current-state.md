# TDflowMCP — Current state

**Observed 2026-11-22, branch `dev`. Phase 1A in progress. S5 and S6 were executed on TouchDesigner 2025.32440; the embedded loopback transport works inside TD; no TD upgrade is needed (owner D10).** This file records evidence only. Plans live in the [roadmap](roadmap.md).

## Verified
- Private repository `jlopesmo/TDflowMCP`. `main` was created at `066dbd0` (Phase 0 foundation) and was **not changed** after that. Development continues on `dev`.
- **Phase 0 docs:** ICM, vision P1–P17, research and review records, architecture, ADRs 0001–0013, milestones, operations and the RepoInspire register. The original bridge is preserved in `reference/historical/`.
- **ADR status:**
  - ADR-0010 (Setup Assistant) is **Conditionally Accepted** for Phase 1A (owner D9).
  - ADR-0002 is **Conditionally Accepted and revised after S6** (owner D10): the default transport is the embedded stdlib Python HTTP server, and the Web Server DAT is optional on builds with `localaddress`.
  - The minimum and oldest tested build is **2025.32440**.
- **Spike S5 (2025.32440, Web Server DAT)** ([findings](../research/2026-11-22-s5-compatibility-2025.32440.md)):
  - Pass: runtime, Python 3.11.10 and libraries, custom-parameter APIs, clipboard text, `onStart`, callbacks on the main thread.
  - **Fail (security):** the Web Server DAT has no `localaddress` and binds `0.0.0.0`. That transport is never used on this build.
- **Spike S6 (2025.32440, embedded Python transport)** ([findings](../research/2026-11-22-s6-embedded-python-transport.md), [review 4](../research/2026-11-22-s6-review.md)): **success criteria met.** Disposable, text-generated project only. Final runs: main **66/66**, conflict **4/4**, TD exit code 0 in both.
  - **Binding:** explicit exclusive `127.0.0.1:9480`. The OS table showed only `127.0.0.1` for TD's PID, and the LAN address did not connect.
  - **Threading:** 924 dispatches on `MainThread`, 0 elsewhere. A scripted operator create/edit/destroy ran via the queue.
  - **Lifecycle:** startup ≈ 0.3 s to `SERVING`; close confirmed in 32–49 ms; restart ≈ 0.6 s.
  - **Robustness:** timeouts (queued jobs cancelled and never run), port conflict reported as `PORT_IN_USE`, overload limited to `200`/`503 BUSY`, and recovery after errors.
  - **Frame time:** p50/p95 unchanged under load (16.66 / ≤ 16.83 ms). Pump cost 0.01–0.06 ms mean per frame.
  - **Setup page:** served byte-identical from Text DATs; headless Chrome ran its JS through the gate.
  - **Security surface:** the gate matrix (23 cases), authentication, kill switch and audit (no token) all behaved as specified.
  - The Web Server DAT itself was **not** benchmarked: on this build that would expose a LAN port.
- **Core code (unit-tested; exercised in TD only by spike S6; no component shell):**
  - `src/tdflowmcp/net/pyhttp.py`: the embedded transport;
  - `net/bindverify.py`: step 0 per transport, verdict with `sockname`/`others`, `on_bind_error`, `user_close`, `on_transport_failure`;
  - `net/oslisteners.py`, `gate.py` (ASCII-digit `Content-Length`);
  - `compat.py`: transport-aware `net.capability`, minimum build 2025.32440;
  - `src/setup/troubleshooting.json`.
- **111 unit tests pass** on CPython 3.11.8 **and** on TD's bundled Python 3.11.10 (1 skipped: non-Windows only). They include the review-4 regression tests. Boundary tests prove that `src/tdflowmcp` imports neither `td` nor process or installer modules.
- The doc checker passes (see [validation-index](validation-index.md)).

## Not implemented (nothing below exists)
- **Component and UI:**
  - TD adapter, component shell or controller.
  - Custom-parameter build, `.tox` builder, in-TD component test runner.
  - Production Setup Assistant page (only spike assets exist).
- **MCP and services:**
  - MCP protocol, policy engine, identity/token store, durable audit store.
  - Skills, continuity services, `tdflowProject`, dashboard.
- **Release:** no production `.tox`.
- **Third-party code:** no third-party binary has been downloaded.

## Not observed / unverified
- **CI:** no GitHub Actions run has been observed for any commit. No GitHub integration is available in this environment (no `gh`, no GitHub connector; a credential-based API read was denied earlier), and the repository is private ([B3](roadmap.md#open-blockers-and-owner-actions)).
- **Browser launch:** launching a browser from TouchDesigner itself (`Opensetup`) has not been exercised. In S6 the harness started headless Chrome.
- **S1** (optional Web Server DAT comparison) needs a build with `localaddress`; that build is not installed and is not required.
- **Cloudflare:** the codemode/mcp page could not be fetched first-hand (TLS interception).
- **TDMCP / TDMCPSkills:** facts are documentation-level only; nothing was tested hands-on.

## Commit record
- `82b64f1`, `b70000a`, `066dbd0`, `380d317`: Phase 0, as recorded previously. `main` = `066dbd0`.
- `377841c`, `ec259ed`: S5, bind-gate wording, TD-independent 1A prep, review-3 fixes. Pushed to `origin/dev`.
- S6 commit: the embedded transport, S6 spike and findings, review 4, ADR-0002 revision and compatibility policy. Recorded by the follow-up commit after the push is observed. CI: **not observed**.

## Next action
**Phase 1A item 9 on 2025.32440:** the component shell.
- Controller states and essential parameters.
- `pyhttp` under the bind gate, with the pre-activation port-ownership check and periodic re-verification.
- `/.well-known/tdflowmcp`.

Do the S2/S3 spikes first, because they are needed to build and test the `.tox`.
