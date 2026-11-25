"""Run the unit tests the way CI does; on GitHub Actions, publish every failure as an annotation.

    python tools/ci_unittest.py

Same tests as ``python -m unittest discover -s tests -t . -v``. Why this exists: GitHub serves job logs only to
signed-in users, while the annotations of a public repository's check run are public. A failing test's name and
traceback are therefore readable from the Actions API without credentials (the work PC has no ``gh`` CLI).
The traceback of a unit test holds no secret: the tests use fixed fake tokens and temporary directories.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_ANNOTATIONS = 10            # GitHub shows at most ten error annotations per step
MAX_CHARS = 1800


def annotation(kind: str, test_id: str, tb: str) -> str:
    """One ``::error`` workflow command: the last line of the traceback first, then the whole of it."""
    lines = [x for x in tb.strip().splitlines() if x.strip()]
    text = ((lines[-1] if lines else "") + "\n" + tb.strip())[:MAX_CHARS]
    data = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = f"{kind} {test_id}".replace("%", "%25").replace("\r", "").replace("\n", " ").replace(",", "%2C").replace(
        ":", "%3A")
    return f"::error title={title}::{data}"


def main() -> int:
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        problems = [("ERROR", t, tb) for t, tb in result.errors] + [("FAIL", t, tb) for t, tb in result.failures]
        for kind, test, tb in problems[:MAX_ANNOTATIONS]:
            print(annotation(kind, test.id() if hasattr(test, "id") else str(test), tb))
        if len(problems) > MAX_ANNOTATIONS:
            print(f"::error title=More failures::{len(problems) - MAX_ANNOTATIONS} more; see the job log")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
