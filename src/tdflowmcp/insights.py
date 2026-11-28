"""Network summary and optimization hints (Phase 4 extensions; dogfood finding 5). Pure Python, no `td`.

The TD adapter collects plain facts per readable operator (type, family, inputs, flags, error and warning text,
cook counts and times as TouchDesigner already measured them). Nothing here cooks or evaluates anything. Hints
are heuristics, labelled as such: they point at where to look, they are not findings.
"""
from __future__ import annotations

import ast
from collections import deque
from math import isfinite
import re
from typing import Dict, List, Mapping, Sequence

MAX_ROWS = 300
SLOW_MS = 2.0              # one operator's last cook (ms) worth mentioning at 60 fps
FRAME_MS = 1000.0 / 60.0
MAX_FINDINGS = 12
_REFERENCE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def literal_reference(source: str):
    """Recognize one exact stored op('path').par.Name or op('path')['channel'] expression."""
    if not source or len(source) > 512:
        return None
    try:
        node = ast.parse(source.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return None
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "par":
        call, kind, name = node.value.value, "parameter", node.attr
    elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        call, kind, name = node.value, "channel", node.slice.value
    else:
        return None
    if not isinstance(name, str) or not _REFERENCE_NAME.fullmatch(name):
        return None
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "op"
            and len(call.args) == 1 and not call.keywords and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)):
        return call.args[0].value, kind, name
    return None


def _measured_cooks(rows):
    return [r for r in rows if (r.get("cook_count") or 0) > 0
            and isinstance(r.get("cook_ms"), (int, float)) and isfinite(r["cook_ms"]) and r["cook_ms"] >= 0]


def _upstream_findings(path, by_path, observations):
    queue = deque([(path, 0)])
    seen, found = {path}, []
    while queue and len(found) < 5:
        current, distance = queue.popleft()
        if distance and current in observations:
            found.append({"path": current, "hops": distance, "observations": observations[current]})
        if distance < 3:
            for src in by_path[current]["inputs"]:
                if len(seen) >= 40:
                    break
                if src in by_path and src not in seen:
                    seen.add(src)
                    queue.append((src, distance + 1))
    return found


def diagnostics(rows, partial, input_notes):
    """Explain the available evidence within the scoped, bounded network snapshot."""
    by_path = {row["path"]: row for row in rows}
    observations = {}
    for row in rows:
        path = row["path"]
        labels = [key for key, present in (
            ("confirmed_error", row.get("errors")), ("observed_warning", row.get("warnings")),
            ("missing_required_inputs", input_notes.get(path)), ("bypassed", row.get("bypass") is True),
            ("cooking_disabled", row.get("allow_cooking") is False),
            ("never_cooked", row.get("cook_count") == 0), ("cook_state_unavailable", row.get("cook_count") is None)
        ) if present]
        if labels:
            observations[path] = labels
    ordered = sorted(observations, key=lambda path: (
        "confirmed_error" not in observations[path], "observed_warning" not in observations[path], path))
    findings = []
    for path in ordered[:MAX_FINDINGS]:
        evidence = {"path": path, "observations": observations[path]}
        if path in input_notes:
            evidence["wiring"] = input_notes[path]
        if "confirmed_error" in observations[path] or "observed_warning" in observations[path]:
            evidence["upstream_leads"] = _upstream_findings(path, by_path, observations)
        findings.append(evidence)

    measured = _measured_cooks(rows)
    ranked = sorted(measured, key=lambda row: (-row["cook_ms"], row["path"]))[:8]
    return {
        "partial_view": partial,
        "findings": findings,
        "findings_truncated": len(ordered) > len(findings),
        "trace_note": "Upstream leads follow readable wires up to three hops. They indicate possible causes. "
                      "Warnings and errors are recorded by TouchDesigner. Uncooked operators have unknown results.",
        "performance": {
            "units": "ms", "reference_frame_ms": round(FRAME_MS, 4), "sample_count": len(measured),
            "operators_without_sample": len(rows) - len(measured),
            "top": [dict(path=row["path"], last_cpu_cook_ms=row["cook_ms"], cook_abs_frame=row.get("cook_abs_frame"),
                         reference_frame_budget_pct=round(100 * row["cook_ms"] / FRAME_MS, 2)) for row in ranked],
            "note": "One last CPU cook measurement per sampled operator. Samples may be old or from different "
                    "frames. The 60 fps reference is a comparison budget. These measurements do not report FPS."
        }
    }


