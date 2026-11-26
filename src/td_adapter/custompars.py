"""Bounded, constant custom parameter definitions and explicit rollback inside TouchDesigner."""
import math

from tdflowmcp.engine import ToolFailure
from tdflowmcp.policy import PARAM_DENYLIST, _name_denied


STYLES = {"float": "Float", "int": "Int", "toggle": "Toggle", "menu": "Menu", "string": "Str", "pulse": "Pulse"}
KINDS = {style: kind for kind, style in STYLES.items()}
DEFAULTS = {"float": 0.0, "int": 0, "toggle": False, "string": ""}


def _invalid(message):
    raise ToolFailure("INVALID_ARGS", message)


def _page(op, name):
    return next((page for page in op.customPages if page.name == name), None)


def _par(op, name):
    return next((par for par in op.pars() if par.name == name), None)


def _value(kind, value):
    if kind == "float" and type(value) in (float, int) and abs(value) <= 1e9:
        return float(value)
    if kind == "int" and type(value) is int and abs(value) <= 1e9:
        return value
    if kind == "toggle" and type(value) is bool:
        return value
    if kind in ("menu", "string") and isinstance(value, str) and len(value) <= 512:
        return value
    _invalid(f"Invalid constant value for {kind}.")


def _row(par, page):
    if (not par.isCustom or par.page.name != page or par.style not in KINDS or len(par.parGroup) != 1
            or par.readOnly or par.hidden or str(par.mode).split(".")[-1] != "CONSTANT" or par.enableExpr
            or (par.style == "Menu" and par.menuSource)):
        _invalid(f"{par.name} must be an editable, constant scalar custom parameter on {page}.")
    kind = KINDS[par.style]
    row = {"type": kind, "label": str(par.label)}
    if kind != "pulse":
        row.update(default=_value(kind, par.default), value=_value(kind, par.val))
    if kind in ("float", "int"):
        row["range"] = [float(par.normMin), float(par.normMax)]
    if kind == "menu":
        row.update(menu=list(par.menuNames), menu_labels=list(par.menuLabels))
    return row


def snapshot(op, page_name, names):
    page = _page(op, page_name)
    return {"order": [par.name for par in page.pars] if page is not None else None,
            "parameters": {name: _row(par, page_name) if (par := _par(op, name)) is not None else None for name in names}}


def plan(op, args):
    page_name, definitions = args["page"], args.get("definitions", [])
    if page_name != page_name.strip():
        _invalid("Use a custom page name without leading or trailing spaces.")
    names = [entry["name"] for entry in definitions]
    if not names and "order" not in args:
        _invalid("Provide definitions or a complete parameter order.")
    if len(names) != len(set(names)) or any(_name_denied(name.lower(), PARAM_DENYLIST) for name in names):
        _invalid("Parameter names must be unique and must avoid reserved names.")
    existing_page = _page(op, page_name)
    if existing_page is None and any(p.name.casefold() == page_name.casefold() for p in op.pages):
        _invalid("Choose a distinct custom page name.")
    before = snapshot(op, page_name, names)
    before_order = before["order"] or []
    if len(before_order) + len(names) > 80:
        _invalid("The custom page is too large for a bounded edit.")
    rows = {entry["name"]: _definition(entry, before["parameters"][entry["name"]]) for entry in definitions}
    final_order = before_order + [name for name in names if before["parameters"][name] is None]
    desired = args.get("order", final_order)
    if sorted(desired) != sorted(final_order) or not desired:
        _invalid("Order must list every parameter on the resulting page exactly once.")
    if "order" in args and existing_page is not None and any(len(p.parGroup) != 1 for p in existing_page.pars):
        _invalid("Explicit ordering is supported only on pages of scalar parameters.")
    return before, {"order": desired, "parameters": rows}


def _definition(entry, old):
    kind = entry["type"]
    name = entry["name"]
    if old is not None and old["type"] != kind:
        _invalid(f"The type of {name} cannot change.")
    if ("range" in entry and kind not in ("float", "int")) or ("choices" in entry and kind != "menu"):
        _invalid(f"Range or choices are incompatible with {kind}.")
    row = dict(old or {"type": kind, "label": name})
    row["label"] = entry.get("label", row["label"])
    if kind == "pulse":
        if "default" in entry:
            _invalid("A pulse button has no default value.")
        return row
    if kind == "menu":
        if "choices" in entry:
            row["menu"] = [item["name"] for item in entry["choices"]]
            row["menu_labels"] = [item.get("label", item["name"]) for item in entry["choices"]]
        choices = row.get("menu", [])
        if not choices or len(choices) != len(set(choices)):
            _invalid("A menu needs unique static choices.")
        fallback = choices[0]
    else:
        fallback = DEFAULTS[kind]
    row["default"] = _value(kind, entry.get("default", row.get("default", fallback)))
    row.setdefault("value", row["default"])
    if kind == "menu" and (row["default"] not in choices or row["value"] not in choices):
        _invalid("The menu must retain its existing selection and the requested default.")
    if kind in ("float", "int"):
        lower, upper = entry.get("range", row.get("range", [0, 1]))
        if not math.isfinite(lower) or not math.isfinite(upper) or not lower < upper:
            _invalid("The slider range must have finite, increasing endpoints.")
        row["range"] = [float(lower), float(upper)]
    return row


def apply(op, page_name, state):
    """Restore or apply only the named scalar definitions and the participating page order."""
    page = _page(op, page_name)
    for name, row in state["parameters"].items():
        par = _par(op, name)
        if row is None:
            if par is not None:
                if not par.isCustom or par.page.name != page_name or len(par.parGroup) != 1:
                    _invalid(f"Cannot restore {name} on this page.")
                par.destroy()
            continue
        if page is None:
            page = op.appendCustomPage(page_name)
        if par is None:
            par = getattr(page, "append" + STYLES[row["type"]])(name, label=row["label"], replace=False)[0]
        if par.label != row["label"]:
            par.label = row["label"]
        if row["type"] == "menu":
            if list(par.menuNames) != row["menu"]:
                par.menuNames = row["menu"]
            if list(par.menuLabels) != row["menu_labels"]:
                par.menuLabels = row["menu_labels"]
        if "range" in row and [float(par.normMin), float(par.normMax)] != row["range"]:
            par.normMin, par.normMax = row["range"]
        if "default" in row and par.default != row["default"]:
            par.default = row["default"]
        if "value" in row and par.val != row["value"]:
            par.val = row["value"]
    if page is None:
        return
    order = state["order"]
    if order is None:
        if page.pars:
            _invalid("Cannot remove a page that still contains parameters.")
        page.destroy()
    elif [par.name for par in page.pars] != order:
        page.sort(*order)
