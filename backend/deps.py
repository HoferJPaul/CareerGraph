"""Shared FastAPI dependencies: read-only Neo4j sessions and access to the
existing CareerGraph pipeline modules in pipeline/ (matching.py, tailor_cv.py,
capability_suggest.py, metrics.py, requirement_schema.py, match_job.py,
setup_schema.py, ingest_career.py).

The pipeline modules are NOT duplicated or reimplemented here -- this file
only makes them importable from backend/ and provides one shared, read-only
driver for the API routes to reuse.
"""
import sys
import threading
from pathlib import Path
from typing import Generator, Optional

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
PIPELINE_DIR = ROOT_DIR / "pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from neo4j import Driver, GraphDatabase  # noqa: E402
from setup_schema import load_env  # noqa: E402

# The driver is created on first use, not at import time: importing the API (and running the
# offline test suite against dependency-overridden routes) must not require Neo4j credentials.
_driver: Optional[Driver] = None
_database: Optional[str] = None
_driver_lock = threading.Lock()


def _get_driver() -> Driver:
    global _driver, _database
    with _driver_lock:
        if _driver is None:
            env = load_env(ROOT_DIR / ".env")
            _driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
            _database = env["NEO4J_DATABASE"]
        return _driver


def get_session() -> Generator:
    """FastAPI dependency yielding a READ-ONLY Neo4j session.

    default_access_mode="READ" is enforced here as the single choke point for
    every route in this API -- no route may open its own driver/session, so
    this is the one place a write-mode session could ever be introduced, and
    it deliberately never is.
    """
    driver = _get_driver()
    with driver.session(database=_database, default_access_mode="READ") as session:
        yield session


def shutdown_driver() -> None:
    with _driver_lock:
        if _driver is not None:
            _driver.close()
