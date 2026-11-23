"""TD operations for the Phase 1B tools (TD adapter; main thread only).

Implements ``tdflowmcp.engine.Adapter``:
- ``resolve``: arguments → concrete operators → policy ``Target``s with the classification of
  security-model §3.3 (expressions, Python-style parameters, executable op types and code-bearing
  DAT text need ``execute``). Paths must be absolute and canonical (``op(path).path == path``).
- ``execute``: re-checks preconditions, applies the change inside a named ``ui.undo`` block, rolls
  back on partial failure, and records what undo will need (undo-stack names, previous values).
- ``undo``: reverses only if TouchDesigner's undo stack is exactly as it was right after the change
  (S4b: interleaved edits, reloads and other agents change it → ``UNDO_CONFLICT``), then verifies the
  restoration by path and state (S4b: restored operators get new ids). DAT text is restored from a
  TDflowMCP snapshot (S4: TD's undo does not cover DAT text) after checking nothing changed since.
"""
import hashlib
import json
import os

import td

from tdflowmcp import layout as LAYOUT
from tdflowmcp import memory as MEM
from tdflowmcp.engine import Adapter, Resolution, ToolFailure
from tdflowmcp.policy import ACTIVATION_PARAMS, APPROVABLE_CREATE_TYPES, EXECUTABLE_TYPES, Target

DATA_TAG = "tdflowdata"
# Parameter styles whose value names another operator or a file: setting them can read or wire things
# outside the agent's scope (review 6, H2), so they are classified as execute (denied in Phase 1).
REFERENCE_STYLES = {"OP", "COMP", "OBJ", "Object", "PanelCOMP", "TOP", "CHOP", "SOP", "DAT", "MAT", "POP", "File",
                    "Folder", "FileSave", "Python"}
MAX_TEXT = 200000
MAX_WALK = 1000                    # operators visited per memory fingerprint (main-thread bound; review 10)
MAX_LIST = 200
README_EMBEDDED = ("Project memory for this TouchDesigner project (TDflowMCP, embedded authority).\n"
                   "Agents add task checkpoints and handoffs here through TDflowMCP's memory tools only.\n"
                   "Each record is one Text DAT; its hash is in the TDflowMCP audit log. If you edit, delete or\n"
                   "copy records, TDflowMCP makes memory read-only until you pulse Claim Project Memory.\n"
                   "Save the project to keep new records.\n")
README_REPO = ("Project memory for this TouchDesigner project (TDflowMCP, repository authority).\n"
               "The records are files in the linked folder (Repository Folder parameter), under tdflow/memory/.\n"
               "This component holds only the link, the Project ID and 'derived_index', a DERIVED cache with\n"
               "counts. Nothing here is authoritative. If the files are edited, removed or replaced (for example\n"
               "by a Git checkout or pull), TDflowMCP makes memory read-only until you pulse Claim Project Memory.\n"
               "To move memory back into this component, use Project memory on the TDflowMCP Setup page.\n")


def _norm(text):
    return str(text).replace("\r\n", "\n").rstrip("\n")


def _cooks(o):
    try:
        return int(o.totalCooks)
    except Exception:
        return None


def _pos(o, attr):
    try:
        return int(getattr(o, attr))
    except Exception:
        return None


def _sha(text):
    """Hash of DAT text as TouchDesigner stores it (line endings and trailing newlines normalised)."""
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    p = getattr(v, "path", None)
    return p if isinstance(p, str) else str(v)


