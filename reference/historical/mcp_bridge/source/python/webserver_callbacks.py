# ============================================================
# MCP Bridge - WebServer DAT Handler
# Paste this into the WebServer DAT's "Request" callback DAT
# TouchDesigner 2025.32440+
# ============================================================
import json


def onHTTPRequest(webServerDAT, request, response):
	method  = request['method']
	uri     = request['uri']
	body    = request['data'].decode('utf-8') if request['data'] else '{}'

	try:
		data = json.loads(body)
	except Exception:
		data = {}

	response['statusCode']    = 200
	response['statusReason']  = 'OK'
	response['data']          = dispatch(method, uri, data)
	return response


def dispatch(method, uri, data):
	routes = {
		'/network/state':       handle_network_state,
		'/params/get':          handle_params_get,
		'/params/set':          handle_params_set,
		'/dats/read':           handle_dats_read,
		'/dats/write':          handle_dats_write,
		'/dats/create':         handle_dats_create,
		'/dats/delete':         handle_dats_delete,
		'/custompars/get':      handle_custompars_get,
		'/custompars/create':   handle_custompars_create,
		'/custompars/delete':   handle_custompars_delete,
		'/textport/read':       handle_textport_read,
		'/textport/clear':      handle_textport_clear,
		'/python/run':          handle_python_run,
	}

	handler = routes.get(uri)
	if handler is None:
		return encode({'error': f'Unknown endpoint: {uri}'})
	try:
		return encode(handler(data))
	except Exception as e:
		return encode({'error': str(e)})


def encode(obj):
	return json.dumps(obj).encode('utf-8')


# ============================================================
# NETWORK
# ============================================================

def _op_to_dict(o, recursive=False):
	"""Serialize a single operator to a dict, optionally with nested children."""
	entry = {
		'path':    o.path,
		'name':    o.name,
		'type':    o.OPType,
		'family':  o.family,
		'errors':  o.errors(),
		'bypass':  o.bypass,
		'x':       o.nodeX,
		'y':       o.nodeY,
	}
	if recursive and hasattr(o, 'children'):
		entry['children'] = [_op_to_dict(c, recursive=True) for c in o.children]
	return entry


def handle_network_state(data):
	"""Return a snapshot of operators under a given path.

	data = {
	  path:      '/show',   # root to inspect (default '/')
	  recursive: true       # if true, returns full nested tree (default false)
	}
	"""
	path      = data.get('path', '/')
	recursive = bool(data.get('recursive', False))
	parent    = op(path)
	if parent is None:
		return {'error': f'Path not found: {path}'}

	ops = [_op_to_dict(o, recursive=recursive) for o in parent.children]

	return {
		'path':      path,
		'recursive': recursive,
		'operators': ops,
		'count':     len(ops),
	}


# ============================================================
# PARAMETERS
# ============================================================

def handle_params_get(data):
	"""Get all or specific parameters of an operator."""
	op_path   = data.get('op')
	par_names = data.get('pars', [])  # empty = return all

	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}

	result = {}
	targets = [getattr(o.par, n) for n in par_names if hasattr(o.par, n)] if par_names else o.pars()
	for p in targets:
		try:
			result[p.name] = {
				'val':     p.val,
				'expr':    p.expr,
				'mode':    str(p.mode),
				'label':   p.label,
				'style':   p.style,
			}
		except Exception:
			result[p.name] = {'val': None}

	return {'op': op_path, 'pars': result}


def handle_params_set(data):
	"""Set one or more parameters on an operator.
	   data = { op: '/path', pars: { name: value, ... } }
	"""
	op_path = data.get('op')
	pars    = data.get('pars', {})

	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}

	changed = []
	errors  = []
	for name, value in pars.items():
		try:
			setattr(o.par, name, value)
			changed.append(name)
		except Exception as e:
			errors.append({'par': name, 'error': str(e)})

	return {'op': op_path, 'changed': changed, 'errors': errors}


# ============================================================
# DAT SCRIPTS
# ============================================================

