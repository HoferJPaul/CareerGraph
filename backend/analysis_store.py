"""In-memory store of server-produced analyses, keyed by an unguessable id.

Why it exists: CV generation must only ever start from the CVContext THIS SERVER built from
Neo4j evidence. If /api/cv/generate accepted a CVContext from the browser, anyone could post
fabricated "evidence" and have it written up as verified. So /api/jobs/analyze stores its
CVContext here and returns only an `analysisId`; /api/cv/generate looks the context up.

It also replaces the old shared output/requirements.json: each analysis has its own id, nothing
is written to disk, and concurrent analyses cannot overwrite or leak into one another.

Retention: process memory only. Entries expire after TTL_SECONDS, at most MAX_ENTRIES are kept
(oldest evicted first), and everything is lost on restart -- the UI asks the user to re-run the
analysis if an id is gone. The store holds career evidence, so nothing here is logged.
"""
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

from source_cv.reconcile import Reconciliation
from source_cv.schema import SourceProfile
from tailor_cv import CVContext

TTL_SECONDS = 60 * 60
MAX_ENTRIES = 100


@dataclass(frozen=True)
class AnalysisRecord:
    analysis_id: str
    cv_context: CVContext
    created_at: float
    # The Source CV as it was WHEN THIS ANALYSIS RAN (an immutable deep copy), and its reconciliation with
    # the graph evidence. Later edits, replacement or deletion of the stored profile never reach them, and
    # the browser never supplies either.
    source_profile: Optional[SourceProfile] = None
    reconciliation: Optional[Reconciliation] = None


class AnalysisStore:
    def __init__(
        self,
        max_entries: int = MAX_ENTRIES,
        ttl_seconds: float = TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._records: "OrderedDict[str, AnalysisRecord]" = OrderedDict()
        self._lock = threading.Lock()

    def put(
        self,
        cv_context: CVContext,
        source_profile: Optional[SourceProfile] = None,
        reconciliation: Optional[Reconciliation] = None,
    ) -> AnalysisRecord:
        record = AnalysisRecord(
            analysis_id=secrets.token_urlsafe(16),
            cv_context=cv_context,
            created_at=self._clock(),
            source_profile=source_profile,
            reconciliation=reconciliation,
        )
        with self._lock:
            self._evict_expired()
            self._records[record.analysis_id] = record
            while len(self._records) > self._max_entries:
                self._records.popitem(last=False)
        return record

    def get(self, analysis_id: str) -> Optional[AnalysisRecord]:
        with self._lock:
            self._evict_expired()
            return self._records.get(analysis_id)

    def _evict_expired(self) -> None:
        now = self._clock()
        expired = [k for k, r in self._records.items() if now - r.created_at > self._ttl]
        for key in expired:
            del self._records[key]


_store = AnalysisStore()


def get_analysis_store() -> AnalysisStore:
    """FastAPI dependency. Tests override it with app.dependency_overrides."""
    return _store
