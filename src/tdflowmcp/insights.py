"""Network summary and optimization hints (Phase 4 extensions; dogfood finding 5). Pure Python, no `td`.

The TD adapter collects plain facts per readable operator (type, family, inputs, flags, error and warning text,
cook counts and times as TouchDesigner already measured them). Nothing here cooks or evaluates anything. Hints
are heuristics, labelled as such: they point at where to look, they are not findings.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Sequence

MAX_ROWS = 300
SLOW_MS = 2.0              # one operator's last cook (ms) worth mentioning at 60 fps
FRAME_MS = 1000.0 / 60.0


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
    cooked = [r for r in rows if isinstance(r.get("cook_ms"), (int, float))]
    total_ms = sum(r["cook_ms"] for r in cooked)
    for r in sorted(cooked, key=lambda r: -r["cook_ms"])[:10]:
        if r["cook_ms"] >= SLOW_MS:
            out.append({"path": r["path"], "kind": "slow_cook", "value_ms": round(r["cook_ms"], 3),
                        "why": f"its last cook took {r['cook_ms']:.2f} ms ({100 * r['cook_ms'] / FRAME_MS:.0f} % of a "
                               "60 fps frame). Check its resolution, inputs and parameters"})
    if total_ms > FRAME_MS:
        out.append({"path": None, "kind": "frame_budget", "value_ms": round(total_ms, 3),
                    "why": f"the listed operators' last cooks add up to {total_ms:.1f} ms, more than one 60 fps frame"})
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
