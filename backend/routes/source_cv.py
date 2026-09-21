"""The persistent Source CV: upload, review, correct, replace, delete.

    POST   /api/source-cv          multipart upload -> extract -> Groq parse -> validate -> persist
    POST   /api/source-cv/reparse  parse the stored original again (replaces the profile, incl. corrections)
    GET    /api/source-cv          the parsed profile, for review
    GET    /api/source-cv/status   exists? filename, timestamps, revision, warnings, completeness
    PUT    /api/source-cv          the user's corrections (optimistic concurrency on `expectedRevision`)
    DELETE /api/source-cv          removes the stored original AND the parsed profile

Orchestration only: every step is in source_cv/ (extraction, grounding, storage) or llm/source_profile.py
(the one model call). Responses never contain server paths, raw extracted text, excerpts, prompts or the
API key. Errors use the same {"detail": {"code", "message", "retryable"}} shape as the rest of the API.

`POST` answers with plain JSON. A client that sends `Accept: application/x-ndjson` (the web UI does)
instead receives newline-delimited progress events -- {"stage": "parsing" | "validating" | "saving"},
then {"stage": "done", "profile": ...} or {"stage": "error", "detail": ...} -- so it can show the stage the
server is really in. File-level problems (type, size, empty, encrypted, scanned) are detected BEFORE the
stream starts and come back as ordinary HTTP errors in both modes.
"""
import json
import logging
from typing import Iterator

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse

from llm.errors import LLMError
from source_cv.errors import SourceCvError, StorageFailureError
from source_cv.schema import ProfileEdit, SourceProfile, profile_view
from source_cv.service import Done, SourceCvEvent, SourceCvService, Stage
from source_cv.wiring import get_source_cv_service

router = APIRouter(prefix="/api/source-cv", tags=["source-cv"])
log = logging.getLogger("careergraph.api")

NDJSON = "application/x-ndjson"


def _response(profile: SourceProfile) -> dict:
    return {"profile": profile_view(profile)}


def _line(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _stream(events: Iterator[SourceCvEvent]) -> Iterator[bytes]:
    try:
        for event in events:
            if isinstance(event, Stage):
                yield _line({"stage": event.name})
            elif isinstance(event, Done):
                yield _line({"stage": "done", **_response(event.profile)})
    except (SourceCvError, LLMError) as exc:
        yield _line({"stage": "error", "detail": exc.to_detail()})
    except Exception as exc:  # noqa: BLE001 - the connection is already open; report a generic failure
        log.error("source_cv_stream_failed type=%s", type(exc).__name__)  # type only: messages may embed content
        yield _line({"stage": "error", "detail": StorageFailureError("The source CV could not be processed.").to_detail()})


def _reply(request: Request, service: SourceCvService, events: Iterator[SourceCvEvent]):
    if NDJSON in request.headers.get("accept", ""):
        return StreamingResponse(_stream(events), media_type=NDJSON, headers={"Cache-Control": "no-store"})
    return _response(service.run(events))


@router.get("/status")
def source_cv_status(service: SourceCvService = Depends(get_source_cv_service)) -> dict:
    return service.status()


@router.get("")
def get_source_cv(service: SourceCvService = Depends(get_source_cv_service)) -> dict:
    return _response(service.get())


@router.post("")
def upload_source_cv(
    request: Request,
    file: UploadFile = File(...),
    service: SourceCvService = Depends(get_source_cv_service),
):
    data = file.file.read(service.max_upload_bytes + 1)  # bounded read, whatever the client claims
    prepared = service.prepare(file.filename, data)
    return _reply(request, service, service.ingest_events(prepared))


@router.post("/reparse")
def reparse_source_cv(request: Request, service: SourceCvService = Depends(get_source_cv_service)):
    prepared = service.prepare_reparse()
    return _reply(request, service, service.ingest_events(prepared))


@router.put("")
def update_source_cv(edit: ProfileEdit, service: SourceCvService = Depends(get_source_cv_service)) -> dict:
    return _response(service.update(edit))


@router.delete("")
def delete_source_cv(service: SourceCvService = Depends(get_source_cv_service)) -> dict:
    return {"deleted": service.delete()}
