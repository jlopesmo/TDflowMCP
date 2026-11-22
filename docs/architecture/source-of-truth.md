# Repository structure and source-of-truth rules

**Status:** Rules 1–4 follow [ADR-0007](../decisions/0007-source-first-build.md) (Conditionally Accepted). Rules 5–7 apply now as repository policy for agents ([AGENTS.md](../../AGENTS.md)). Rules 8–9 follow Proposed [ADR-0013](../decisions/0013-project-memory-model.md).

## Rules

1. **Text source in `src/` is the only source of truth for TDflowMCP's code.** Python extensions, callback DATs, dashboard HTML/JS/CSS, tool schemas and the component's network description are all text files.
2. **`TDflowMCP.tox` (and later `tdflowProject.tox`) is a build artifact.** It is produced by `tools/build_tox` (Phase 1) from `src/`, written to `build/` (git-ignored), and published only as a release asset with a sha256 manifest. Never edit a built `.tox` by hand and commit it.
3. **The component loads its Python from DATs whose content is embedded at build time** (release) or **synced from files** (`file` parameter, dev mode). Dev mode lets a developer edit `src/` in an IDE and have TD reload. Release mode has no external file dependency.
4. **The network layout of the component** (which DATs/COMPs exist, their parameters) is declared in a text manifest (`src/component/manifest.json`, proposed). The builder constructs the COMP from it, so layout changes are diffable.
5. **Documentation authority** follows the [ICM index](../icm/README.md#sources-of-truth). Architecture text never implies implementation.
6. **Runtime state never enters Git:** tokens, policies with secrets, audit DBs and logs (enforced by `.gitignore` and the doc checker's privacy patterns today; CI secret scanning is planned for Phase 1A, item 6).
7. **Historical references** (e.g. the original `mcp_bridge.tox`) are committed only under `reference/historical/`, read-only, with a provenance README. They are never built, loaded or tested ([ADR-0008](../decisions/0008-upstream-reuse-policy.md)). This is the only exception to rule 2.
8. **Host-project memory** follows the single-authority model in [continuity-and-memory.md](continuity-and-memory.md#2-authority-model-avoids-conflicting-authoritative-records). This repository's `docs/icm/` is TDflowMCP's own development memory, never a host project's.
9. **Host-project source-first support** (externalizing the user's own networks) is a *separate later capability*. It may interoperate with Embody's TDXN format after evaluation ([repoinspire](../../repoinspire.md)). It is not a dependency.

## Directory layout

The current tree is in [project-map.md](../icm/project-map.md). Planned additions (not yet created):

```
src/
  tdflowmcp/            # pure-Python core: protocol, policy, registry, audit (no `td` imports)
    protocol/  policy/  registry/  audit/  identity/  approvals/
    gate.py  compat.py  net/   # EXIST (Phase 1A prep): request gate, compatibility checks, bind verification
    continuity/  memory/  integrations/   # planned; pure logic, TD/file access injected
  td_adapter/           # thin layer that touches `td` (op access, undo, Web Server DAT callbacks)
  tools/                # core tool definitions (Phase 1)
  extensions/           # optional tool modules (Phase 4+)
  setup/                # troubleshooting.json EXISTS; Setup Assistant static page planned (Phase 1A)
  dashboard/            # governance dashboard static UI (Phase 2B) — no build step
  integrations/         # clients.json registry (Phase 1C)
  skills/               # agent Skills bundle sources (Phase 1C)
  project/              # tdflowProject COMP manifest (Phase 2A)
  component/            # manifest.json describing the COMP layout and custom parameters
reference/historical/   # read-only historical artifacts (exists: mcp_bridge/)
reference/third_party/  # git-ignored; official third-party releases for hands-on tests only
spikes/                 # EXISTS: disposable, reproducible spike code (never built or imported)
tests/
  unit/                 # EXISTS: stdlib unittest, runs in plain CPython 3.11 (CI) and TD's bundled Python
  td/                   # in-TouchDesigner integration tests + runner
tools/
  check_docs.py         # Phase 0 (exists)
  build_tox/            # Phase 1
```

**Key boundary:** `src/tdflowmcp/` must not import `td`. All TD access goes through `src/td_adapter/`. This keeps ≥ 80 % of logic testable in CI without TouchDesigner (hypothesis to validate).
