# TDflowMCP frame_exec (Execute DAT inside TDflowMCP.tox). Main thread only.
# Drives the controller once per frame and stops the listener when the project exits.

def _ext():
	return getattr(me.parent().ext, 'TDflowMCPExt', None)

def onFrameStart(frame):
	ext = _ext()
	if ext is not None:
		ext.OnFrame()
	return

def onExit():
	ext = _ext()
	if ext is not None:
		ext.Shutdown('exit')
	return
