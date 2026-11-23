"""TDflowMCP core (pure Python, no `td` imports — see invariant P-7).

Phases 1A/1B: the embedded loopback transport, bind verification, request gate, compatibility checks, the
Setup Assistant controller, the policy engine, identity, the durable audit store, the MCP protocol layer
and the tool registry. Nothing here touches TouchDesigner; the component's TD adapter
(``src/td_adapter/``) drives it from the main thread.
"""

__version__ = "0.2.0.dev1"
PHASE = "1B"
SKILL_VERSION = "1.0"           # bundled Skills (src/skills), major = tool surface
TOOL_SURFACE_VERSION = "1"   # 16 Phase 1B tools (tool-surface.md)
