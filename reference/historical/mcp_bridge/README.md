# Historical reference: original `mcp_bridge.tox`

**Status:** Historical reference only, preserved by owner decision on 2026-11-22 ([ADR-0008](../../../docs/decisions/0008-upstream-reuse-policy.md)).
- **Not source.** Nothing here is built, loaded, tested or imported by TDflowMCP.
- **Do not run** any of this code. Do not load the `.tox` into a real project.
- **Origin:** the owner's own predecessor work. Not third-party.

## Contents and provenance

| Item | Location | SHA-256 | Notes |
|---|---|---|---|
| `mcp_bridge.tox` (original) | **Not committed.** Kept locally at `.tmp/references/mcp_bridge.tox` (git-ignored) | `4386bfe5dacda95f7966d2df484dd0fdc0d01919e8f7c2c8fd70aafafc079891` | 5,366 bytes. Saved with TD 2025.32440 on 2026-04-02 (per `build_metadata.txt`). Contains a `textport_log` Table DAT with historical log rows that have not been reviewed, so committing it needs an owner decision |
| Technical audit | [mcp_bridge_audit_2026-11-22.md](mcp_bridge_audit_2026-11-22.md) | `7ede476ae4402dc88eb59d2d055944defc333a6adca22233c6c530ec99ff57f0` | Static audit supplied by the owner. Byte-identical copy |
| Extracted source (text) | [source/](source/README.md), extracted verbatim from the zip for diffing and privacy scanning. **Never run it** | — | Same content as the zip |
| Extracted source | `mcp_bridge_review_source.zip` | `0002ce3741fecf0782a4c361fd6a9f749c2e67e0267ab750232a36eef82d3a0c` | Python callbacks + operator metadata. The textport log is excluded. Byte-identical copy |

The `.tox` hash matches the one stated in the audit. The Phase 0 comparison is in [docs/research/2026-11-22-mcp-bridge-review.md](../../../docs/research/2026-11-22-mcp-bridge-review.md).
