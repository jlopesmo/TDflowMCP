# TDflowMCP — Project map

This map lists the repository's actual contents (Phase 0). Planned directories are listed in [source-of-truth.md](../architecture/source-of-truth.md#directory-layout). **They do not exist yet.**

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
| `src/tdflowmcp/` | Pure-Python core (no `td`): `net/bindverify.py`, `net/oslisteners.py`, `gate.py`, `compat.py`. Phase 1A preparation, not integrated | [security model §8](../architecture/security-model.md#8-network-posture), [setup assistant §3](../architecture/setup-assistant.md#3-compatibility-checks) |
| `src/setup/troubleshooting.json` | Remediation entries for every warn/fail check; internal actions only | [ADR-0010](../decisions/0010-setup-assistant-and-component-parameters.md) |
| `tests/unit/` | Unit tests for the core, including the boundary tests | [validation-index](validation-index.md) |
| `spikes/s5/` | Disposable, reproducible S5 spike code. Never built or imported | [S5 findings](../research/2026-11-22-s5-compatibility-2025.32440.md) |
| `reference/historical/mcp_bridge/` | Read-only historical reference: owner's original bridge audit + extracted source zip + provenance README. Never built, loaded or tested | [ADR-0008](../decisions/0008-upstream-reuse-policy.md) |
| `reference/third_party/` | **Git-ignored**; future home of official third-party releases downloaded for hands-on testing | [ADR-0008](../decisions/0008-upstream-reuse-policy.md) |

No TD adapter, component, `.tox`, `.toe`, MCP server, Setup Assistant page or dashboard exists in this repository.
