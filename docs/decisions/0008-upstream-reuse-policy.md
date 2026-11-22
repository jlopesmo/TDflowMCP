# ADR-0008 — Upstream reuse and licensing policy

**Status:** Accepted, owner brief 2026-11-22 ("Avoid modifying or redistributing upstream implementations without confirming license permissions"). The per-project classifications below are research observations, **not legal advice**.

## Decision
1. Nothing is copied from external projects (code, schemas, prompts, skills or assets) unless `repoinspire.md` records an exact version, a completed license review, and an owner-approved `code_reuse` decision. Attribution and notices must then be preserved.
2. Conceptual patterns from public documentation may inform the design, with the source cited.
3. Classifications as of 2026-11-22:
   - **Derivative TDMCP:** Shared Use License + Tool Terms of Use. Non-OSI, revocable, restricted to use with TouchDesigner, with AI-provider restrictions. **Reference only: do not copy or redistribute.** Its `.tox` must not be bundled.
   - **Embody/Envoy:** MIT. Reuse is legally possible with notice but is not planned. Do **not** install Embody into this repository (open issue #194 reports deletion of `.git/`).
   - **8beeeaaat/touchdesigner-mcp, Pantani/tdmcp, r-spring/touchdesigner-mcp-server:** MIT. Reference only.
   - **johnsabath/touchdesigner-mcp, twozero-team/twozero-td-mcp:** no license detected, which means all rights reserved. **Do not copy.**
   - **@cloudflare/codemode:** MIT (per README). Pattern reference only.
4. TDflowMCP's own license is an **owner decision pending** (see the [roadmap](../icm/roadmap.md#owner-decisions-pending)). Until it is set, the repository is not licensed for reuse by others.
