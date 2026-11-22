def onStart():
    mod('/mcp_bridge/stdout_redirect').enable()


def onExit():
    mod('/mcp_bridge/stdout_redirect').disable()
