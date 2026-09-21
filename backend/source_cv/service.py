"""The Source CV use cases: upload/replace, re-parse, review, correct, delete, snapshot for an analysis.

Ingestion is a generator of progress events so the API can report the real stage the server is in
(parsing -> validating -> saving) instead of a simulated progress bar:

    prepare(filename, data)   size + content-type + text extraction + length limit  (raises SourceCvError)
    ingest_events(prepared)   Stage("parsing"), Stage("validating"), Stage("saving"), Done(profile)

Only the very last step (the store's atomic commit) changes anything on disk, so a failed extraction,
model call or validation leaves the currently stored profile exactly as it was.

Logs carry counts and revisions only -- never document text, contact details, filenames or paths.
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator, Optional, Union

from llm.source_profile import SourceCvParser
from source_cv.errors import (
    DocumentTooLongError,
    ProfileNotFoundError,
    ProfileVersionConflictError,
    StorageFailureError,
)
from source_cv.extraction import (
    MAX_UPLOAD_BYTES,
    FileKind,
    check_size,
    detect_kind,
    extract_text,
    sanitize_filename,
)
from source_cv.grounding import build_profile
from source_cv.schema import (
    Conflict,
    ParserInfo,
    ProfileEdit,
    SourceProfile,
    UploadInfo,
    apply_edit,
    completeness,
    derive,
    now_utc,
    unresolved_graph_conflicts,
)
from source_cv.store import NewUpload, SourceCvStore

log = logging.getLogger("careergraph.source_cv")


@dataclass(frozen=True)
class Stage:
    name: str  # "parsing" | "validating" | "saving"


@dataclass(frozen=True)
class Done:
    profile: SourceProfile


SourceCvEvent = Union[Stage, Done]


@dataclass(frozen=True)
class PreparedUpload:
    text: str
    kind: FileKind
    filename: str  # sanitized, display only
    sha256: str
    size_bytes: int
    data: Optional[bytes]  # None when re-parsing the stored upload (nothing new to write)


class SourceCvService:
    def __init__(
        self,
        store: SourceCvStore,
        parser: Union[SourceCvParser, Callable[[], SourceCvParser]],
        *,
        max_text_chars: int,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        clock: Callable[[], datetime] = now_utc,
    ):
        self._store = store
        self._parser = parser  # an instance, or a callable that builds one when a parse is actually needed
        self._max_text_chars = max_text_chars
        self.max_upload_bytes = max_upload_bytes
        self._clock = clock

    # ---- ingestion ----------------------------------------------------------------------------------

    def _check_text_length(self, text: str) -> None:
        if len(text) > self._max_text_chars:
            raise DocumentTooLongError(
                f"The CV has more than {self._max_text_chars:,} characters of text, which is more than can be sent to the language model."
            )

    def prepare(self, filename: Optional[str], data: bytes) -> PreparedUpload:
        check_size(data, self.max_upload_bytes)
        kind = detect_kind(data)
        document = extract_text(data, kind)
        self._check_text_length(document.text)
        return PreparedUpload(
            text=document.text, kind=kind, filename=sanitize_filename(filename),
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), data=data,
        )

    def prepare_reparse(self) -> PreparedUpload:
        current = self._store.load()
        stored = self._store.read_upload()
        if current is None or stored is None:
            raise ProfileNotFoundError()
        document = extract_text(stored.data, stored.kind)
        self._check_text_length(document.text)
        return PreparedUpload(
            text=document.text, kind=stored.kind, filename=current.upload.originalFilename,
            sha256=current.upload.sha256, size_bytes=current.upload.sizeBytes, data=None,
        )

    def ingest_events(self, prepared: PreparedUpload) -> Iterator[SourceCvEvent]:
        try:
            current = self._store.load()
        except StorageFailureError:
            current = None  # a damaged store can only be replaced by a fresh upload (the store enforces this)
        expected = current.revision if current else None

        yield Stage("parsing")
        parser = self._parser if isinstance(self._parser, SourceCvParser) else self._parser()
        output = parser.parse(prepared.text)

        yield Stage("validating")
        now = self._clock()
        profile = build_profile(
            output.draft,
            prepared.text,
            upload=UploadInfo(
                originalFilename=prepared.filename, detectedType=prepared.kind,
                sha256=prepared.sha256, sizeBytes=prepared.size_bytes,
            ),
            parser=ParserInfo(provider=output.provider, model=output.model, mode=output.mode, attempts=output.attempts),
            revision=(expected or 0) + 1,
            now=now,
            uploaded_at=current.uploadedAt if (prepared.data is None and current) else now,
        )

        yield Stage("saving")
        upload = NewUpload(data=prepared.data, kind=prepared.kind) if prepared.data is not None else None
        self._store.commit(profile, expected_revision=expected, upload=upload)
        log.info(
            "source_cv_saved revision=%d provider=%s employment=%d education=%d warnings=%d",
            profile.revision, profile.parser.provider, len(profile.employment), len(profile.education), len(profile.warnings),
        )
        yield Done(profile)

    @staticmethod
    def run(events: Iterator[SourceCvEvent]) -> SourceProfile:
        """Consume an ingestion to its end without reporting progress."""
        for event in events:
            if isinstance(event, Done):
                return event.profile
        raise StorageFailureError()

    # ---- review / edit / delete ---------------------------------------------------------------------

    def get(self) -> SourceProfile:
        profile = self._store.load()
        if profile is None:
            raise ProfileNotFoundError()
        return profile

    def find(self) -> Optional[SourceProfile]:
        return self._store.load()

    def status(self) -> dict:
        limits = {"maxUploadBytes": self.max_upload_bytes, "acceptedTypes": ["pdf", "docx"]}
        profile = self._store.load()
        if profile is None:
            return {"exists": False, "limits": limits}
        return {
            "exists": True,
            "limits": limits,
            "filename": profile.upload.originalFilename,
            "detectedType": profile.upload.detectedType,
            "uploadedAt": profile.uploadedAt.isoformat(),
            "parsedAt": profile.parsedAt.isoformat(),
            "updatedAt": profile.updatedAt.isoformat(),
            "revision": profile.revision,
            "warningCount": len(profile.warnings),
            "conflictCount": len(profile.conflicts),
            "unresolvedConflictCount": len([c for c in profile.conflicts if c.origin == "document"])
            + len(unresolved_graph_conflicts(profile)),
            "completeness": completeness(profile),
            "parser": {
                "provider": profile.parser.provider,
                "model": profile.parser.model,
                "devParsed": profile.parser.provider != "groq",
            },
        }

    def update(self, edit: ProfileEdit) -> SourceProfile:
        current = self.get()
        if edit.expectedRevision != current.revision:
            raise ProfileVersionConflictError()
        updated = apply_edit(current, edit, self._clock())
        self._store.commit(updated, expected_revision=current.revision)
        log.info("source_cv_edited revision=%d", updated.revision)
        return updated

    def delete(self) -> bool:
        removed = self._store.delete()
        log.info("source_cv_deleted removed=%s", removed)
        return removed

    # ---- analysis integration -----------------------------------------------------------------------

    def snapshot(self) -> Optional[SourceProfile]:
        """An immutable copy of the active profile, for an analysis to hold. Later edits, replacements
        or deletion of the stored profile never reach it."""
        profile = self._store.load()
        return profile.model_copy(deep=True) if profile else None

    def record_graph_conflicts(self, revision: int, conflicts: list[Conflict]) -> None:
        """Remember the graph-vs-source disagreements an analysis found, so the review screen can show
        them. Only applied if the stored profile is still the revision the analysis used; does not bump
        the revision (no CV content changed). Best effort: never fails an analysis."""
        try:
            current = self._store.load()
            if current is None or current.revision != revision:
                return
            updated = current.model_copy(deep=True)
            updated.conflicts = [c for c in updated.conflicts if c.origin != "graph"] + [
                c.model_copy(deep=True) for c in conflicts
            ]
            if updated.conflicts == current.conflicts:
                return
            self._store.commit(derive(updated), expected_revision=revision)
        except (StorageFailureError, ProfileVersionConflictError):
            log.warning("source_cv_conflicts_not_recorded")
