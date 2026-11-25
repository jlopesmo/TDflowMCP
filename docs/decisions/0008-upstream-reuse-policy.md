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
4. **Licensing of TDflowMCP itself:** The 2026-11-22 decision kept the repository private and deferred selecting a public license. **Superseding owner direction (2026-11-25):** the repository may be **public** so others can download and try the officially released `.tox` for evaluation. No open-source license has been selected and broader rights to modify, redistribute, commercialize or reuse the source are **not granted** by repository visibility. The final release notes and README must describe the evaluation scope accurately; formal public licensing is a separate owner decision.
5. **Original `mcp_bridge.tox` (owner decision, 2026-11-22):** preserved as a **historical reference**. It is never source and never a runtime dependency.
   - The audit and extracted source are committed read-only under `reference/historical/mcp_bridge/`, with a provenance README (hashes, TD build, date).
   - The original `.tox` stays git-ignored (`.tmp/references/`), recorded by SHA-256. It embeds unreviewed historical log rows, so committing it requires an owner decision.
   - Historical material is excluded from builds and tests, and is never loaded into a real project.
6. **Third-party reference implementations (owner instruction, 2026-11-22):**
   - Research uses official GitHub repositories as primary sources, e.g. TouchDesigner/TDMCP and TDMCPSkills.
   - Binaries are downloaded **only when hands-on testing requires them**. The download is the official release for the relevant version, placed in git-ignored `reference/third_party/<project>-<version>/`. Version, source URL, SHA-256 and the applicable license terms are recorded in `docs/research/` before use.
   - Testing happens only in a disposable TouchDesigner project.
   - Third-party code stays physically separate from `src/` and is never imported, copied or adapted into TDflowMCP. Comparisons cover functionality and architecture only.
