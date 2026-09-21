"""API views of the Source CV's part in an analysis or a generated CV. Conversion only -- no decisions."""
from typing import Optional

from api_schemas import ConflictView, RoleTreatment, SourceCvAnalysisInfo
from source_cv.reconcile import Reconciliation
from source_cv.schema import Conflict, SourceProfile


def conflict_view(conflict: Conflict) -> ConflictView:
    return ConflictView(
        id=conflict.id, origin=conflict.origin, kind=conflict.kind, field=conflict.field,
        description=conflict.description, sourceValue=conflict.sourceValue, graphValue=conflict.graphValue,
        resolution=conflict.resolution,
    )


def source_cv_info(
    profile: Optional[SourceProfile],
    recon: Optional[Reconciliation],
    *,
    treatments: Optional[dict[str, str]] = None,
    unavailable_reason: Optional[str] = None,
) -> SourceCvAnalysisInfo:
    """`treatments` maps role id -> "featured" | "additional" once a CV has been generated."""
    if profile is None or recon is None:
        return SourceCvAnalysisInfo(used=False, unavailableReason=unavailable_reason)
    treatments = treatments or {}
    return SourceCvAnalysisInfo(
        used=True,
        revision=profile.revision,
        filename=profile.upload.originalFilename,
        summary=recon.summary(),
        roles=[
            RoleTreatment(
                title=role.header.title, employer=role.header.employer, period=role.header.period, basis=role.basis,
                treatment=treatments.get(role.role_id), omittedFields=list(role.header.omitted),
            )
            for role in recon.roles
        ],
        conflicts=[conflict_view(c) for c in [*recon.conflicts, *[c for c in profile.conflicts if c.origin == "document"]]],
        chronologyGaps=[gap.message for gap in recon.chronology_gaps],
    )
