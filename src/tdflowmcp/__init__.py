"""TDflowMCP core (pure Python, no `td` imports — see invariant P-7).

Phases 1A/1B: the embedded loopback transport, bind verification, request gate, compatibility checks, the
Setup Assistant controller, the policy engine, identity, the durable audit store, the MCP protocol layer
and the tool registry. Nothing here touches TouchDesigner; the component's TD adapter
(``src/td_adapter/``) drives it from the main thread.
"""

__version__ = "1.0.0"
PHASE = "1.0"
SKILL_VERSION = "1.6"           # bundled Skills (src/skills), major = tool surface
TOOL_SURFACE_VERSION = "1"   # 16 Phase 1B tools; additive since: 4 memory (2A), approval status (2B), script (3), network (4), capabilities (D25)