def handle_dats_read(data):
	"""Read the text content of a DAT operator."""
	op_path = data.get('op')
	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}
	if o.family != 'DAT':
		return {'error': f'{op_path} is not a DAT (family={o.family})'}

	# For Table DATs return structured rows; for others return raw text
	if o.OPType == 'tableDAT':
		rows = []
		for r in range(o.numRows):
			rows.append([o[r, c].val for c in range(o.numCols)])
		return {'op': op_path, 'type': 'table', 'rows': rows,
		        'numRows': o.numRows, 'numCols': o.numCols}
	else:
		return {'op': op_path, 'type': 'text', 'text': o.text}


def handle_dats_write(data):
	"""Overwrite the text content of a DAT operator."""
	op_path = data.get('op')
	text    = data.get('text', '')

	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}
	if o.family != 'DAT':
		return {'error': f'{op_path} is not a DAT'}

	o.text = text
	return {'op': op_path, 'ok': True, 'chars': len(text)}


def handle_dats_create(data):
	"""Create a new DAT operator.
	   data = { parent: '/project1', type: 'text', name: 'myDAT', text: '...', x: 0, y: 0 }
	   Supported types: text, table, script, parameterexec, chopexec, datexec, execute
	"""
	parent_path = data.get('parent', '/project1')
	dat_type    = data.get('type', 'text')   # textDAT suffix added automatically
	name        = data.get('name')
	text        = data.get('text', '')
	x           = data.get('x', 0)
	y           = data.get('y', 0)

	parent_op = op(parent_path)
	if parent_op is None:
		return {'error': f'Parent not found: {parent_path}'}

	# Map friendly names → TD operator type strings
	type_map = {
		'text':          'textDAT',
		'table':         'tableDAT',
		'script':        'scriptDAT',
		'parameterexec': 'parameterexecDAT',
		'chopexec':      'chopexecDAT',
		'datexec':       'datexecDAT',
		'execute':       'executeDAT',
		'fifo':          'fifoDAT',
	}
	td_type = type_map.get(dat_type, dat_type)

	new_op = parent_op.create(td_type, name)
	if new_op is None:
		return {'error': f'Failed to create {td_type}'}

	new_op.nodeX = x
	new_op.nodeY = y
	if text:
		new_op.text = text

	return {'op': new_op.path, 'type': td_type, 'ok': True}


def handle_dats_delete(data):
	"""Delete a DAT operator."""
	op_path = data.get('op')
	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}
	if o.family != 'DAT':
		return {'error': f'{op_path} is not a DAT'}

	path_copy = o.path
	o.destroy()
	return {'op': path_copy, 'deleted': True}


# ============================================================
# CUSTOM PARAMETERS
# ============================================================

def handle_custompars_get(data):
	"""Return all custom parameters of an operator."""
	op_path = data.get('op')
	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}

	result = {}
	for p in o.customPars:
		try:
			result[p.name] = {
				'val':    p.val,
				'label':  p.label,
				'style':  p.style,
				'page':   p.page.name if p.page else None,
				'min':    p.min  if hasattr(p, 'min')  else None,
				'max':    p.max  if hasattr(p, 'max')  else None,
				'expr':   p.expr,
			}
		except Exception as e:
			result[p.name] = {'error': str(e)}

	return {'op': op_path, 'customPars': result, 'count': len(result)}


