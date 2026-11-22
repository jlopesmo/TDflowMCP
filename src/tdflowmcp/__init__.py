"""TDflowMCP core (pure Python, no `td` imports — see invariant P-7).

Phase 1A: the embedded loopback transport, bind verification, request gate, compatibility checks and
the Setup Assistant controller. Nothing here touches TouchDesigner; the component's TD adapter
(``src/td_adapter/``) drives it from the main thread.
"""

__version__ = "0.1.0.dev1"
PHASE = "1A"
TOOL_SURFACE_VERSION = "0"   # no MCP tools exist in Phase 1A
