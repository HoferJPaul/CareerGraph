"""pytest setup for the test suites under tests/.

  * The pipeline/ and backend/ directories are flat module folders (no package), so both are put on
    sys.path exactly the way backend/deps.py and the existing script-style tests do it.
  * PRIVATE DATA ISOLATION: the Source CV store lives under a gitignored private directory (by default
    data/private). Tests must never read or write the developer's real stored CV, so every test run is
    pointed at a throwaway directory via CAREERGRAPH_PRIVATE_DIR before any service is built.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("pipeline", "backend", "tests/offline"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

_PRIVATE = tempfile.mkdtemp(prefix="careergraph-test-private-")
os.environ["CAREERGRAPH_PRIVATE_DIR"] = _PRIVATE


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    import shutil

    shutil.rmtree(_PRIVATE, ignore_errors=True)
