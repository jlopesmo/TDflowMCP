"""Unit tests for the pure-Python core (stdlib unittest, plain CPython 3.11, no TouchDesigner)."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
