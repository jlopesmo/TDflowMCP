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

import td

from tdflowmcp.engine import Adapter, Resolution, ToolFailure
from tdflowmcp.policy import EXECUTABLE_TYPES, Target

DATA_TAG = "tdflowdata"
# Parameter styles whose value names another operator or a file: setting them can read or wire things
# outside the agent's scope (review 6, H2), so they are classified as execute (denied in Phase 1).
REFERENCE_STYLES = {"OP", "COMP", "OBJ", "Object", "PanelCOMP", "TOP", "CHOP", "SOP", "DAT", "MAT", "POP", "File",
                    "Folder", "FileSave", "Python"}
MAX_TEXT = 200000
MAX_LIST = 200


def _norm(text):
    return str(text).replace("\r\n", "\n").rstrip("\n")


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
        p = o.path if o is not None else kw.pop("path")
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
        rows = [{"name": c.name, "path": c.path, "type": c.OPType, "family": c.family} for c in kids if c.path in vis]
        off, lim = int(args.get("offset", 0)), int(args.get("limit", 100))
        return {"path": o.path, "operators": rows[off:off + lim], "total": len(rows),
                "next_offset": off + lim if off + lim < len(rows) else None, "filtered": len(kids) - len(rows)}

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
                "comment": (o.comment or "")[:1000], "num_children": len(o.children) if o.isCOMP else 0}

    def _t_get_parameters(self, args, res, label, readable):
        o = res.context["op"]
        page = args.get("page")
        pars = [p for p in o.pars() if (not page or p.page.name == page)]
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
        out = []
        for c in cands:
            if c.path not in vis:
                continue
            e, w = c.errors() or "", c.warnings() or ""
            if e or w:
                out.append({"path": c.path, "errors": e[:2000], "warnings": w[:2000]})
            if len(out) >= int(args.get("limit", 100)):
                break
        return {"path": o.path, "problems": out, "filtered": len(cands) - len(vis)}

    # -- mutations --
    def _t_set_parameters(self, args, res, label, readable):
        o, pars = res.context["op"], res.context["pars"]
        values = args.get("values", {})
        prev = {}
        for name, par in pars.items():
            prev[name] = {"mode": str(par.mode).split(".")[-1], "value": _jsonable(par.eval())}
        def do():
            for name, v in values.items():
                par = pars[name]
                const = type(par.mode).CONSTANT          # td.ParMode is not exposed on the td module
                if par.mode != const:
                    par.mode = const
                par.val = v
        _, stack = self._mutate(label, do)
        now = {n: _jsonable(pars[n].eval()) for n in values}
        return {"path": o.path, "previous": {n: prev[n]["value"] for n in values}, "now": now,
                "_audit": {"kind": "set_params", "path": o.path, "stack": stack, "prev": prev,
                           "reversible": "td_undo" if stack else "no"}}

    def _t_create_operator(self, args, res, label, readable):
        parent, cls, path = res.context["parent"], res.context["cls"], res.context["path"]
        if parent.op(args["name"]) is not None:
            raise ToolFailure("INVALID_ARGS", "the operator already exists")
        def do():
            n = parent.create(cls, args["name"])
            if "x" in args:
                n.nodeX = int(args["x"])
            if "y" in args:
                n.nodeY = int(args["y"])
            return n
        n, stack = self._mutate(label, do)
        return {"path": n.path, "type": n.OPType, "family": n.family,
                "_audit": {"kind": "create", "path": n.path, "type": n.OPType, "stack": stack,
                           "reversible": "td_undo" if stack else "no"}}

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
                    (v["mode"] != "CONSTANT" or _jsonable(getattr(o.par, n).eval()) == v["value"])
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
        # Review 7: the snapshot row lives in the project folder; only restore text whose hash matches both the
        # row and the value recorded in the (chain-covered) outcome event.
        anchored = d.get("before_sha")
        if _sha(snap["before_text"]) != snap["before_sha"] or (anchored and anchored != snap["before_sha"]):
            raise ToolFailure("UNDO_FAILED", "the recovery snapshot does not match the audit log; nothing was restored")
        o.text = snap["before_text"]
        ok = _sha(o.text) == snap["before_sha"]
        return {"undone": ok, "verified": ok, "code": None if ok else "UNDO_FAILED"}
