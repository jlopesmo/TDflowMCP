# ADR-0020 — Offline Script drafts

**Status:** Conditionally Accepted (owner, 2026-11-27, [Issue #26](https://github.com/jlopesmo/TDflowMCP/issues/26)). Production promotion requires separate approval and the release validation gates.

Store bounded inert text and a previous revision for comparison in the existing private project SQLite store. Use the established project identity and storage worker. The `.toe`, `.tox`, MCP audit export and product source are outside the draft store. An authenticated owner can explicitly copy reviewed text and a checksum manifest for manual use. The private database contains credentials and must stay private.

Require separate owner-issued, expiring draft read, author and submit grants on exact project subtree scopes. Apply existing read policy and protected areas to declared scopes and optional target paths. The author can revise and submit their own records. A paired owner session can decide only the pending revision and exact hash, for offline review. A new revision clears its review state.

Static syntax diagnostics use bounded AST parsing. The draft path exposes no TouchDesigner graph operations, execution hook, artifact writer or executable tool delegation. [ADR-0018](0018-script-operator-lifecycle.md) remains Proposed and the Script operator creation and activation decisions stay separate.
