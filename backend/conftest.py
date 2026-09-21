"""pytest setup for the API tests run from backend/ (test_api.py, test_requirements_route.py).

The Source CV store lives under a gitignored private directory (default data/private). These tests must
never read or write the developer's real stored CV, so they are pointed at a throwaway directory.
"""
import os
import shutil
import tempfile

_PRIVATE = tempfile.mkdtemp(prefix="careergraph-test-private-")
os.environ["CAREERGRAPH_PRIVATE_DIR"] = _PRIVATE


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_PRIVATE, ignore_errors=True)