def handle_custompars_create(data):
	"""Create one or more custom parameters on an operator.

	data = {
	  op: '/project1/myComp',
	  page: 'Control',          # page name (created if missing)
	  pars: [
	    { name: 'Speed', style: 'Float', label: 'Speed', default: 1.0, min: 0.0, max: 10.0 },
	    { name: 'Mode',  style: 'Menu',  label: 'Mode',  menuNames: ['a','b'], menuLabels: ['A','B'] },
	    { name: 'Tag',   style: 'Str',   label: 'Tag',   default: '' },
	    { name: 'Go',    style: 'Pulse', label: 'Go' },
	  ]
	}

	Supported styles: Float, Int, Str, Toggle, Menu, Pulse, XY, XYZ, UV, UVW, RGBA, RGB, WH
	"""
	op_path  = data.get('op')
	page_name= data.get('page', 'Custom')
	pars     = data.get('pars', [])

	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}

	page = o.appendCustomPage(page_name)

	created = []
	errors  = []

	for par_def in pars:
		name    = par_def.get('name')
		style   = par_def.get('style', 'Float')
		label   = par_def.get('label', name)
		default = par_def.get('default')

		try:
			append_fn_map = {
				'Float':  page.appendFloat,
				'Int':    page.appendInt,
				'Str':    page.appendStr,
				'Toggle': page.appendToggle,
				'Menu':   page.appendMenu,
				'Pulse':  page.appendPulse,
				'XY':     page.appendXY,
				'XYZ':    page.appendXYZ,
				'UV':     page.appendUV,
				'UVW':    page.appendUVW,
				'RGBA':   page.appendRGBA,
				'RGB':    page.appendRGB,
				'WH':     page.appendWH,
			}

			fn = append_fn_map.get(style)
			if fn is None:
				errors.append({'name': name, 'error': f'Unknown style: {style}'})
				continue

			if style == 'Menu':
				new_par = fn(name, label=label)[0]
				menu_names  = par_def.get('menuNames', [])
				menu_labels = par_def.get('menuLabels', menu_names)
				new_par.menuNames  = menu_names
				new_par.menuLabels = menu_labels
				if default is not None:
					new_par.default = default
			else:
				new_par = fn(name, label=label)[0]
				if default is not None:
					new_par.default = default
				if 'min' in par_def:
					new_par.min = par_def['min']
				if 'max' in par_def:
					new_par.max = par_def['max']

			created.append(name)

		except Exception as e:
			errors.append({'name': name, 'error': str(e)})

	return {'op': op_path, 'page': page_name, 'created': created, 'errors': errors}


def handle_custompars_delete(data):
	"""Delete specific custom parameters or an entire custom page.

	data = { op: '/path', pars: ['Speed', 'Mode'] }          # delete specific pars
	     OR { op: '/path', page: 'Control' }                  # delete entire page
	"""
	op_path   = data.get('op')
	par_names = data.get('pars', [])
	page_name = data.get('page')

	o = op(op_path)
	if o is None:
		return {'error': f'Operator not found: {op_path}'}

	deleted = []
	errors  = []

	if page_name:
		# Delete entire page
		for page in o.customPages:
			if page.name == page_name:
				page.destroy()
				deleted.append(f'page:{page_name}')
				break
		else:
			errors.append({'page': page_name, 'error': 'Page not found'})
	else:
		for name in par_names:
			try:
				p = getattr(o.par, name, None)
				if p is None:
					errors.append({'par': name, 'error': 'Not found'})
					continue
				p.destroy()
				deleted.append(name)
			except Exception as e:
				errors.append({'par': name, 'error': str(e)})

	return {'op': op_path, 'deleted': deleted, 'errors': errors}


# ============================================================
# TEXTPORT
# ============================================================

def handle_python_run(data):
	"""Execute a Python string in TD's global namespace.
	   data = { code: 'print(42)' }
	   Returns stdout is captured only if redirect is active; otherwise returns ok.
	"""
	code = data.get('code', '')
	if not code:
		return {'error': 'No code provided'}
	try:
		exec(compile(code, '<mcp>', 'exec'), {'op': op, 'ops': ops, 'run': run})
		return {'ok': True}
	except Exception as e:
		return {'error': str(e)}


def handle_textport_read(data):
	"""Return captured stdout lines from the textport_log table.
	   data = { lines: 50 }  # optional max lines from end (default all)
	"""
	table = op('/mcp_bridge/textport_log')
	if table is None:
		return {'error': 'textport_log table not found'}

	limit = data.get('lines')
	rows  = []
	for r in range(1, table.numRows):
		rows.append({'timestamp': table[r, 'timestamp'].val,
		             'line':      table[r, 'line'].val})

	if limit:
		rows = rows[-int(limit):]

	return {'lines': rows, 'count': len(rows)}


def handle_textport_clear(data):
	"""Clear all captured lines from textport_log, keeping the header row."""
	table = op('/mcp_bridge/textport_log')
	if table is None:
		return {'error': 'textport_log table not found'}

	while table.numRows > 1:
		table.deleteRow(1)

	return {'ok': True}