class TDAdapter(Adapter):
    def __init__(self, comp, root_fn, store_fn):
        self.comp = comp
        self.root_fn = root_fn                   # current Projectroot (main thread)
        self.store_fn = store_fn                 # current Store (for DAT snapshots)
        self.protected = {}                      # op path -> "instance" | "tdflowproject"
        self.memory_changed = False              # Phase 2A: a record was written (Tell Your Coding Agent refresh)
        self._mem_cache = {}                     # (record name, text sha) -> parsed entry (review 9, F5b)
        self.refresh_protected()

    # ---- protection registry (§3.4: registration, tags for discovery only) --------------------
    def _fresh(self):
        """Recompute protection at most once per frame, so renames/moves/new instances apply at once."""
        f = td.absTime.frame
        if getattr(self, "_prot_frame", None) != f:
            self.refresh_protected()
            self._prot_frame = f

    def path_facts(self, path):
        self._fresh()
        prot = next((k for pp, k in self.protected.items() if path == pp or path.startswith(pp + "/")), "")
        anc = next((k for pp, k in self.protected.items() if pp.startswith(path.rstrip("/") + "/")), "")
        return prot, anc

    def refresh_protected(self):
        prot = {self.comp.path: "instance"}
        try:
            for c in td.op("/").findChildren(tags=["TDflowMCP"]):
                prot[c.path] = "instance"
            for c in td.op("/").findChildren(tags=["tdflowProject"]):
                prot.setdefault(c.path, "tdflowproject")
        except Exception:
            pass
        self.protected = prot

    def _facts(self, o, action, **kw):
        given = kw.pop("path", None)
        p = o.path if o is not None else given
        protected = next((kind for pp, kind in self.protected.items() if p == pp or p.startswith(pp + "/")), "")
        ancestor = next((kind for pp, kind in self.protected.items() if pp.startswith(p.rstrip("/") + "/")), "")
        return Target(action=action, path=p, exists=o is not None, op_type=kw.pop("op_type", o.OPType if o else ""),
                      protected=protected, ancestor_of=ancestor, **kw)

    @staticmethod
    def _op(path, what="operator"):
        o = td.op(path) if isinstance(path, str) and path.startswith("/") else None
        if o is None or o.path != path:
            raise ToolFailure("NOT_FOUND", f"No {what} at {path} (paths must be absolute and exact).")
        return o

    # ---- resolve -------------------------------------------------------------------------------
    def resolve(self, tool, args):
        self._fresh()
        k = tool.resolver
        if k == "undo":
            return Resolution([])
        if k == "project":
            return Resolution([Target("read", self.root_fn())])
        if k in ("read_path", "read_children", "read_tree"):
            o = self._op(args["path"])
            if k == "read_children" and not o.isCOMP:
                raise ToolFailure("INVALID_ARGS", f"{o.path} is not a component.")
            if tool.name == "tdflow_get_dat_text" and not o.isDAT:
                raise ToolFailure("INVALID_ARGS", f"{o.path} is not a DAT.")
            return Resolution([self._facts(o, "read")], {"op": o})
        if k == "set_params":
            return self._resolve_params(args)
        if k == "create":
            return self._resolve_create(args)
        if k == "delete":
            o = self._op(args["path"])
            n = 1 + (len(o.findChildren()) if o.isCOMP else 0)
            return Resolution([self._facts(o, "delete", change="delete", count=n)], {"op": o, "count": n})
        if k == "wire":
            src, dst = self._op(args["src"]), self._op(args["dst"])
            si, di = int(args.get("src_index", 0)), int(args.get("dst_index", 0))
            if si >= len(src.outputConnectors) or di >= len(dst.inputConnectors):
                raise ToolFailure("INVALID_ARGS", "connector index out of range")
            if src.family != dst.family:
                raise ToolFailure("INVALID_ARGS", "src and dst must be the same operator family")
            ts = [self._facts(dst, "update", change="wire"), self._facts(src, "read")]
            if dst.family == "DAT" or dst.OPType in EXECUTABLE_TYPES:
                # Data wired into a DAT can be evaluated as Python downstream (review 6, H1): execute.
                ts.append(self._facts(dst, "execute", change="wire"))
            return Resolution(ts, {"src": src, "dst": dst, "si": si, "di": di})
        if k == "dat_text":
            o = self._op(args["path"], "DAT")
            if not o.isDAT:
                raise ToolFailure("INVALID_ARGS", f"{o.path} is not a DAT.")
            if o.OPType not in ("tableDAT", "textDAT") and o.OPType not in EXECUTABLE_TYPES:
                raise ToolFailure("INVALID_ARGS", f"{o.OPType} text cannot be edited.")
            if o.inputs:
                raise ToolFailure("INVALID_ARGS", "DATs with inputs cannot be edited.")
            ts = [self._facts(o, "update", change="text")]
            # Only DATs the user explicitly tagged as data; even tables can be evaluated downstream (review 6).
            data = o.OPType in ("tableDAT", "textDAT") and DATA_TAG in o.tags
            if not data:                       # TD may execute it (module, callbacks, script, eval): execute
                ts.append(self._facts(o, "execute", change="text"))
            before = o.text
            if len(before) > MAX_TEXT:
                raise ToolFailure("INVALID_ARGS", "the current text is too large to snapshot safely")
            # Plain-data snapshot; the engine persists it durably (audit worker) before execution.
            snap = (o.path, int(o.id), before, _sha(before), _sha(args.get("text", "")))
            return Resolution(ts, {"op": o, "snapshot": snap})
        raise ToolFailure("INVALID_ARGS", "unsupported tool")

    def _resolve_params(self, args):
        o = self._op(args["path"])
        values, exprs = args.get("values", {}), args.get("expressions", {}) or {}
        if not values and not exprs:
            raise ToolFailure("INVALID_ARGS", "nothing to set")
        ts, pars = [], {}
        for name in list(values) + list(exprs):
            par = getattr(o.par, name, None)
            if par is None or not hasattr(par, "style"):
                raise ToolFailure("INVALID_ARGS", f"{o.path} has no parameter {name}")
            if par.name != name:                                  # policy always sees the real name
                raise ToolFailure("INVALID_ARGS", f"use the exact parameter name {par.name}")
            if par.readOnly:
                raise ToolFailure("INVALID_ARGS", f"parameter {name} is read-only")
            if getattr(par, "isPulse", False) or par.style in ("Pulse", "Momentary"):
                raise ToolFailure("INVALID_ARGS", f"pulse parameters are not supported ({name})")
            if name in values and getattr(par, "isMenu", False) and str(values[name]) not in par.menuNames:
                raise ToolFailure("INVALID_ARGS", f"{name} must be one of {list(par.menuNames)[:20]}")
            pars[name] = par
            ext = name.startswith(("extension", "promoteextension")) or (name[:3] == "ext" and name[3:4].isdigit())
            change = "expression" if name in exprs else ("extension" if ext else "param")
            ts.append(self._facts(o, "update", change=change, param=name))
            if o.OPType in EXECUTABLE_TYPES and name in ACTIVATION_PARAMS:
                # D20: switching an executable operator on (or off) is a code-running change: approval-gated.
                ts.append(self._facts(o, "execute", change="activation", param=name))
            elif o.OPType in APPROVABLE_CREATE_TYPES:
                # Review 14: on Execute DATs every parameter decides *when* (or whether) code runs - callback
                # toggles, watched operators and parameters. No parameter there is treated as inert.
                ts.append(self._facts(o, "execute", change="trigger", param=name))
            if name in exprs or par.style in REFERENCE_STYLES:
                ts.append(self._facts(o, "execute", change="expression" if name in exprs else "reference",
                                      param=name))
        return Resolution(ts, {"op": o, "pars": pars})

    def _resolve_create(self, args):
        parent = self._op(args["parent"], "component")
        if not parent.isCOMP:
            raise ToolFailure("INVALID_ARGS", f"{parent.path} is not a component.")
        cls = getattr(td, args["type"], None)
        if not isinstance(cls, type) or not args["type"].endswith(("TOP", "CHOP", "SOP", "DAT", "MAT", "COMP", "POP")):
            raise ToolFailure("INVALID_ARGS", f"unknown operator type {args['type']}")
        if parent.op(args["name"]) is not None:
            raise ToolFailure("INVALID_ARGS", f"{parent.path}/{args['name']} already exists")
        path = parent.path.rstrip("/") + "/" + args["name"]
        t = [self._facts(None, "create", path=path, op_type=args["type"])]
        if args["type"] in EXECUTABLE_TYPES:
            t.append(self._facts(None, "execute", path=path, op_type=args["type"]))
        return Resolution(t, {"parent": parent, "cls": cls, "path": path})

    # ---- execute -------------------------------------------------------------------------------
    def _stack(self):
        try:
            return [str(e) for e in list(td.ui.undo.undoStack)][:50]
        except Exception:
            return None

    def _mutate(self, label, fn):
        """Run fn inside one named undo block. Returns (result, stack, recorded): ``recorded`` is True only if
        exactly one new entry landed on top of an unchanged stack (review 6), the precondition for undo."""
        u = td.ui.undo
        before = self._stack()
        u.startBlock(label)
        err = None
        try:
            out = fn()
        except Exception as e:
            err = e
        finally:
            u.endBlock()
        if err is not None:
            rolled = False
            st = self._stack()
            if st is not None and before is not None and st != before:
                u.undo()                        # roll back the partial change (our block is on top)
                rolled = True
            if isinstance(err, ToolFailure):
                err.extra["rolled_back"] = rolled
                raise err
            raise ToolFailure("EXECUTION_FAILED", "TouchDesigner rejected the change.", rolled_back=rolled,
                              _audit={"exc": type(err).__name__, "rolled_back": rolled})
        after = self._stack()
        # Exactly one new entry on top within this main-thread call is ours (its name may be the block label
        # or TD's own, e.g. "Delete Node", S4b). Stack lists are capped at 50 names.
        recorded = bool(after is not None and before is not None and len(after) >= 1
                        and after[1:] == before[:len(after) - 1]
                        and (len(after) == len(before) + 1 or len(after) == len(before) == 50))
        return out, (after if recorded else None)

    def execute(self, tool, args, res, label, readable):
        h = getattr(self, "_t_" + tool.handler)
        return h(args, res, label, readable)

    # -- reads --
    def _t_project_info(self, args, res, label, readable):
        root = td.op(self.root_fn())
        kids = [self._facts(c, "read") for c in (root.children if root is not None else [])]
        vis = readable(kids)
        return {"td_build": str(td.app.build), "td_version": str(td.app.version), "project_name": td.project.name,
                "project_root": self.root_fn(), "cook_rate": td.project.cookRate,
                "top_level": sorted(t.path.rsplit("/", 1)[1] for t in vis), "filtered": len(kids) - len(vis)}

    def _t_list_operators(self, args, res, label, readable):
        o = res.context["op"]
        kids = sorted(o.children, key=lambda c: c.name)
        facts = [self._facts(c, "read") for c in kids]
        vis = {t.path for t in readable(facts)}
        # Dogfood 1: inputs and node position per child, so agents need not call get_operator per child.
        # Review 8 (L2): the extra fields are built only for the requested page (main-thread cost).
        shown = [c for c in kids if c.path in vis]
        off, lim = int(args.get("offset", 0)), int(args.get("limit", 100))
        rows = [{"name": c.name, "path": c.path, "type": c.OPType, "family": c.family,
                 "inputs": self._conn_paths(list(c.inputs), readable), "x": _pos(c, "nodeX"), "y": _pos(c, "nodeY")}
                for c in shown[off:off + lim]]
        return {"path": o.path, "operators": rows, "total": len(shown),
                "next_offset": off + lim if off + lim < len(shown) else None, "filtered": len(kids) - len(shown)}

    def _conn_paths(self, ops, readable):
        facts = [self._facts(x, "read") for x in ops if x is not None]
        vis = {t.path for t in readable(facts)}
        return [x.path for x in ops if x is not None and x.path in vis]

    def _t_get_operator(self, args, res, label, readable):
        o = res.context["op"]
        flags = {}
        for f in ("bypass", "lock", "display", "render", "viewer", "allowCooking", "expose"):
            if hasattr(o, f):
                try:
                    flags[f] = bool(getattr(o, f))
                except Exception:
                    pass
        return {"path": o.path, "name": o.name, "type": o.OPType, "family": o.family, "flags": flags,
                "inputs": self._conn_paths(list(o.inputs), readable),
                "outputs": self._conn_paths(list(o.outputs), readable),
                "errors": (o.errors() or "")[:4000], "warnings": (o.warnings() or "")[:4000],
                "comment": (o.comment or "")[:1000], "num_children": len(o.children) if o.isCOMP else 0,
                "x": _pos(o, "nodeX"), "y": _pos(o, "nodeY"), "cook_count": _cooks(o)}

    def _t_get_parameters(self, args, res, label, readable):
        o = res.context["op"]
        page = args.get("page")
        names = set(args.get("names") or ())

        def page_name(p):                       # dogfood 1: some parameters have no page object
            try:
                return p.page.name if p.page is not None else ""
            except Exception:
                return ""
        pars = [p for p in o.pars() if (not page or page_name(p) == page) and (not names or p.name in names)]
        rows = []
        for p in pars:
            def g(attr, default=None, fn=lambda x: x):
                try:
                    return fn(getattr(p, attr))
                except Exception:
                    return default
            page_obj = g("page")
            r = {"name": p.name, "label": g("label", p.name), "page": getattr(page_obj, "name", "") if page_obj else "",
                 "style": g("style", ""), "mode": g("mode", "", lambda m: str(m).split(".")[-1]),
                 "custom": bool(g("isCustom", False)), "read_only": bool(g("readOnly", False))}
            try:
                r["value"] = _jsonable(p.eval())
            except Exception:
                r["value"] = None
            if r["mode"] == "EXPRESSION":
                r["expr"] = g("expr", "") or ""
            elif r["mode"] == "BIND":
                r["expr"] = g("bindExpr", "") or ""
            r["default"] = _jsonable(g("default"))
            if g("isMenu", False):
                r["menu"] = list(g("menuNames", []) or [])[:50]
            rows.append(r)
        off, lim = int(args.get("offset", 0)), int(args.get("limit", 100))
        return {"path": o.path, "parameters": rows[off:off + lim], "total": len(rows),
                "next_offset": off + lim if off + lim < len(rows) else None}

    def _t_get_dat_text(self, args, res, label, readable):
        o = res.context["op"]
        text = o.text
        cap = int(args.get("max_chars", 20000))
        return {"path": o.path, "type": o.OPType, "text": text[:cap], "length": len(text), "truncated": len(text) > cap,
                "sha256": _sha(text)}

    def _t_get_errors(self, args, res, label, readable):
        o = res.context["op"]
        cands = [o] + (list(o.findChildren()) if o.isCOMP else [])
        vis = {t.path for t in readable([self._facts(c, "read") for c in cands])}
        out, uncooked = [], []
        for c in cands:
            if c.path not in vis:
                continue
            e, w = c.errors() or "", c.warnings() or ""
            if e or w:
                out.append({"path": c.path, "errors": e[:2000], "warnings": w[:2000]})
            elif _cooks(c) == 0:
                uncooked.append(c.path)               # dogfood 1: errors exist only after a cook
            if len(out) >= int(args.get("limit", 100)):
                break
        r = {"path": o.path, "problems": out, "filtered": len(cands) - len(vis), "never_cooked": uncooked[:200],
             "never_cooked_total": len(uncooked), "never_cooked_truncated": len(uncooked) > 200,
             "complete": len(out) < int(args.get("limit", 100))}
        if uncooked:
            r["note"] = ("TouchDesigner reports errors only after an operator cooks. The operators in never_cooked "
                         "have not cooked yet, so their errors are unknown, not absent. This tool does not cook "
                         "anything to find out.")
        return r

    # -- mutations --
    def _t_set_parameters(self, args, res, label, readable):
        o, pars = res.context["op"], res.context["pars"]
        values, exprs = args.get("values", {}), args.get("expressions", {}) or {}
        prev = {}
        for name, par in pars.items():
            mode = str(par.mode).split(".")[-1]
            prev[name] = {"mode": mode, "value": _jsonable(par.eval())}
            if mode == "EXPRESSION":
                prev[name]["expr"] = str(par.expr)
        def do():
            for name, v in values.items():
                par = pars[name]
                const = type(par.mode).CONSTANT          # td.ParMode is not exposed on the td module
                if par.mode != const:
                    par.mode = const
                par.val = v
            # Phase 2B: expressions run as Python inside TouchDesigner. Policy classified them as execute, and
            # the engine only gets here after the user approved exactly this text (security-model §4).
            for name, text in exprs.items():
                par = pars[name]
                par.expr = str(text)
                par.mode = type(par.mode).EXPRESSION
        _, stack = self._mutate(label, do)
        now = {n: _jsonable(pars[n].eval()) for n in values}
        out = {"path": o.path, "previous": {n: prev[n]["value"] for n in values}, "now": now,
               "_audit": {"kind": "set_params", "path": o.path, "stack": stack, "prev": prev,
                          "reversible": "td_undo" if stack else "no"}}
        if exprs:
            out["expressions_set"] = {n: str(pars[n].expr) for n in exprs}
            out["previous_modes"] = {n: prev[n]["mode"] for n in exprs}
        return out

    def _t_create_operator(self, args, res, label, readable):
        parent, cls, path = res.context["parent"], res.context["cls"], res.context["path"]
        if parent.op(args["name"]) is not None:
            raise ToolFailure("INVALID_ARGS", "the operator already exists")
        # Dogfood finding (2A): without a position the node used to land at 0,0 on top of the network. Now it
        # goes to the right of the rightmost node (bounded read of the siblings' boxes; nothing is cooked).
        auto = None
        if "x" not in args and "y" not in args:
            boxes, kids = [], list(parent.children[:500])
            # Only siblings the caller can read: the answer returns the position, which must not reveal where
            # a hidden or protected node (the TDflowMCP component, for example) sits (review 11).
            vis = {t.path for t in readable([self._facts(c, "read") for c in kids])}
            for c in kids:
                if c.path not in vis:
                    continue
                try:
                    boxes.append((int(c.nodeX), int(c.nodeY), int(c.nodeWidth), int(c.nodeHeight)))
                except Exception:
                    pass
            auto = LAYOUT.default_position(boxes)

        staged = args["type"] in APPROVABLE_CREATE_TYPES

        def do():
            n = parent.create(cls, args["name"])
            if staged:
                # D20 staging: an approved executable DAT is created switched off. Its text and its activation are
                # separate, approval-gated changes.
                # Review 14: fail inside the undo block, so a create that cannot be left switched off is rolled back.
                for pn in ACTIVATION_PARAMS:
                    p = getattr(n.par, pn, None)
                    if p is None:
                        raise ToolFailure("EXECUTION_FAILED", f"the operator has no {pn} parameter to stage it off")
                    p.val = False
                    if bool(p.eval()):
                        raise ToolFailure("EXECUTION_FAILED", "the operator could not be created switched off")
            if auto is not None:
                n.nodeX, n.nodeY = auto
            if "x" in args:
                n.nodeX = int(args["x"])
            if "y" in args:
                n.nodeY = int(args["y"])
            return n
        n, stack = self._mutate(label, do)
        out = {"path": n.path, "type": n.OPType, "family": n.family, "x": _pos(n, "nodeX"), "y": _pos(n, "nodeY"),
               "_audit": {"kind": "create", "path": n.path, "type": n.OPType, "stack": stack,
                          "reversible": "td_undo" if stack else "no"}}
        if staged:
            out["staged_inactive"] = True                                  # checked inside do(), rolled back if not
        return out

    def _t_delete_operator(self, args, res, label, readable):
        o = res.context["op"]
        path, typ = o.path, o.OPType
        _, stack = self._mutate(label, o.destroy)
        if td.op(path) is not None:
            raise ToolFailure("EXECUTION_FAILED", "the operator still exists")
        return {"deleted": path, "count": res.context["count"],
                "_audit": {"kind": "delete", "path": path, "type": typ, "stack": stack,
                           "reversible": "td_undo" if stack else "no"}}

    def _wired(self, src, dst, di):
        conns = dst.inputConnectors[di].connections
        return any(c.owner.path == src.path for c in conns)

    def _t_connect(self, args, res, label, readable):
        c = res.context
        src, dst, si, di = c["src"], c["dst"], c["si"], c["di"]
        before = [x.owner.path for x in dst.inputConnectors[di].connections]
        _, stack = self._mutate(label, lambda: src.outputConnectors[si].connect(dst.inputConnectors[di]))
        if not self._wired(src, dst, di):
            raise ToolFailure("EXECUTION_FAILED", "the wire was not created")
        return {"src": src.path, "dst": dst.path, "src_index": si, "dst_index": di,
                "_audit": {"kind": "connect", "src": src.path, "dst": dst.path, "di": di, "before": before,
                           "stack": stack, "reversible": "td_undo" if stack else "no"}}

    def _t_disconnect(self, args, res, label, readable):
        c = res.context
        src, dst, di = c["src"], c["dst"], c["di"]
        if not self._wired(src, dst, di):
            raise ToolFailure("INVALID_ARGS", "those operators are not wired at that input")
        _, stack = self._mutate(label, lambda: dst.inputConnectors[di].disconnect())
        return {"src": src.path, "dst": dst.path, "dst_index": di,
                "_audit": {"kind": "disconnect", "src": src.path, "dst": dst.path, "di": di, "stack": stack,
                           "reversible": "td_undo" if stack else "no"}}

    def _t_set_dat_text(self, args, res, label, readable):
        o, text = res.context["op"], args["text"]
        snap, planned = res.context.get("snapshot_id"), res.context.get("planned_snapshot")
        before = o.text
        if snap is None or not planned or _sha(before) != planned[3]:
            # The durable snapshot must describe exactly the text being replaced.
            raise ToolFailure("PRECONDITION_CHANGED", "the DAT text changed since the request was checked; "
                              "nothing was written. Retry.")
        o.text = text
        if _sha(o.text) != _sha(text):
            o.text = before
            raise ToolFailure("EXECUTION_FAILED", "the DAT did not accept the text", rolled_back=True,
                              _audit={"rolled_back": _sha(o.text) == _sha(before)})
        return {"path": o.path, "length": len(text), "previous_sha256": _sha(before), "sha256": _sha(o.text),
                "_audit": {"kind": "dat_text", "path": o.path, "snapshot": snap, "after_sha": _sha(o.text),
                           "op_id": int(o.id), "type": o.OPType,
                           "before_sha": planned[3]}}             # anchors the snapshot in the hash chain

    # ---- undo ----------------------------------------------------------------------------------
    def undo(self, record, snapshot=None):
        d = record["detail"]
        kind = d.get("kind")
        if kind == "dat_text":
            return self._undo_text(d, snapshot)
        if not d.get("stack") or self._stack() != d.get("stack"):     # None/unrecorded is never 'unchanged'
            raise ToolFailure("UNDO_CONFLICT", "other changes happened since (or TouchDesigner was restarted)")
        td.ui.undo.undo()
        return {"undone": True, "verified": self._verify(d)}

    def _verify(self, d):
        kind, path = d.get("kind"), d.get("path")
        try:
            if kind == "create":
                return td.op(path) is None
            if kind == "delete":
                o = td.op(path)
                return o is not None and o.OPType == d.get("type")
            if kind == "set_params":
                o = td.op(path)
                return o is not None and all(
                    str(getattr(o.par, n).mode).split(".")[-1] == v["mode"] and
                    (v["mode"] != "CONSTANT" or _jsonable(getattr(o.par, n).eval()) == v["value"]) and
                    (v["mode"] != "EXPRESSION" or "expr" not in v or str(getattr(o.par, n).expr) == v["expr"])
                    for n, v in d.get("prev", {}).items())
            if kind in ("connect", "disconnect"):
                src, dst = td.op(d["src"]), td.op(d["dst"])
                if src is None or dst is None:
                    return False
                wired = self._wired(src, dst, d["di"])
                return (not wired) if kind == "connect" and d["src"] not in d.get("before", []) else wired
        except Exception:
            return False
        return False

    def _undo_text(self, d, snap):
        o = td.op(d["path"])
        expected = d.get("after_sha") or (snap or {}).get("after_sha")   # hash of the text actually written
        if snap is None or snap["restored_at"] or o is None or _sha(o.text) != expected:
            raise ToolFailure("UNDO_CONFLICT", "the DAT text changed since (or was already restored)")
        # Review 14: restore only into the same operator. A DAT replaced at that path (say by an Execute DAT
        # holding the same text) must not receive the old text without its own approval.
        if snap.get("op_id") is None or int(o.id) != int(snap["op_id"]) or \
                ("type" in d and o.OPType != d["type"]) or ("op_id" in d and int(o.id) != int(d["op_id"])):
            raise ToolFailure("UNDO_CONFLICT", "the DAT at this path was replaced since; nothing was restored")
        # Review 7: the snapshot row lives in the project folder; only restore text whose hash matches both the
        # row and the value recorded in the (chain-covered) outcome event.
        anchored = d.get("before_sha")
        if _sha(snap["before_text"]) != snap["before_sha"] or (anchored and anchored != snap["before_sha"]):
            raise ToolFailure("UNDO_FAILED", "the recovery snapshot does not match the audit log; nothing was restored")
        o.text = snap["before_text"]
        ok = _sha(o.text) == snap["before_sha"]
        return {"undone": ok, "verified": ok, "code": None if ok else "UNDO_FAILED"}

    # ---- project memory (Phase 2A, ADR-0012/0013) ----------------------------------------------
    def _memory_comps(self):
        root = td.op(self.root_fn())
        if root is None or not root.isCOMP:
            return []
        try:
            return [c for c in root.findChildren(tags=[MEM.MEMORY_TAG]) if c.isCOMP]
        except Exception:
            return []

    def memory_component(self):
        """(status, reason, comp). ``status``: ready | none | ambiguous | uninitialized | repo."""
        comps = self._memory_comps()
        if not comps:
            return "none", ("This project has no project memory yet. Ask the user to pulse Create Project Memory "
                            "on the TDflowMCP component's Advanced page."), None
        if len(comps) > 1:
            return "ambiguous", (f"There are {len(comps)} tdflowProject components; project memory needs exactly "
                                 "one (single authority). Ask the user to keep only one."), None
        c = comps[0]
        pid = getattr(c.par, "Projectid", None)
        if pid is None or not MEM.MEMORY_ID.match(str(pid.eval())):
            return "uninitialized", ("The tdflowProject component has no valid Projectid. Ask the user to create "
                                     "project memory with Create Project Memory."), c
        if self._authority(c) == "repo" and not self._repopath(c):
            return "uninitialized", ("Project memory is in repository mode but no folder is linked. Ask the user to "
                                     "link one on the TDflowMCP Setup page (Project memory)."), c
        return "ready", "", c

    @staticmethod
    def _authority(c):
        auth = getattr(c.par, "Memoryauthority", None)
        return "repo" if auth is not None and str(auth.eval()) == "repo" else "embedded"

    @staticmethod
    def _repopath(c):
        rp = getattr(c.par, "Repopath", None)
        return str(rp.eval()).strip() if rp is not None else ""

    @staticmethod
    def _record_dats(c):
        return [(d.name, d.text) for d in c.children if d.OPType == "textDAT" and MEM.RECORD_NAME.match(d.name)]

    def memory_entries(self):
        """Main thread: (status, comp_path, memory_id, entries) for the Setup page and Tell Your Coding Agent.
        In repository mode the entries are empty here (the records are files; see ``memory_info``)."""
        status, _, c = self.memory_component()
        if status != "ready":
            return status, (c.path if c is not None else None), None, []
        if self._authority(c) == "repo":
            return status, c.path, str(c.par.Projectid.eval()), []
        return status, c.path, str(c.par.Projectid.eval()), MEM.read_records(self._record_dats(c), self._mem_cache)

    def memory_info(self, with_dats=True):
        """Main thread, no disk I/O: where project memory lives. Plain data for other threads."""
        status, reason, c = self.memory_component()
        out = {"status": status, "reason": reason, "comp": c.path if c is not None else None, "memory_id": None,
               "authority": "embedded", "repopath": "", "project_folder": str(td.project.folder or ""), "dats": [],
               "derived": "",
               # A never-saved project still reports a folder; "saved" means this file exists (checked off the
               # main thread by the caller; review 11).
               "project_file": os.path.join(str(td.project.folder or ""), str(td.project.name or ""))}
        if c is None or status not in ("ready", "uninitialized"):
            return out
        pid = getattr(c.par, "Projectid", None)
        out.update(memory_id=str(pid.eval()) if pid is not None else None, authority=self._authority(c),
                   repopath=self._repopath(c), dats=self._record_dats(c) if with_dats else [])
        d = c.op(MEM.DERIVED_NAME)
        out["derived"] = d.text if d is not None and d.isDAT else ""
        return out

    @staticmethod
    def _op_digest(o, detail=False):
        """Type, parameters (constant values, expression/bind text), inputs, flags and authored DAT text.
        Nothing is evaluated (no cooking); node position is not included. ``detail``: also a short digest per
        parameter that is not at its default (increment 3: resume can say which parameter changed)."""
        parts, away = [o.OPType], {}
        try:
            pars = sorted(o.pars(), key=lambda p: p.name)
        except Exception:
            pars = []
        for p in pars:
            try:
                if p.readOnly or p.isPulse or getattr(p, "isMomentary", False):
                    continue
                mode = str(p.mode).split(".")[-1]
                if mode == "EXPRESSION":
                    v = "e:" + str(p.expr)
                elif mode == "BIND":
                    v = "b:" + str(p.bindExpr)
                elif mode == "EXPORT":
                    v = "x"
                else:
                    v = "c:" + repr(_jsonable(p.val))
            except Exception:
                v = "?"
            parts.append(p.name + "=" + v)
            if detail:
                try:
                    default = v.startswith("c:") and p.val == p.default
                except Exception:
                    default = False
                if not default:
                    away[p.name] = hashlib.sha256(v.encode("utf-8")).hexdigest()[:6]
        try:
            ins = [x.path for x in o.inputs if x is not None]
        except Exception:
            ins = []
        parts.append("in=" + ",".join(ins))
        for f in ("bypass", "display", "render"):
            try:
                parts.append(f + "=" + str(bool(getattr(o, f))))
            except Exception:
                pass
        if o.OPType in ("textDAT", "tableDAT") and not ins:        # authored text only (cooked DATs change alone)
            parts.append("t=" + _sha(o.text))
        digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
        return [digest, away] if detail else digest

    def memory_snapshot(self, task_id, scopes, claims, fingerprint, readable):
        self._fresh()
        out = {"status": "", "reason": "", "comp": None, "memory_id": None, "dats": [], "scopes": None,
               "scope_targets": [], "fingerprint": None, "fp_paths": [], "fp_total": 0, "live": {},
               "protected": dict(self.protected), "td_build": str(td.app.build), "authority": "embedded",
               "repopath": "", "project_folder": str(td.project.folder or ""), "needs_scopes": False}
        status, reason, c = self.memory_component()
        out.update(status=status, reason=reason)
        if status != "ready":
            return out
        repo = self._authority(c) == "repo"
        out.update(comp=c.path, memory_id=str(c.par.Projectid.eval()), authority="repo" if repo else "embedded",
                   repopath=self._repopath(c), dats=[] if repo else self._record_dats(c))
        if scopes is None and task_id and repo:
            # The task's recorded scopes are in the record files, which are never read on the main thread: the
            # engine reads them on its handler thread and asks again with the scopes.
            out["needs_scopes"] = bool(fingerprint)
            return out
        if scopes is None and task_id:
            last = MEM.latest(MEM.task_records(MEM.read_records(out["dats"], self._mem_cache), task_id))
            scopes = list(last["body"].get("scopes") or []) if last else None
        out["scopes"] = list(scopes) if scopes else None
        if not fingerprint or not scopes:
            return out
        found = []
        for s in scopes:
            o = td.op(s)
            o = o if o is not None and o.path == s else None
            found.append(o)
            out["scope_targets"].append(self._facts(o, "read", path=s))
        # Bounded breadth-first walk (review 9, F5): never more than MAX_WALK operators on the main thread.
        seen, ops, queue = set(), [], [o for o in found if o is not None]
        while queue and len(ops) < MAX_WALK:
            x = queue.pop(0)
            if x.path in seen:
                continue
            seen.add(x.path)
            ops.append(x)
            if x.isCOMP:
                queue.extend(x.children[:MAX_WALK])
        vis = {t.path for t in readable([self._facts(x, "read") for x in ops])}
        shown = sorted((x for x in ops if x.path in vis), key=lambda x: x.path)
        out["fp_total"] = len(shown)
        out["fp_paths"] = [x.path for x in shown[:MEM.MAX_FP_OPS]]     # digested in chunks by the engine (D18)
        claims = list(claims)[:50]
        for cl in claims:
            p = cl.get("path")
            if cl.get("kind") not in MEM.LIVE_CLAIMS or not isinstance(p, str) or p in out["live"]:
                continue
            o = td.op(p)
            o = o if o is not None and o.path == p else None
            ok = bool(readable([self._facts(o, "read", path=p)]))
            f = {"readable": ok, "exists": (o is not None) if ok else None, "values": {}}
            if ok and o is not None and any(c2.get("path") == p and c2.get("kind") == "input" for c2 in claims):
                ins = [x for x in o.inputs if x is not None]
                vis = {t.path for t in readable([self._facts(x, "read") for x in ins])}
                f["inputs"] = [x.path for x in ins if x.path in vis]
                srcs = [c2.get("value") for c2 in claims if c2.get("path") == p and c2.get("kind") == "input"
                        and isinstance(c2.get("value"), str)]
                f["sources"] = {sp: bool(readable([self._facts(None, "read", path=sp)])) for sp in srcs[:20]}
            if ok and o is not None:
                for c2 in claims:
                    if c2.get("path") == p and c2.get("kind") == "value" and c2.get("param"):
                        par = getattr(o.par, str(c2["param"]), None)
                        try:
                            if par is not None and str(par.mode).split(".")[-1] == "CONSTANT" and not par.readOnly:
                                f["values"][str(c2["param"])] = _jsonable(par.eval())
                        except Exception:
                            pass
            out["live"][p] = f
        return out

    def memory_digests(self, paths):
        out = {}
        for p in list(paths)[:MEM.MAX_FP_OPS]:
            o = td.op(p)
            if o is not None and o.path == p:
                out[p] = self._op_digest(o, True)          # [digest, {parameter: digest}]
        return out

    # -- repository mode (increment 3): the component holds only the pointer and a derived index --
    def memory_check(self, comp, memory_id, authority, repopath):
        status, _, c = self.memory_component()
        if (status != "ready" or c.path != comp or str(c.par.Projectid.eval()) != memory_id
                or self._authority(c) != authority or self._repopath(c) != repopath):
            raise ToolFailure("MEMORY_CONFLICT", "project memory changed since the request was checked; nothing "
                              "was recorded. Retry.")
        return {"ok": True}

    def memory_derived(self, comp, memory_id, text):
        status, _, c = self.memory_component()
        if status != "ready" or c.path != comp or str(c.par.Projectid.eval()) != memory_id or self._authority(c) != "repo":
            return {"written": False}
        d = c.op(MEM.DERIVED_NAME)
        if d is None:
            d = c.create(td.textDAT, MEM.DERIVED_NAME)
            d.nodeX, d.nodeY = 200, 0
        if d.text != text:
            d.text = text
        self.memory_changed = True
        return {"written": True}

    @staticmethod
    def _set_ro(par, value):
        par.readOnly = False
        par.val = value
        par.readOnly = True

    def _memory_pars(self, c):
        """The Project page's parameters, added to components created before repository mode existed."""
        auth = c.par.Memoryauthority
        auth.menuLabels = ["Embedded (this component)", "Repository folder (linked on the Setup page)"]
        if getattr(c.par, "Repopath", None) is None:
            pg = next((p for p in c.customPages if p.name == "Project"), None) or c.appendCustomPage("Project")
            rp = pg.appendStr("Repopath", label="Repository Folder")[0]
            rp.val = ""
            rp.readOnly = True
        return auth, c.par.Repopath

    def memory_to_repo(self, comp, memory_id, repopath, index_text, expect_digest, stamp):
        """User-confirmed migration, last step (main thread): the folder already holds verified copies of
        exactly these records. The component becomes a pointer: record DATs are removed, the derived index and
        a migration note stay. Refused if the records changed since they were copied."""
        status, _, c = self.memory_component()
        if status != "ready" or c.path != comp or str(c.par.Projectid.eval()) != memory_id or self._authority(c) != "embedded":
            raise ValueError("project memory changed during the migration; nothing was switched")
        if MEM.dats_digest(self._record_dats(c)) != expect_digest:
            raise ValueError("a record changed during the migration; nothing was switched")
        # Nothing was changed up to here (a ValueError above means "refused, nothing changed"). From here on the
        # link is set first and the record DATs go last: if anything fails in between, the component already
        # points at the verified files and merely keeps stale copies, which repository mode never reads.
        auth, rp = self._memory_pars(c)
        self._set_ro(rp, repopath)
        self._set_ro(auth, "repo")
        self._note(c, MEM.DERIVED_NAME, index_text, 200, 0)
        self._note(c, "MIGRATIONS", ((c.op("MIGRATIONS").text.rstrip("\n") + "\n") if c.op("MIGRATIONS") else "") + stamp + "\n",
                   400, 0)
        self._note(c, "README", README_REPO, 0, 0)
        for d in [x for x in c.children if x.OPType == "textDAT" and MEM.RECORD_NAME.match(x.name)]:
            d.destroy()
        self._mem_cache.clear()
        self.memory_changed = True
        return {"switched": True, "authority": "repo"}

    def memory_relink(self, comp, memory_id, repopath, index_text, stamp):
        """User-confirmed re-link (main thread): a component already in repository mode points at ``repopath``
        again (the .toe was not saved after a migration, the project was saved elsewhere, or the folder's
        project file was lost). Only the link and the derived index change; the records stay subject to the
        audit ledger."""
        comps = self._memory_comps()
        c = comps[0] if len(comps) == 1 else None
        pid = getattr(c.par, "Projectid", None) if c is not None else None
        if c is None or c.path != comp or pid is None or str(pid.eval()) != memory_id or self._authority(c) != "repo":
            raise ValueError("project memory changed during the request; nothing was re-linked")
        auth, rp = self._memory_pars(c)
        self._set_ro(rp, repopath)
        self._note(c, MEM.DERIVED_NAME, index_text, 200, 0)
        self._note(c, "MIGRATIONS", ((c.op("MIGRATIONS").text.rstrip("\n") + "\n") if c.op("MIGRATIONS") else "") + stamp + "\n",
                   400, 0)
        self._note(c, "README", README_REPO, 0, 0)
        self._mem_cache.clear()
        self.memory_changed = True
        return {"relinked": True, "authority": "repo"}

    def memory_to_embedded(self, comp, memory_id, records, stamp):
        """User-confirmed migration back (main thread): the component's record DATs become exactly ``records``
        ((name, text) read and link-checked from the folder)."""
        status, _, c = self.memory_component()
        if status != "ready" or c.path != comp or str(c.par.Projectid.eval()) != memory_id or self._authority(c) != "repo":
            raise ValueError("project memory changed during the migration; nothing was switched")
        want = dict(records)
        for d in [x for x in c.children if x.OPType == "textDAT" and MEM.RECORD_NAME.match(x.name)]:
            if d.name not in want or MEM.text_sha(d.text) != MEM.text_sha(want[d.name]):
                d.destroy()
        for i, (name, text) in enumerate(sorted(want.items())):
            if c.op(name) is None:
                d = c.create(td.textDAT, name)
                d.nodeX, d.nodeY = (i % 8) * 160, -160 - (i // 8) * 130
                d.text = text
        if MEM.dats_digest(self._record_dats(c)) != MEM.dats_digest(list(want.items())):
            # Not a ValueError: record DATs were already changed, so this is not "refused, nothing changed".
            raise RuntimeError("the component did not accept the records; the authority was not switched")
        auth, rp = self._memory_pars(c)
        self._set_ro(auth, "embedded")
        self._set_ro(rp, "")
        idx = c.op(MEM.DERIVED_NAME)
        if idx is not None:
            idx.destroy()
        self._note(c, "MIGRATIONS", ((c.op("MIGRATIONS").text.rstrip("\n") + "\n") if c.op("MIGRATIONS") else "") + stamp + "\n",
                   400, 0)
        self._note(c, "README", README_EMBEDDED, 0, 0)
        self._mem_cache.clear()
        self.memory_changed = True
        return {"switched": True, "authority": "embedded", "records": len(want)}

    @staticmethod
    def _note(c, name, text, x, y):
        d = c.op(name)
        if d is None:
            d = c.create(td.textDAT, name)
            d.nodeX, d.nodeY = x, y
        d.text = text
        return d

    def memory_write(self, comp, memory_id, name, text, expect_digest):
        status, _, c = self.memory_component()
        if (status != "ready" or c.path != comp or str(c.par.Projectid.eval()) != memory_id
                or self._authority(c) != "embedded"):
            raise ToolFailure("MEMORY_CONFLICT", "project memory changed since the request was checked; nothing "
                              "was recorded. Retry.")
        if MEM.dats_digest(self._record_dats(c)) != expect_digest or c.op(name) is not None:
            raise ToolFailure("MEMORY_CONFLICT", "another record was written since the request was checked; nothing "
                              "was recorded. Resume the task and retry with its latest revision.")
        n = len(c.children)
        d = c.create(td.textDAT, name)
        d.nodeX, d.nodeY = (n % 8) * 160, -160 - (n // 8) * 130
        d.text = text
        if MEM.text_sha(d.text) != MEM.text_sha(text):
            d.destroy()
            raise ToolFailure("EXECUTION_FAILED", "the record DAT did not accept the text; nothing was recorded")
        self.memory_changed = True
        return {"written": True, "path": d.path}

    def create_memory(self, near=None):
        """User action (Create Project Memory pulse): a tagged Base COMP with a new Projectid."""
        root = td.op(self.root_fn())
        if root is None or not root.isCOMP:
            raise ValueError("Project Root is not an existing component.")
        existing = self._memory_comps()
        if existing:
            raise ValueError("Project memory already exists: " + existing[0].path + ".")
        name, i = "tdflowProject", 1
        while root.op(name) is not None:
            name, i = f"tdflowProject{i}", i + 1
        c = root.create(td.baseCOMP, name)
        c.tags.add(MEM.MEMORY_TAG)
        pg = c.appendCustomPage("Project")
        pid = pg.appendStr("Projectid", label="Project ID")[0]
        pid.val = MEM.new_memory_id()
        pid.readOnly = True
        auth = pg.appendMenu("Memoryauthority", label="Memory Authority")[0]
        auth.menuNames = ["embedded", "repo"]
        auth.menuLabels = ["Embedded (this component)", "Repository folder (linked on the Setup page)"]
        auth.val = "embedded"
        auth.readOnly = True
        rp = pg.appendStr("Repopath", label="Repository Folder")[0]
        rp.val = ""
        rp.readOnly = True
        readme = c.create(td.textDAT, "README")
        readme.text = README_EMBEDDED
        readme.nodeX, readme.nodeY = 0, 0
        if near is not None:
            c.nodeX, c.nodeY = near.nodeX, near.nodeY - 200
        self.refresh_protected()
        self.memory_changed = True
        return c.path, str(pid.eval())