def summarize(rows: Sequence[Mapping], total: int, filtered: int) -> Dict:
    """``rows``: facts per operator (already filtered to what the caller can read). A compact network view."""
    rows = list(rows)[:MAX_ROWS]
    by_family: Dict[str, int] = {}
    for r in rows:
        by_family[r.get("family") or "?"] = by_family.get(r.get("family") or "?", 0) + 1
    paths = {r["path"] for r in rows}
    edges = [[src, r["path"]] for r in rows for src in (r.get("inputs") or []) if src in paths]
    return {"operators": [{k: r.get(k) for k in ("path", "type", "family", "inputs", "bypass", "display", "errors",
                                                 "warnings", "cook_count", "cook_ms")} for r in rows],
            "edges": edges[:1000], "by_family": dict(sorted(by_family.items())),
            "with_errors": sorted(r["path"] for r in rows if r.get("errors"))[:100],
            "never_cooked": sorted(r["path"] for r in rows if r.get("cook_count") == 0)[:100],
            "total": total, "shown": len(rows), "truncated": total > len(rows), "filtered": filtered}


def hints(rows: Sequence[Mapping]) -> List[Dict]:
    """Heuristic optimization hints, most expensive first. Each says what it is based on."""
    out: List[Dict] = []
    rows = list(rows)
    cooked = _measured_cooks(rows)
    total_ms = sum(r["cook_ms"] for r in cooked)
    for r in sorted(cooked, key=lambda r: -r["cook_ms"])[:10]:
        if r["cook_ms"] >= SLOW_MS:
            out.append({"path": r["path"], "kind": "slow_cook", "value_ms": round(r["cook_ms"], 3),
                        "why": f"its last cook took {r['cook_ms']:.2f} ms ({100 * r['cook_ms'] / FRAME_MS:.0f} % of a "
                               "60 fps frame). Check its resolution, inputs and parameters"})
    if total_ms > FRAME_MS:
        out.append({"path": None, "kind": "frame_budget", "value_ms": round(total_ms, 3),
                    "why": f"last CPU cook samples total {total_ms:.1f} ms against a 60 fps reference budget. "
                           "Samples can come from different frames"})
    for r in rows:
        if r.get("errors"):
            out.append({"path": r["path"], "kind": "error", "why": "reports an error: " + str(r["errors"])[:200]})
        if r.get("family") == "TOP" and isinstance(r.get("resolution"), (list, tuple)) and len(r["resolution"]) == 2:
            w, h = r["resolution"]
            if w * h >= 3840 * 2160:
                out.append({"path": r["path"], "kind": "large_texture", "value": [w, h],
                            "why": f"works at {w}x{h}; downstream TOPs inherit it unless their resolution is set"})
    outputs = {s for r in rows for s in (r.get("inputs") or [])}
    for r in rows:
        if r.get("family") in ("TOP", "CHOP", "SOP") and r["path"] not in outputs and not r.get("display") \
                and not r.get("render") and r.get("cook_count", 0) > 0 and r.get("type") not in ("nullTOP", "nullCHOP", "nullSOP", "outTOP", "outCHOP", "outSOP"):
            out.append({"path": r["path"], "kind": "dead_end", "why": "nothing readable uses its output, but it cooks; "
                        "it may be left over (or used through a reference this summary cannot see)"})
    return out[:50]
