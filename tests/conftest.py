"""pytest path setup for the offline test suite (tests/offline/).

The pipeline/ and backend/ directories are flat module folders (no package), so both are put
on sys.path exactly the way backend/deps.py and the existing script-style tests do it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("pipeline", "backend", "tests/offline"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)
