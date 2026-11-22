# TDflowMCP par_exec (Parameter Execute DAT inside TDflowMCP.tox). Main thread only.
# Forwards the component's custom-parameter pulses and value changes to the extension.

def _ext():
	return getattr(me.parent().ext, 'TDflowMCPExt', None)

def onPulse(par):
	ext = _ext()
	if ext is not None:
		ext.OnPulse(par)
	return

def onValueChange(par, prev):
	ext = _ext()
	if ext is not None:
		ext.OnValueChange(par, prev)
	return
