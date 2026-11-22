# TDflowMCP — Project map

Actual repository contents (Phase 0). Planned directories are listed in [source-of-truth.md](../architecture/source-of-truth.md#directory-layout) and **do not exist yet**.

| Path | Purpose | Governing doc |
|---|---|---|
| `README.md` | Project overview and status | this ICM |
| `AGENTS.md` | Authoritative agent instructions | — |
| `CLAUDE.md` | Pointer to AGENTS.md for Claude Code | AGENTS.md |
| `repoinspire.md` | External reference register (status, license review, decisions) | [ADR-0008](../decisions/0008-upstream-reuse-policy.md) |
| `.gitignore`, `.gitattributes` | Keep runtime state, secrets, builds out of Git; binary handling for `.tox`/`.toe` | [source-of-truth](../architecture/source-of-truth.md) |
| `.github/workflows/docs.yml` | CI: doc checker + unit tests on Python 3.11 | [verification](../operations/verification-and-release.md) |
| `docs/icm/` | ICM layer (this folder) | [README](README.md) |
| `docs/product/vision.md` | Product vision, pillars, success criteria | [ADR-0001](../decisions/0001-product-scope.md) |
| `docs/research/` | Dated research and review records | — |
| `docs/architecture/` | Proposed architecture (system design, security, tools, stack, source rules) | [decisions](../decisions/README.md) |
| `docs/decisions/` | ADRs | — |
| `docs/milestones/` | Phase scope and acceptance criteria | [roadmap](roadmap.md) |
| `docs/operations/` | Git workflow, verification and release procedures | — |
| `tools/check_docs.py` | Stdlib doc validator (links, anchors, ICM files, ADR status, privacy patterns) | [validation-index](validation-index.md) |
| `tests/test_check_docs.py` | Unit tests for the doc validator | [validation-index](validation-index.md) |

No `src/`, `.tox`, `.toe`, server code or dashboard code exists in this repository.
