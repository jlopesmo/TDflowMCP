# Repository structure and source-of-truth rules

**Status:** Rules 1–4 and 7 are **Proposed** in [ADR-0007](../decisions/0007-source-first-build.md). Rules 5–6 (documentation authority) apply now as repository policy for agents ([AGENTS.md](../../AGENTS.md)).

## Rules

1. **Text source in `src/` is the only source of truth for TDflowMCP's code.** Python extensions, callback DATs, dashboard HTML/JS/CSS, tool schemas and the component's network description are all text files.
2. **`TDflowMCP.tox` is a build artifact.** It is produced by `tools/build_tox` (Phase 1) from `src/`, written to `build/` (git-ignored), and published only as a release asset with a sha256 manifest. Never edit a built `.tox` by hand and commit it.
3. **The component loads its Python from DATs whose content is embedded at build time** (release) or **synced from files** (`file` parameter, dev mode). Dev mode lets a developer edit `src/` in an IDE and have TD reload. Release mode has no external file dependency.
4. **The network layout of the component** (which DATs/COMPs exist, their parameters) is declared in a text manifest (`src/component/manifest.json`, proposed). The builder constructs the COMP from it, so layout changes are diffable.
5. **Documentation authority** follows the [ICM index](../icm/README.md#sources-of-truth). Architecture text never implies implementation.
6. **Runtime state never enters Git:** tokens, policies with secrets, audit DBs and logs (enforced by `.gitignore` and the doc checker's privacy patterns today; CI secret scanning is planned for Phase 1).
7. **Host-project source-first support** (externalizing the user's own networks) is a *separate later capability*. It may interoperate with Embody's TDXN format after evaluation ([repoinspire](../../repoinspire.md)). It is not a dependency.

## Directory layout

The current tree is in [project-map.md](../icm/project-map.md). Planned additions (not yet created):

```
src/
  tdflowmcp/            # pure-Python core: protocol, policy, registry, audit (no `td` imports)
    protocol/  policy/  registry/  audit/  identity/  approvals/
  td_adapter/           # thin layer that touches `td` (op access, undo, Web Server DAT callbacks)
  tools/                # core tool definitions (Phase 1)
  extensions/           # optional tool modules (Phase 4+)
  dashboard/            # static UI (index.html, app.js, styles.css) — no build step
  component/            # manifest.json describing the COMP layout
tests/
  unit/                 # stdlib unittest, runs in plain CPython 3.11 (CI)
  td/                   # in-TouchDesigner integration tests + runner
tools/
  check_docs.py         # Phase 0 (exists)
  build_tox/            # Phase 1
```

**Key boundary:** `src/tdflowmcp/` must not import `td`. All TD access goes through `src/td_adapter/`. This keeps ≥ 80 % of logic testable in CI without TouchDesigner (hypothesis to validate).
