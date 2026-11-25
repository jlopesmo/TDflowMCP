"""Node placement for operators an agent creates without a position (dogfood finding, Phase 2A). Pure Python.

New operators used to land at 0,0, on top of the existing network. The rule here is deliberately simple and
predictable: to the right of the rightmost node, on that node's row.
"""
from __future__ import annotations

from typing import Sequence, Tuple

GAP = 50                 # free space between the rightmost node and the new one (network units)
LIMIT = 100000           # the tool schema's coordinate range


def default_position(siblings: Sequence[Tuple[int, int, int, int]]) -> Tuple[int, int]:
    """``siblings``: (x, y, width, height) of the nodes already in the parent. (0, 0) in an empty network."""
    boxes = [s for s in siblings if len(s) == 4 and all(isinstance(v, (int, float)) for v in s)]
    if not boxes:
        return 0, 0
    x, y, w, _ = max(boxes, key=lambda s: (s[0] + max(0, s[2]), -s[1]))
    nx = int(x + max(0, w) + GAP)
    return max(-LIMIT, min(LIMIT, nx)), max(-LIMIT, min(LIMIT, int(y)))
