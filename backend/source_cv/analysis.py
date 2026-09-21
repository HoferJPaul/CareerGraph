"""Attach the Source CV to an analysis: snapshot the stored profile, reconcile it with the graph evidence.

Used by both analysis entry points so they cannot drift apart. Deterministic -- no model, no Neo4j
(the graph evidence is already in the CVContext). A damaged store never blocks the graph-only flow: the
analysis simply runs without a Source CV and says why.
"""
import logging
from typing import Optional

from llm.evidence_registry import build_registry
from source_cv.dates import today_index
from source_cv.errors import SourceCvError
from source_cv.reconcile import Reconciliation, reconcile
from source_cv.schema import SourceProfile
from source_cv.service import SourceCvService
from tailor_cv import CVContext

log = logging.getLogger("careergraph.api")


def snapshot_and_reconcile(
    service: SourceCvService, cv_context: CVContext
) -> tuple[Optional[SourceProfile], Optional[Reconciliation], Optional[str]]:
    """(snapshot, reconciliation, unavailable-reason). All None when there is no Source CV."""
    try:
        snapshot = service.snapshot()
    except SourceCvError as exc:
        log.warning("source_cv_unavailable code=%s", exc.code)
        return None, None, exc.code
    if snapshot is None:
        return None, None, None
    reconciliation = reconcile(snapshot, build_registry(cv_context), today_index=today_index())
    # Remember what disagreed with the graph so the review screen can show it; best effort.
    service.record_graph_conflicts(snapshot.revision, reconciliation.conflicts)
    return snapshot, reconciliation, None
