"""Source CV upload API: the full flow (real Groq SDK over a mocked transport), refusals with explicit
error codes, persistence, atomic replacement, deletion, failed parses leaving the stored profile alone,
progress streaming, size limits, and no CV content/secrets in responses or logs."""
import copy
import io
import json
import logging
import zipfile

import pytest

from docfixtures import make_docx, make_pdf
from helpers import FAKE_KEY, PROVIDER_BODY_MARKER, FakeStructuredClient, error_response, ok, settings
from llm.errors import LLMConfigurationError, LLMTimeoutError
from llm.source_profile import GroqSourceCvParser
from source_cv.store import FileSourceCvStore
from source_fixtures import ACME_FACT_1, EMAIL, NAME, PHONE, cv_lines, valid_source_draft
from source_helpers import (
    FIXED_NOW, cv_docx, cv_pdf, dev_parser, groq_parser, make_service, source_api, transport_parser, upload,
)

STORE_DIR = "source_cv"


def _files(tmp_path) -> list[str]:
    directory = tmp_path / STORE_DIR
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


# ---- the happy path -----------------------------------------------------------------------------------------


def test_valid_pdf_is_extracted_parsed_validated_and_persisted(tmp_path) -> None:
    parser, transport = transport_parser()
    service = make_service(tmp_path, parser)
    with source_api(service) as client:
        response = upload(client, cv_pdf(), "My CV.pdf")
        assert response.status_code == 200
        profile = response.json()["profile"]

        assert profile["contact"]["fullName"] == NAME and profile["contact"]["email"] == EMAIL
        assert profile["contact"]["telephone"] == PHONE and profile["contact"]["city"] == "Graz"
        assert profile["contact"]["linkedinUrl"] == "https://linkedin.com/in/mira-tannenbaum"
        assert [e["employer"] for e in profile["employment"]] == ["Acme Analytics", "Nordwind Logistics", "Kaffeehaus Ringstrasse"]
        assert [e["institution"] for e in profile["education"]] == ["Riverbank University", "Handelsakademie Graz"]
        assert profile["languages"][0] == {"id": profile["languages"][0]["id"], "language": "German", "proficiency": "Native"}
        assert profile["certifications"][0]["name"] == "Certified Scrum Master"
        assert profile["revision"] == 1 and profile["upload"] == {"originalFilename": "My CV.pdf", "detectedType": "pdf", "sizeBytes": len(cv_pdf())}
        assert profile["parser"] == {"provider": "groq", "model": "openai/gpt-oss-120b", "mode": "llm_structured", "attempts": 1}

        # dates keep the text as written; the normalized form is derived, and never gains a month
        acme = profile["employment"][0]
        assert (acme["startText"], acme["endText"], acme["start"], acme["end"]) == ("Apr 2025", "Sep 2025", "2025-04", "2025-09")
        assert profile["education"][1]["start"] == "2010" and profile["education"][1]["end"] == "2015"

        # the request the Groq SDK actually sent: strict JSON schema, the extraction model, the document as data
        body = transport.json_bodies[0]
        assert body["model"] == "openai/gpt-oss-120b" and body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True and body["response_format"]["json_schema"]["name"] == "source_profile"
        assert NAME in body["messages"][1]["content"] and "<cv_document>" in body["messages"][1]["content"]
        assert FAKE_KEY not in json.dumps(body)

        # what the API returns is a review view: no source excerpts, no hash, no server paths
        text = response.text
        for private in ('"excerpt"', "sha256", str(tmp_path), "upload-", ".json"):
            assert private not in text
        assert client.get("/api/source-cv").json() == response.json()


