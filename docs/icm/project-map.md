# TDflowMCP — Project map

This map lists the repository's actual contents (Phase 1A). Planned directories are listed in [source-of-truth.md](../architecture/source-of-truth.md#directory-layout). **They do not exist yet.**

| Path | Purpose | Governing doc |
|---|---|---|
| `README.md` | Project overview and status | this ICM |
| `AGENTS.md` | Authoritative agent instructions | — |
| `CLAUDE.md` | Pointer to AGENTS.md for Claude Code | AGENTS.md |
| `repoinspire.md` | External and historical reference register | [ADR-0008](../decisions/0008-upstream-reuse-policy.md) |
| `.gitignore`, `.gitattributes` | Keep runtime state, secrets and builds out of Git; binary handling for `.tox`/`.toe` | [source-of-truth](../architecture/source-of-truth.md) |
| `.github/workflows/docs.yml` | CI: doc checker + unit tests on Python 3.11 (Ubuntu, Windows); SHA-pinned actions | [verification](../operations/verification-and-release.md) |
| `docs/icm/` | TDflowMCP's own ICM layer (this folder) | [README](README.md) |
| `docs/product/vision.md` | Vision, pillars P1–P17, success criteria | [ADR-0001](../decisions/0001-product-scope.md) |
| `docs/research/` | Dated research and independent-review records | — |
| `docs/architecture/` | Design documents: system, security, setup, integration, continuity/memory, tools, stack, source rules | [decisions](../decisions/README.md) |
| `docs/decisions/` | ADRs 0001–0013 + index | — |
| `docs/milestones/` | Phase 0 and Phase 1 (1A/1B/1C) scope and acceptance | [roadmap](roadmap.md) |
| `docs/operations/` | Git workflow, verification and release procedures | — |
| `tools/check_docs.py` | Stdlib doc validator (required files, links/anchors, ADR status vs index, privacy patterns) | [validation-index](validation-index.md) |
| `tests/test_check_docs.py`, `tests/__init__.py` | Unit tests for the doc validator | [validation-index](validation-index.md) |
| `src/tdflowmcp/` | Pure-Python core (no `td`): `controller.py` (component state machine), `net/pyhttp.py` (embedded loopback transport), `net/evidence.py`, `net/bindverify.py`, `net/oslisteners.py`, `gate.py`, `compat.py`, `tellagent.py`, `setupapi.py`. Embedded into the `.tox` | [security model §8](../architecture/security-model.md#8-network-posture), [setup assistant](../architecture/setup-assistant.md) |
| `src/td_adapter/` | The only code that touches `td`: `extension.py` (TDflowMCPExt), `loader.py` (embedded-module finder with hash check), `callbacks_execute.py`, `callbacks_parexec.py` | [source-of-truth](../architecture/source-of-truth.md) |
| `src/component/manifest.json` | Component layout + custom parameters; the builder's only input list | [ADR-0007](../decisions/0007-source-first-build.md) |
| `src/setup/web/` | Setup Assistant page (`index.html`, `app.css`, `app.js`), embedded into the `.tox` | [setup assistant §7](../architecture/setup-assistant.md#7-setup-assistant-web) |
| `tools/build_tox/` | Source-first builder (`build.py` outside TD, `builder_runtime.py` inside) → `build/TDflowMCP.tox` + `TDflowMCP.json` | [build and test](../operations/build-and-test.md) |
| `tools/tdtoe.py`, `tools/tdrun.py` | Disposable `.toe` generator; TouchDesigner launcher that protects other TD processes | [build and test](../operations/build-and-test.md) |
| `tests/td/` | In-TouchDesigner integration tests: `td_runner.py` (inside TD) + `run_td_tests.py` (external harness) → `build/td-test-results*.json` | [validation-index](validation-index.md) |
| `build/` | **Git-ignored** build output: `TDflowMCP.tox` (development build), `TDflowMCP.json`, `reproducibility.json`, test results | [build and test](../operations/build-and-test.md) | [security model §8](../architecture/security-model.md#8-network-posture), [setup assistant §3](../architecture/setup-assistant.md#3-compatibility-checks) |
| `src/setup/troubleshooting.json` | Remediation entries for every warn/fail check; internal actions only | [ADR-0010](../decisions/0010-setup-assistant-and-component-parameters.md) |
| `tests/unit/` | Unit tests for the core, including the boundary tests | [validation-index](validation-index.md) |
| `spikes/s4/` | Disposable S4 spike (undo semantics) | [S4 record](../research/2026-11-22-s4-undo-semantics.md) |
| `spikes/s5/` | Disposable, reproducible S5 spike code. Never built or imported | [S5 findings](../research/2026-11-22-s5-compatibility-2025.32440.md) |
| `spikes/s6/` | Disposable S6 spike: toe builder, Setup page assets, in-TD driver, external harness. Its `s6/*` control methods are spike-only and unsafe in production | [S6 findings](../research/2026-11-22-s6-embedded-python-transport.md) |
| `reference/historical/mcp_bridge/` | Read-only historical reference: owner's original bridge audit + extracted source zip + provenance README. Never built, loaded or tested | [ADR-0008](../decisions/0008-upstream-reuse-policy.md) |
| `reference/third_party/` | **Git-ignored**; future home of official third-party releases downloaded for hands-on testing | [ADR-0008](../decisions/0008-upstream-reuse-policy.md) |

No `.tox` or `.toe` is committed (builds are git-ignored). No MCP server, policy engine, audit store or dashboard exists.