def test_valid_docx_upload_works_the_same_way(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        response = upload(client, cv_docx(), "cv.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["upload"]["detectedType"] == "docx" and len(profile["employment"]) == 3


def test_type_is_decided_by_content_not_by_the_filename_or_content_type(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        as_docx = upload(client, cv_pdf(), "actually-a-pdf.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        not_a_pdf = upload(client, b"plain text pretending to be a pdf " * 30, "cv.pdf", "application/pdf")
    assert as_docx.status_code == 200 and as_docx.json()["profile"]["upload"]["detectedType"] == "pdf"
    assert not_a_pdf.status_code == 415 and not_a_pdf.json()["detail"]["code"] == "unsupported_file_type"


def test_status_reports_existence_filename_timestamps_revision_warnings_and_completeness(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        empty = client.get("/api/source-cv/status").json()
        assert empty == {"exists": False, "limits": {"maxUploadBytes": 5 * 1024 * 1024, "acceptedTypes": ["pdf", "docx"]}}
        missing = client.get("/api/source-cv")
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "source_cv_not_found"

        upload(client, cv_pdf(), "cv.pdf")
        status = client.get("/api/source-cv/status").json()
    assert status["exists"] and status["filename"] == "cv.pdf" and status["revision"] == 1
    assert status["uploadedAt"] == status["parsedAt"] == FIXED_NOW.isoformat()
    assert status["warningCount"] == 0 and status["conflictCount"] == 0
    assert status["completeness"]["isComplete"] is True and status["completeness"]["employmentCount"] == 3
    assert status["parser"] == {"provider": "groq", "model": "openai/gpt-oss-120b", "devParsed": False}


# ---- refusals, each with its own code -------------------------------------------------------------------------------


def _zip_without_word_document() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p/>")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "data,status,code",
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, 415, "unsupported_file_type"),
        (bytes([0xD0, 0xCF, 0x11, 0xE0]) + b"\x00" * 200, 415, "unsupported_file_type"),
        (_zip_without_word_document(), 415, "unsupported_file_type"),
        (b"", 422, "empty_document"),
        (make_docx(["  "]), 422, "empty_document"),
        (make_pdf([[""]], image_only=True), 422, "scanned_or_unreadable_pdf"),
        (make_pdf([cv_lines()], fake_encryption=True), 422, "encrypted_document"),
        (b"%PDF-1.4\n" + b"garbage " * 50, 422, None),
        (b"PK\x03\x04" + b"not a zip " * 40, 422, "extraction_failed"),
        (make_docx([], document_xml="<w:document><w:body><w:p>"), 422, "extraction_failed"),
    ],
)
def test_refused_files_get_a_specific_error_and_leave_nothing_behind(tmp_path, data, status, code) -> None:
    with source_api(make_service(tmp_path)) as client:
        response = upload(client, data)
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    if code:
        assert detail["code"] == code
    assert detail["message"] and detail["retryable"] is False and set(detail) == {"code", "message", "retryable"}
    assert _files(tmp_path) == []  # a refused upload stores nothing


def test_oversized_files_are_refused_by_the_service_and_by_the_body_limit(tmp_path) -> None:
    with source_api(make_service(tmp_path, max_upload_bytes=2000)) as client:
        by_service = upload(client, cv_pdf())  # a real CV PDF is larger than 2000 bytes here
    assert by_service.status_code == 413 and by_service.json()["detail"]["code"] == "file_too_large"

    # the default limit is 5 MB; the body-size middleware answers before the multipart body is parsed
    with source_api(make_service(tmp_path)) as client:
        huge = upload(client, b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 300 * 1024))
    assert huge.status_code == 413 and huge.json()["detail"]["code"] == "file_too_large"
    assert "5 MB" in huge.json()["detail"]["message"] and _files(tmp_path) == []


def test_a_file_just_under_the_limit_is_still_read(tmp_path) -> None:
    padded = cv_pdf() + b"\n%" + b"x" * (5 * 1024 * 1024 - len(cv_pdf()) - 4000)
    assert len(padded) < 5 * 1024 * 1024
    with source_api(make_service(tmp_path)) as client:
        response = upload(client, padded)
    assert response.status_code == 200


def test_uploads_declaring_no_length_are_refused_not_read_unbounded(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        response = client.post(
            "/api/source-cv", content=b"--x\r\n", headers={"Content-Type": "multipart/form-data; boundary=x", "Transfer-Encoding": "chunked"},
        )
    assert response.status_code in (411, 400, 422)


def test_a_document_with_more_text_than_the_model_limit_is_refused_before_any_model_call(tmp_path) -> None:
    parser = groq_parser()
    with source_api(make_service(tmp_path, parser, max_text_chars=200)) as client:
        response = upload(client, cv_pdf())
    assert response.status_code == 413 and response.json()["detail"]["code"] == "document_too_long"
    assert parser._client.calls == []


# ---- a failed parse never replaces the stored profile ----------------------------------------------------------------------


def _bad_drafts() -> dict[str, dict]:
    invented_employer = valid_source_draft()
    invented_employer["employment"][0]["employer"] = "Globex Corporation"  # not in the document
    unverified_entry = valid_source_draft()
    unverified_entry["employment"][1]["excerpt"] = "Chief Executive Officer - Initech, 2019 - 2024"
    invented_institution = valid_source_draft()
    invented_institution["education"][0]["institution"] = "Hogwarts"
    extra_field = valid_source_draft()
    extra_field["contact"]["nickname"] = "x"
    missing_field = valid_source_draft()
    del missing_field["languages"]
    return {
        "employer_not_in_document": invented_employer,
        "entry_excerpt_not_in_document": unverified_entry,
        "institution_not_in_document": invented_institution,
        "extra": extra_field,
        "missing": missing_field,
    }


@pytest.mark.parametrize("case", ["employer_not_in_document", "entry_excerpt_not_in_document", "institution_not_in_document", "extra", "missing"])
def test_a_rejected_parse_leaves_the_previous_profile_and_upload_untouched(tmp_path, case) -> None:
    good = make_service(tmp_path)
    with source_api(good) as client:
        first = upload(client, cv_pdf(), "first.pdf")
    assert first.status_code == 200
    before = {name: (tmp_path / STORE_DIR / name).read_bytes() for name in _files(tmp_path)}

    bad = make_service(tmp_path, groq_parser(_bad_drafts()[case]))
    with source_api(bad) as client:
        rejected = upload(client, cv_pdf(), "second.pdf")
        assert rejected.status_code == 502
        detail = rejected.json()["detail"]
        assert detail["code"] in ("llm_schema_rejected", "llm_invalid_response") and detail["retryable"] is True
        assert "Globex" not in rejected.text and "Hogwarts" not in rejected.text and "Initech" not in rejected.text
        still = client.get("/api/source-cv").json()["profile"]

    assert still["revision"] == 1 and still["upload"]["originalFilename"] == "first.pdf"
    assert {name: (tmp_path / STORE_DIR / name).read_bytes() for name in _files(tmp_path)} == before


def test_the_rejection_names_violation_codes_and_counts_only(tmp_path) -> None:
    bad = valid_source_draft()
    bad["employment"][0]["employer"] = "Globex Corporation"
    bad["employment"][1]["title"] = "Chief Executive Officer"
    with source_api(make_service(tmp_path, groq_parser(bad))) as client:
        detail = upload(client, cv_pdf()).json()["detail"]
    assert detail["code"] == "llm_schema_rejected"
    assert detail["violations"] == {"employer_not_in_document": 1, "title_not_in_document": 1}


@pytest.mark.parametrize(
    "error,status,code",
    [(LLMTimeoutError(), 504, "llm_timeout"), (LLMConfigurationError(), 503, "llm_not_configured")],
)
def test_model_failures_are_reported_with_the_existing_llm_codes_and_store_nothing(tmp_path, error, status, code) -> None:
    parser = GroqSourceCvParser(settings(), FakeStructuredClient(error=error))
    with source_api(make_service(tmp_path, parser)) as client:
        response = upload(client, cv_pdf())
    assert response.status_code == status and response.json()["detail"]["code"] == code
    assert _files(tmp_path) == []


def test_a_real_sdk_rate_limit_is_retried_within_bounds_then_surfaces_without_the_provider_body(tmp_path) -> None:
    sleeps: list[float] = []
    parser, transport = transport_parser(error_response(429), sleeps=sleeps, max_retries=2)
    with source_api(make_service(tmp_path, parser)) as client:
        response = upload(client, cv_pdf())
    assert response.status_code == 429 and response.json()["detail"]["code"] == "llm_rate_limited"
    assert len(transport.requests) == 3 and len(sleeps) == 2
    assert PROVIDER_BODY_MARKER not in response.text and _files(tmp_path) == []


def test_a_transient_failure_then_success_is_retried_transparently(tmp_path) -> None:
    parser, transport = transport_parser(error_response(503), ok(valid_source_draft()), sleeps=[])
    with source_api(make_service(tmp_path, parser)) as client:
        response = upload(client, cv_pdf())
    assert response.status_code == 200 and response.json()["profile"]["parser"]["attempts"] == 2


def test_a_document_that_yields_no_cv_content_is_refused(tmp_path) -> None:
    nothing = valid_source_draft()
    for key in ("employment", "education", "projects", "certifications", "languages"):
        nothing[key] = []
    nothing["contact"] = {k: ("" if k != "otherUrls" else []) for k in nothing["contact"]}
    nothing["summary"] = {"text": "", "excerpt": ""}
    with source_api(make_service(tmp_path, groq_parser(nothing))) as client:
        response = upload(client, cv_pdf())
    assert response.status_code == 422 and response.json()["detail"]["code"] == "no_cv_content"


# ---- no invented values ---------------------------------------------------------------------------------------------------


def test_missing_information_stays_absent_and_unverifiable_values_are_dropped_with_a_warning(tmp_path) -> None:
    draft = valid_source_draft()
    draft["contact"].update({"email": "someone.else@nowhere.example", "telephone": "+1 555 000 9999", "country": "", "githubUrl": ""})
    draft["employment"][0]["location"] = "Atlantis"
    draft["employment"][0]["facts"][0]["text"] = ACME_FACT_1.replace("60%", "90%")  # embellished number
    draft["employment"][0]["facts"].append({"text": "Led a team of 40 engineers.", "excerpt": "Led a team of 40 engineers."})
    with source_api(make_service(tmp_path, groq_parser(draft))) as client:
        profile = upload(client, cv_pdf()).json()["profile"]

    contact = profile["contact"]
    assert contact["email"] is None and contact["telephone"] is None  # not in the document -> dropped
    assert contact["country"] is None and contact["githubUrl"] is None  # "" -> absent, never a placeholder
    acme = profile["employment"][0]
    assert acme["location"] is None
    assert [f["text"] for f in acme["facts"]] == ["Wrote onboarding documentation for new engineers."]  # 90% and the invented lead are gone
    codes = {w["code"] for w in profile["warnings"]}
    assert {"value_not_in_document", "statement_unsupported", "statement_unverified"} <= codes
    strings: list[str] = []

    def collect(node) -> None:
        if isinstance(node, str):
            strings.append(node.strip().lower())
        elif isinstance(node, dict):
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(profile)
    assert not {"n/a", "unknown", "tbd", "null", "none", "", "-"} & set(strings)  # absent means null, never a placeholder


def test_model_reported_ambiguities_become_visible_warnings(tmp_path) -> None:
    draft = valid_source_draft()
    draft["warnings"] = [{"code": "ambiguous_date", "message": "One date could be read two ways."}]
    with source_api(make_service(tmp_path, groq_parser(draft))) as client:
        profile = upload(client, cv_pdf()).json()["profile"]
    assert profile["warnings"] == [{"code": "ambiguous_date", "message": "One date could be read two ways.", "entryId": None, "origin": "parser"}]


# ---- persistence, replacement, deletion ------------------------------------------------------------------------------------------------


def test_the_profile_survives_an_application_restart(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        first = upload(client, cv_pdf(), "cv.pdf").json()
    restarted = make_service(tmp_path)  # a brand-new store + service over the same directory
    with source_api(restarted) as client:
        again = client.get("/api/source-cv").json()
        status = client.get("/api/source-cv/status").json()
    assert again == first and status["exists"] and status["revision"] == 1
    assert restarted.snapshot().employment[0].employer == "Acme Analytics"


def test_replacing_swaps_the_profile_and_the_original_atomically(tmp_path) -> None:
    second_draft = valid_source_draft()
    second_draft["employment"] = second_draft["employment"][:1]
    second_draft["education"] = second_draft["education"][:1]
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf(), "old.pdf")
        old_files = _files(tmp_path)
        assert len(old_files) == 2 and "profile.json" in old_files
    with source_api(make_service(tmp_path, groq_parser(second_draft))) as client:
        replaced = upload(client, cv_docx(), "new.docx").json()["profile"]
    new_files = _files(tmp_path)
    assert replaced["revision"] == 2 and replaced["upload"]["originalFilename"] == "new.docx"
    assert len(replaced["employment"]) == 1 and len(replaced["education"]) == 1
    assert len(new_files) == 2 and new_files != old_files  # exactly one profile and ONE original: the old one is gone
    assert sum(name.endswith(".docx") for name in new_files) == 1 and not any(name.endswith(".pdf") for name in new_files)


def test_a_crash_while_writing_the_new_profile_leaves_the_old_state_intact(tmp_path, monkeypatch) -> None:
    service = make_service(tmp_path)
    with source_api(service) as client:
        upload(client, cv_pdf(), "old.pdf")
        before = {name: (tmp_path / STORE_DIR / name).read_bytes() for name in _files(tmp_path)}

        real_replace = __import__("os").replace

        def fail_on_manifest(src, dst):
            if str(dst).endswith("profile.json"):
                raise OSError("disk full")
            return real_replace(src, dst)

        monkeypatch.setattr("source_cv.store.os.replace", fail_on_manifest)
        failed = upload(client, cv_docx(), "new.docx")
        monkeypatch.undo()

        assert failed.status_code == 500 and failed.json()["detail"]["code"] == "storage_failure"
        assert str(tmp_path) not in failed.text and "disk full" not in failed.text
        assert {name: (tmp_path / STORE_DIR / name).read_bytes() for name in _files(tmp_path)} == before  # no torn or orphaned files
        assert client.get("/api/source-cv").json()["profile"]["upload"]["originalFilename"] == "old.pdf"


def test_no_temporary_files_are_left_behind(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        client.put("/api/source-cv", json={"expectedRevision": 1, **_edit_from(client)})
    assert not [name for name in _files(tmp_path) if name.startswith(".tmp")]


def test_orphaned_files_from_an_interrupted_write_are_cleaned_up_on_the_next_commit(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        (tmp_path / STORE_DIR / "upload-deadbeefdeadbeef.pdf").write_bytes(b"orphan")
        (tmp_path / STORE_DIR / ".tmp-abc123").write_bytes(b"partial")
        upload(client, cv_pdf())
    assert len(_files(tmp_path)) == 2


def test_delete_removes_both_the_original_and_the_parsed_profile(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        assert len(_files(tmp_path)) == 2
        deleted = client.delete("/api/source-cv")
        assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
        assert _files(tmp_path) == []
        assert client.get("/api/source-cv").status_code == 404
        assert client.get("/api/source-cv/status").json()["exists"] is False
        assert client.delete("/api/source-cv").json() == {"deleted": False}  # idempotent


def test_only_server_generated_names_and_sanitised_metadata_are_used_for_storage(tmp_path) -> None:
    nasty = "../../../etc/passwd" + chr(0) + "\\..\\evil.pdf"
    with source_api(make_service(tmp_path)) as client:
        response = upload(client, cv_pdf(), nasty)
    assert response.status_code == 200
    filename = response.json()["profile"]["upload"]["originalFilename"]
    assert "/" not in filename and "\\" not in filename and ".." not in filename and chr(0) not in filename
    stored = _files(tmp_path)
    assert sorted(stored)[0].startswith("profile") or sorted(stored)[0].startswith("upload-")
    assert all(name == "profile.json" or (name.startswith("upload-") and name.endswith(".pdf")) for name in stored)
    assert not (tmp_path.parent / "passwd").exists() and not list(tmp_path.glob("**/evil*"))


def test_a_tampered_manifest_cannot_point_the_store_at_another_path(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        manifest = tmp_path / STORE_DIR / "profile.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["uploadFile"] = "../../outside.pdf"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        response = client.get("/api/source-cv")
        assert response.status_code == 500 and response.json()["detail"]["code"] == "storage_failure"
        assert client.delete("/api/source-cv").status_code == 200  # a damaged store can always be cleared
        assert upload(client, cv_pdf()).status_code == 200  # ...and a fresh upload replaces a damaged one


def test_a_corrupt_profile_file_is_a_storage_failure_that_a_fresh_upload_repairs(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        (tmp_path / STORE_DIR / "profile.json").write_text("{not json", encoding="utf-8")
        assert client.get("/api/source-cv/status").status_code == 500
        repaired = upload(client, cv_docx(), "fresh.docx")
        assert repaired.status_code == 200 and repaired.json()["profile"]["revision"] == 1


def test_a_swapped_original_is_detected_by_its_hash(tmp_path) -> None:
    service = make_service(tmp_path)
    with source_api(service) as client:
        upload(client, cv_pdf())
        original = next(p for p in (tmp_path / STORE_DIR).iterdir() if p.name.startswith("upload-"))
        original.write_bytes(cv_pdf() + b"tampered")
        response = client.post("/api/source-cv/reparse")
    assert response.status_code == 500 and response.json()["detail"]["code"] == "storage_failure"


# ---- re-parse ---------------------------------------------------------------------------------------------------------------------------------


def test_reparse_reads_the_stored_original_again_and_bumps_the_revision(tmp_path) -> None:
    parser, transport = transport_parser()
    with source_api(make_service(tmp_path, parser)) as client:
        first = upload(client, cv_pdf(), "cv.pdf").json()["profile"]
        again = client.post("/api/source-cv/reparse")
    assert again.status_code == 200
    profile = again.json()["profile"]
    assert profile["revision"] == 2 and profile["upload"]["originalFilename"] == "cv.pdf"
    assert [e["id"] for e in profile["employment"]] == [e["id"] for e in first["employment"]]  # content-derived ids are stable
    assert len(transport.requests) == 2 and len(_files(tmp_path)) == 2


def test_reparse_without_a_stored_cv_is_a_clean_404(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        response = client.post("/api/source-cv/reparse")
    assert response.status_code == 404 and response.json()["detail"]["code"] == "source_cv_not_found"


# ---- progress streaming ---------------------------------------------------------------------------------------------------------------------


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_streaming_reports_the_real_server_stages_then_the_profile(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        response = upload(client, cv_pdf(), headers={"Accept": "application/x-ndjson"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/x-ndjson")
    events = _events(response)
    assert [e["stage"] for e in events] == ["parsing", "validating", "saving", "done"]
    assert events[-1]["profile"]["contact"]["fullName"] == NAME and "excerpt" not in response.text


def test_streaming_reports_a_late_failure_as_an_error_event_and_stores_nothing(tmp_path) -> None:
    parser = GroqSourceCvParser(settings(), FakeStructuredClient(error=LLMTimeoutError()))
    with source_api(make_service(tmp_path, parser)) as client:
        response = upload(client, cv_pdf(), headers={"Accept": "application/x-ndjson"})
    events = _events(response)
    assert [e["stage"] for e in events] == ["parsing", "error"]
    assert events[-1]["detail"]["code"] == "llm_timeout" and events[-1]["detail"]["retryable"] is True
    assert _files(tmp_path) == []


def test_file_problems_are_ordinary_http_errors_even_when_streaming(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        response = upload(client, b"nope" * 100, headers={"Accept": "application/x-ndjson"})
    assert response.status_code == 415 and response.json()["detail"]["code"] == "unsupported_file_type"


# ---- development provider -----------------------------------------------------------------------------------------------------------------------


def test_dev_provider_parses_with_the_labelled_heuristic_parser(tmp_path) -> None:
    with source_api(make_service(tmp_path, dev_parser())) as client:
        response = upload(client, cv_pdf())
        status = client.get("/api/source-cv/status").json()
    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["parser"] == {"provider": "dev", "model": None, "mode": "heuristic_dev", "attempts": 1}
    assert status["parser"]["devParsed"] is True
    assert [e["employer"] for e in profile["employment"]] == ["Acme Analytics", "Nordwind Logistics", "Kaffeehaus Ringstrasse"]
    assert profile["contact"]["email"] == EMAIL and len(profile["languages"]) == 2


def test_dev_parser_output_goes_through_the_same_grounding_checks(tmp_path) -> None:
    from llm.source_profile import DevSourceCvParser, ParseOutput
    from source_fixtures import valid_draft

    class Hallucinating(DevSourceCvParser):
        def parse(self, text):
            draft = valid_draft()
            draft.employment[0].employer = "Invented Ltd"
            return ParseOutput(draft=draft, provider="dev", model=None, mode="heuristic_dev")

    with source_api(make_service(tmp_path, Hallucinating())) as client:
        response = upload(client, cv_pdf())
    assert response.status_code == 502 and response.json()["detail"]["code"] == "llm_schema_rejected"


# ---- no CV content or secrets in logs or errors ------------------------------------------------------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_no_cv_content_contact_details_or_secrets_reach_the_logs_or_error_bodies(tmp_path) -> None:
    handler = _Collect()
    logger = logging.getLogger("careergraph")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    bodies: list[str] = []
    try:
        parser, _ = transport_parser(error_response(429), ok(valid_source_draft()), sleeps=[])
        with source_api(make_service(tmp_path, parser)) as client:
            bodies.append(upload(client, cv_pdf(), f"{NAME}.pdf").text)
            bodies.append(upload(client, b"garbage " * 100, f"{NAME}.pdf").text)
            bodies.append(upload(client, make_pdf([cv_lines()], fake_encryption=True), f"{NAME}.pdf").text)
            client.put("/api/source-cv", json={"expectedRevision": 99})
            client.delete("/api/source-cv")
        bad = valid_source_draft()
        bad["employment"][0]["employer"] = "Globex Corporation"
        with source_api(make_service(tmp_path, groq_parser(bad))) as client:
            bodies.append(upload(client, cv_pdf()).text)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    log_text = "\n".join(handler.lines)
    assert "llm_call" in log_text and "source_cv_saved" in log_text
    private = [
        NAME, "Tannenbaum", EMAIL, PHONE, "Acme Analytics", "Nordwind", "Kaffeehaus", "Riverbank", "graz", "Globex", "example.org",
        ACME_FACT_1[:30], FAKE_KEY, "linkedin.com/in", str(tmp_path),
    ]
    for needle in private:
        assert needle.lower() not in log_text.lower(), f"leaked into logs: {needle[:24]!r}"
    for body in bodies[1:]:
        for needle in (NAME, EMAIL, PHONE, "Acme Analytics", "Globex", FAKE_KEY, str(tmp_path)):
            assert needle not in body


# ---- correcting the profile (PUT) ---------------------------------------------------------------------------------------------------------------------


def _edit_from(client) -> dict:
    """The editable form of the stored profile, as the review screen would send it back."""
    profile = client.get("/api/source-cv").json()["profile"]

    def facts(items):
        return [{"id": f["id"], "text": f["text"]} for f in items]

    return {
        "contact": profile["contact"],
        "summary": profile["summary"]["text"] if profile["summary"] else None,
        "employment": [
            {k: e[k] for k in ("id", "employer", "title", "location", "startText", "endText", "current")} | {"facts": facts(e["facts"])}
            for e in profile["employment"]
        ],
        "education": [
            {k: e[k] for k in ("id", "institution", "qualification", "field", "location", "startText", "endText", "current")} | {"facts": facts(e["facts"])}
            for e in profile["education"]
        ],
        "projects": [
            {k: e[k] for k in ("id", "name", "role", "url", "startText", "endText", "current")} | {"facts": facts(e["facts"])}
            for e in profile["projects"]
        ],
        "certifications": [{k: c[k] for k in ("id", "name", "issuer", "dateText")} for c in profile["certifications"]],
        "languages": [{k: lang[k] for k in ("id", "language", "proficiency")} for lang in profile["languages"]],
        "otherSections": [],
        "conflictResolutions": {},
    }


def test_corrections_are_saved_with_a_new_revision_and_keep_ids_and_excerpts(tmp_path) -> None:
    service = make_service(tmp_path)
    with source_api(service) as client:
        original = upload(client, cv_pdf()).json()["profile"]
        edit = _edit_from(client)
        edit["contact"]["telephone"] = "+43 660 555 9999"
        edit["employment"][1]["title"] = "Warehouse Team Lead"
        edit["employment"][0]["facts"][1]["text"] = "Wrote onboarding guides for new engineers."
        edit["employment"][0]["facts"].append({"id": None, "text": "Mentored two interns."})
        edit["education"] = edit["education"][:1]  # the user removes an entry
        edit["languages"].append({"id": None, "language": "French", "proficiency": "B1"})
        response = client.put("/api/source-cv", json={"expectedRevision": 1, **edit})

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["revision"] == 2 and profile["contact"]["telephone"] == "+43 660 555 9999"
    assert profile["employment"][1]["title"] == "Warehouse Team Lead"
    assert [e["id"] for e in profile["employment"]] == [e["id"] for e in original["employment"]]  # ids never change on edit
    facts = profile["employment"][0]["facts"]
    assert facts[1]["text"] == "Wrote onboarding guides for new engineers." and facts[1]["userEdited"] is True
    assert facts[0]["userEdited"] is False and facts[2]["userEdited"] is True and facts[2]["id"] not in {f["id"] for f in original["employment"][0]["facts"]}
    assert len(profile["education"]) == 1 and profile["languages"][-1]["language"] == "French"

    stored = service.get()  # the source excerpts are kept privately, for provenance
    assert stored.employment[0].facts[1].excerpt == "Wrote onboarding documentation for new engineers."
    assert stored.employment[0].facts[2].excerpt is None and stored.employment[0].excerpt


def test_editing_dates_rederives_the_normalized_form_without_inventing_precision(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        edit = _edit_from(client)
        edit["employment"][0].update({"startText": "2025", "endText": "Present", "current": False})
        edit["employment"][1].update({"startText": "Summer 2018", "endText": "sometime later"})
        profile = client.put("/api/source-cv", json={"expectedRevision": 1, **edit}).json()["profile"]
    acme, nordwind = profile["employment"][0], profile["employment"][1]
    assert (acme["start"], acme["end"], acme["current"]) == ("2025", None, True)  # a year stays a year
    assert (nordwind["start"], nordwind["end"]) == ("2018", None)
    assert any(w["code"] == "date_unparsed" and w["origin"] == "derived" and w["entryId"] == nordwind["id"] for w in profile["warnings"])


def test_a_stale_revision_is_a_conflict_and_changes_nothing(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        edit = _edit_from(client)
        client.put("/api/source-cv", json={"expectedRevision": 1, **edit})  # revision is now 2
        stale = client.put("/api/source-cv", json={"expectedRevision": 1, **edit})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "profile_version_conflict"
        assert client.get("/api/source-cv/status").json()["revision"] == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e["contact"].update(email="not-an-email"),
        lambda e: e["contact"].update(linkedinUrl="javascript:alert(1)"),
        lambda e: e["contact"].update(githubUrl="ftp://example.org/x"),
        lambda e: e["contact"].update(fullName="x" * 400),
        lambda e: e["employment"][0].update(employer=None, title=None),
        lambda e: e["employment"][0].update(id="emp_doesnotexist"),
        lambda e: e["employment"][0]["facts"][0].update(id="f_doesnotexist"),
        lambda e: e["employment"].append({**e["employment"][0]}),  # the same id twice
        lambda e: e["education"][0].update(institution="   "),
        lambda e: e.update(conflictResolutions={"cnf_unknown": "use_graph"}),
        lambda e: e["employment"].extend([{"id": None, "employer": f"E{i}", "title": "T", "facts": []} for i in range(50)]),
    ],
)
def test_invalid_corrections_are_rejected_with_a_clear_code_and_nothing_is_saved(tmp_path, mutate) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        edit = _edit_from(client)
        mutate(edit)
        response = client.put("/api/source-cv", json={"expectedRevision": 1, **edit})
        assert response.status_code == 422
        assert client.get("/api/source-cv/status").json()["revision"] == 1


def test_unknown_fields_in_a_correction_are_rejected(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        edit = _edit_from(client)
        edit["employment"][0]["excerpt"] = "attempt to overwrite provenance"
        assert client.put("/api/source-cv", json={"expectedRevision": 1, **edit}).status_code == 422


def test_a_bare_web_address_gets_a_scheme_but_other_schemes_never_pass(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        edit = _edit_from(client)
        edit["contact"]["portfolioUrl"] = "miratannenbaum.example.org/work"
        profile = client.put("/api/source-cv", json={"expectedRevision": 1, **edit}).json()["profile"]
    assert profile["contact"]["portfolioUrl"] == "https://miratannenbaum.example.org/work"


# ---- ids are stable, unique, and content-derived --------------------------------------------------------------------------------------------------------------


def test_source_ids_are_unique_prefixed_and_reproducible(tmp_path) -> None:
    with source_api(make_service(tmp_path / "a")) as client:
        first = upload(client, cv_pdf()).json()["profile"]
    with source_api(make_service(tmp_path / "b")) as client:
        second = upload(client, cv_pdf()).json()["profile"]

    def ids(profile):
        out = [profile["summary"]["id"]]
        for group in ("employment", "education", "projects"):
            for entry in profile[group]:
                out.append(entry["id"])
                out.extend(f["id"] for f in entry["facts"])
        out.extend(c["id"] for c in profile["certifications"])
        out.extend(lang["id"] for lang in profile["languages"])
        return out

    assert ids(first) == ids(second)  # same document -> same ids
    assert len(ids(first)) == len(set(ids(first)))
    assert all(i.split("_")[0] in {"sum", "emp", "edu", "prj", "f", "crt", "lng"} and len(i.split("_")[1]) >= 8 for i in ids(first))


def test_two_identical_statements_still_get_distinct_ids() -> None:
    from source_cv.schema import IdAllocator

    allocator = IdAllocator()
    a, b = allocator.allocate("f", "owner", "same text"), allocator.allocate("f", "owner", "same text")
    assert a != b and a.startswith("f_") and b.startswith("f_")


# ---- the body-size middleware does not interfere with anything else -----------------------------------------------------------------------------------------------------


def test_other_endpoints_are_not_subject_to_the_source_cv_body_limit(tmp_path) -> None:
    from fastapi.testclient import TestClient
    from main import app

    with source_api(make_service(tmp_path)):
        response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert copy.deepcopy(response.json()) == {"status": "ok"}


# ---- no key configured: only parsing needs the model ------------------------------------------------------------------------------------


def test_reviewing_editing_and_deleting_work_without_a_configured_key_and_only_parsing_reports_it(tmp_path) -> None:
    import os
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from llm import factory
    from main import app
    from source_cv import wiring

    seeded = make_service(tmp_path)  # a CV stored earlier, while a key was available
    seeded.run(seeded.ingest_events(seeded.prepare("cv.pdf", cv_pdf())))

    env = {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "", "CAREERGRAPH_PRIVATE_DIR": str(tmp_path)}
    with patch.dict(os.environ, env):
        factory._default_services.cache_clear()
        wiring._default_service.cache_clear()
        try:
            client = TestClient(app)
            assert client.get("/api/source-cv/status").json()["exists"] is True
            profile = client.get("/api/source-cv").json()["profile"]
            assert client.put("/api/source-cv", json={"expectedRevision": profile["revision"], "contact": profile["contact"]}).status_code == 200
            blocked = upload(client, cv_docx())
            assert blocked.status_code == 503 and blocked.json()["detail"]["code"] == "llm_not_configured"
            assert client.get("/api/source-cv").json()["profile"]["revision"] == 2  # the failed upload changed nothing
            assert client.delete("/api/source-cv").json() == {"deleted": True}
        finally:
            factory._default_services.cache_clear()
            wiring._default_service.cache_clear()
