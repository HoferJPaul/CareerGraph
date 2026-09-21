"""The Source CV inside the analyze -> generate API flow: the server-held snapshot, the browser's inability
to inject evidence, conflict review, graceful degradation, privacy on the wire (real Groq SDK over a mocked
transport) and the untouched no-source-CV flow."""
import copy
import json

import pytest

from fixtures import build_cv_context
from helpers import FAKE_KEY, RecordingTransport, client_over, ok, settings
from llm.complete_writers import GroqCompleteWriter, GroqSupportVerifier
from llm.errors import LLMTimeoutError
from llm.factory import LLMServices
from source_fixtures import EMAIL, NAME, PHONE, valid_source_draft
from source_helpers import (
    JD, ScriptedClient, cv_pdf, complete_api, groq_parser, make_profile, make_service, valid_complete_draft,
)


def analyze(client, jd=JD):
    response = client.post("/api/jobs/analyze", json={"jobDescription": jd})
    assert response.status_code == 200, response.text
    return response.json()


def generate(client, analysis_id, **extra):
    return client.post("/api/cv/generate", json={"analysisId": analysis_id, **extra})


# ---- the snapshot ---------------------------------------------------------------------------------------------------


def test_an_analysis_snapshots_the_source_cv_and_reports_how_it_was_reconciled(tmp_path) -> None:
    with complete_api(tmp_path) as (client, service, _, _):
        analysis = analyze(client)
    info = analysis["sourceCv"]
    assert info["used"] is True and info["revision"] == 1 and info["filename"] == "cv.pdf"
    assert info["summary"] == {
        "roles": 3, "rolesWithGraphEvidence": 1, "sourceOnlyRoles": 2, "graphOnlyRoles": 0, "education": 2,
        "conflicts": 0, "unresolvedConflicts": 0, "chronologyGaps": 1,
    }
    assert [(r["employer"], r["basis"], r["treatment"]) for r in info["roles"]] == [
        ("Acme Analytics", "graph+source", None), ("Nordwind Logistics", "source_only", None), ("Kaffeehaus Ringstrasse", "source_only", None),
    ]
    assert info["chronologyGaps"] and info["conflicts"] == []
    assert NAME not in json.dumps(info) and EMAIL not in json.dumps(info)  # no contact details in the analysis view


def test_generation_uses_the_snapshot_even_after_the_stored_profile_is_replaced_edited_or_deleted(tmp_path) -> None:
    with complete_api(tmp_path) as (client, service, _, store):
        analysis = analyze(client)
        analysis_id = analysis["analysisId"]

        # 1. the stored CV is REPLACED by a different one (revision 2, a different employer)
        other = valid_source_draft()
        other["employment"][1]["employer"] = "Nordwind Logistics"
        service._parser = groq_parser(other)
        edit = client.get("/api/source-cv").json()["profile"]
        put = client.put("/api/source-cv", json={
            "expectedRevision": 1, "contact": {**edit["contact"], "email": "changed.address@example.org"},
            "employment": [{k: e[k] for k in ("id", "employer", "title", "location", "startText", "endText", "current")}
                           | {"facts": [{"id": f["id"], "text": f["text"]} for f in e["facts"]]} for e in edit["employment"]],
            "education": [], "languages": [], "certifications": [], "projects": [], "otherSections": [],
        })
        assert put.status_code == 200 and put.json()["profile"]["revision"] == 2
        client.delete("/api/source-cv")  # ...and then deleted altogether

        response = generate(client, analysis_id)
        assert response.status_code == 200
        body = response.json()

    md = body["markdown"]
    assert EMAIL in md and "changed.address@example.org" not in md  # the snapshot's contact details, not the edited ones
    assert "Handelsakademie Graz" in md and "Certified Scrum Master" in md  # education/certifications the edit had removed
    assert body["sourceCv"]["revision"] == 1 and body["generation"]["completeCv"] is True
    assert store.get(analysis_id).source_profile.revision == 1


def test_the_snapshot_is_an_independent_immutable_copy(tmp_path) -> None:
    with complete_api(tmp_path) as (client, service, _, store):
        analysis_id = analyze(client)["analysisId"]
        record = store.get(analysis_id)
        held = record.source_profile
        assert held is not service.get() and held.employment is not service.get().employment
        held_before = held.model_dump()
        edit = client.get("/api/source-cv").json()["profile"]
        client.put("/api/source-cv", json={"expectedRevision": 1, "contact": {**edit["contact"], "fullName": "Someone Else"}})
        assert store.get(analysis_id).source_profile.model_dump() == held_before  # later edits never reach it


def test_the_dev_requirements_endpoint_snapshots_too(tmp_path) -> None:
    from unittest.mock import patch

    payload = {"requirements": [{"raw": "PostgreSQL", "skillQuery": "postgresql", "importance": "required", "category": "technology", "relatedCapabilities": []}]}
    with complete_api(tmp_path) as (client, _, _, store):
        with patch("routes.requirements.build_cv_context", lambda reqs, session, root: build_cv_context()):
            body = client.post("/api/requirements/analyze", json=payload).json()
    assert store.get(body["analysisId"]).source_profile.revision == 1


# ---- the browser cannot supply evidence --------------------------------------------------------------------------------------


def test_the_browser_cannot_inject_source_evidence_a_profile_or_a_context(tmp_path) -> None:
    forged_profile = make_profile(lambda p: setattr(p.employment[1], "employer", "FABRICATED-EMPLOYER-XYZ")).model_dump(mode="json")
    forged_context = build_cv_context().model_dump()
    forged_context["evidenceStories"][0]["label"] = "FABRICATED-STORY-XYZ"
    with complete_api(tmp_path) as (client, _, scripted, _):
        analysis = client.post("/api/jobs/analyze", json={"jobDescription": JD, "sourceProfile": forged_profile, "cvContext": forged_context})
        assert analysis.status_code == 200
        response = generate(
            client, analysis.json()["analysisId"],
            sourceProfile=forged_profile, cvContext=forged_context, evidence=["source:f_forged"], employment=[{"employer": "FABRICATED-EMPLOYER-XYZ"}],
        )
        missing = generate(client, "not-an-analysis")
    assert response.status_code == 200 and missing.status_code == 404
    for forged in ("FABRICATED-EMPLOYER-XYZ", "FABRICATED-STORY-XYZ", "f_forged"):
        assert forged not in response.text and forged not in analysis.text
        assert all(forged not in call["user_content"] for call in scripted.calls)  # nor was it ever shown to the model


def test_there_is_no_endpoint_that_accepts_a_profile_for_generation() -> None:
    from main import app

    paths = json.dumps(app.openapi()["paths"])
    assert "/api/source-cv" in paths
    generate_body = json.dumps(app.openapi()["components"]["schemas"]["CvGenerateRequest"])
    assert "sourceProfile" not in generate_body and "cvContext" not in generate_body and "analysisId" in generate_body


# ---- unchanged and degraded flows -----------------------------------------------------------------------------------------------


def test_without_a_source_cv_the_graph_only_flow_is_exactly_as_before(tmp_path) -> None:
    from helpers import FakeStructuredClient
    from llm.groq_cv_writer import GroqCVWriter
    from source_helpers import ScriptedClient as _Scripted  # noqa: F401
    from fixtures import valid_cv_draft

    with complete_api(tmp_path, stored=False) as (client, service, scripted, _):
        # swap in the legacy writer's scripted draft
        from main import app
        from llm.factory import get_llm_services

        legacy = LLMServices(
            settings=settings(), extraction_provider=app.dependency_overrides[get_llm_services]().extraction_provider,
            cv_writer=GroqCVWriter(settings(), FakeStructuredClient(valid_cv_draft())),
        )
        app.dependency_overrides[get_llm_services] = lambda: legacy
        analysis = analyze(client)
        assert analysis["sourceCv"] == {
            "used": False, "revision": None, "filename": None, "unavailableReason": None, "summary": None,
            "roles": [], "conflicts": [], "chronologyGaps": [],
        }
        body = generate(client, analysis["analysisId"]).json()
    assert body["generation"]["completeCv"] is False and body["generation"]["repairAttempts"] == 0
    assert body["layoutWarnings"] == [] and body["sourceCv"] is None
    assert body["structuredCv"]["contact"] is None and body["structuredCv"]["additionalExperience"] == []
    assert body["markdown"].startswith("# Paul Hofer\n") and "## Additional Experience" not in body["markdown"]
    assert scripted.calls == []  # the source-aware writer was never involved
    assert all(e["origin"] == "graph" for p in body["provenance"] for e in p["evidence"])


def test_a_damaged_source_store_does_not_block_the_graph_only_flow(tmp_path) -> None:
    with complete_api(tmp_path) as (client, service, _, _):
        (tmp_path / "source_cv" / "profile.json").write_text("{corrupt", encoding="utf-8")
        analysis = analyze(client)
    assert analysis["sourceCv"]["used"] is False and analysis["sourceCv"]["unavailableReason"] == "storage_failure"


def test_deleting_the_source_cv_leaves_later_analyses_graph_only(tmp_path) -> None:
    with complete_api(tmp_path) as (client, _, _, store):
        client.delete("/api/source-cv")
        analysis = analyze(client)
    assert analysis["sourceCv"]["used"] is False and store.get(analysis["analysisId"]).source_profile is None


# ---- conflicts: recorded for review, resolved explicitly ----------------------------------------------------------------------------


def _with_title_conflict(service):
    profile = service.get()
    edit = {
        "expectedRevision": profile.revision,
        "contact": profile.contact.model_dump(),
        "employment": [
            {"id": e.id, "employer": e.employer, "title": "Software Engineer" if e.employer == "Acme Analytics" else e.title,
             "location": e.location, "startText": e.startText, "endText": e.endText, "current": e.current,
             "facts": [{"id": f.id, "text": f.text} for f in e.facts]}
            for e in profile.employment
        ],
        "education": [{"id": e.id, "institution": e.institution, "qualification": e.qualification, "startText": e.startText, "endText": e.endText, "facts": []} for e in profile.education],
        "languages": [{"id": lang.id, "language": lang.language, "proficiency": lang.proficiency} for lang in profile.languages],
        "certifications": [{"id": c.id, "name": c.name, "issuer": c.issuer, "dateText": c.dateText} for c in profile.certifications],
        "projects": [{"id": p.id, "name": p.name, "startText": p.startText, "endText": p.endText, "facts": [{"id": f.id, "text": f.text} for f in p.facts]} for p in profile.projects],
    }
    return edit


def test_a_disagreement_with_the_graph_is_shown_recorded_and_kept_out_of_the_cv_until_resolved(tmp_path) -> None:
    with complete_api(tmp_path) as (client, service, _, _):
        edit = _with_title_conflict(service)
        assert client.put("/api/source-cv", json=edit).status_code == 200

        analysis = analyze(client)
        (conflict,) = analysis["sourceCv"]["conflicts"]
        assert (conflict["origin"], conflict["kind"], conflict["resolution"]) == ("graph", "title", None)
        assert conflict["sourceValue"] == "Software Engineer" and conflict["graphValue"] == "Backend Engineer"
        assert analysis["sourceCv"]["roles"][0]["omittedFields"] == ["title"]

        # recorded in the stored profile, so the review screen shows it before the user generates anything
        stored = client.get("/api/source-cv").json()["profile"]
        assert [(c["id"], c["origin"]) for c in stored["conflicts"]] == [(conflict["id"], "graph")]
        assert client.get("/api/source-cv/status").json()["unresolvedConflictCount"] == 1

        first = generate(client, analysis["analysisId"]).json()
        assert "### Acme Analytics\n" in first["markdown"] and "Software Engineer" not in first["markdown"] and "Backend Engineer —" not in first["markdown"]
        assert any(w["code"] == "unresolved_conflict" for w in first["layoutWarnings"])

        # the user chooses explicitly; the choice applies from the next analysis on
        chosen = client.get("/api/source-cv").json()["profile"]
        resolve = _with_title_conflict(service) | {"expectedRevision": chosen["revision"], "conflictResolutions": {conflict["id"]: "use_graph"}}
        assert client.put("/api/source-cv", json=resolve).status_code == 200
        again = analyze(client)
        assert again["sourceCv"]["conflicts"][0]["resolution"] == "use_graph"
        second = generate(client, again["analysisId"]).json()
    assert "### Backend Engineer — Acme Analytics" in second["markdown"]
    assert not any(w["code"] == "unresolved_conflict" for w in second["layoutWarnings"])


def test_the_treatment_of_every_role_is_reported_with_the_generated_cv(tmp_path) -> None:
    with complete_api(tmp_path) as (client, _, _, _):
        body = generate(client, analyze(client)["analysisId"]).json()
    treatments = {r["employer"]: r["treatment"] for r in body["sourceCv"]["roles"]}
    assert treatments == {"Acme Analytics": "featured", "Nordwind Logistics": "additional", "Kaffeehaus Ringstrasse": "additional"}


def test_source_evidence_is_labelled_in_the_provenance_and_no_internal_ids_are_shown_there(tmp_path) -> None:
    with complete_api(tmp_path) as (client, _, _, _):
        body = generate(client, analyze(client)["analysisId"]).json()
    provenance = json.dumps(body["provenance"])
    for internal in ("source:", "graph:", "role:emp_", "emp_", "f_"):
        assert internal not in provenance
    sourced = [e for p in body["provenance"] for e in p["evidence"] if e["origin"] == "source"]
    assert [e["label"] for e in sourced] == ["Wrote onboarding documentation for new engineers."]
    assert {p["section"] for p in body["provenance"]} == {"profile", "experience", "projects"}
    assert all(e["origin"] == "graph" for p in body["provenance"] if p["section"] != "experience" or "onboarding" not in p["text"] for e in p["evidence"])


# ---- errors ------------------------------------------------------------------------------------------------------------------------


def test_complete_generation_failures_map_to_the_same_clear_codes(tmp_path) -> None:
    bad = valid_complete_draft()
    bad["roles"][0]["bullets"][0]["text"] = "Built a FastAPI service and deployed it to AWS."
    cases = [
        (ScriptedClient(write_complete_cv=LLMTimeoutError()), 504, "llm_timeout"),
        (ScriptedClient(write_complete_cv=bad, repair_cv={"fixes": []}), 502, "cv_provenance_failed"),
    ]
    for scripted, status, code in cases:
        with complete_api(tmp_path / code, scripted) as (client, _, _, _):
            response = generate(client, analyze(client)["analysisId"])
        assert response.status_code == status and response.json()["detail"]["code"] == code
    detail = response.json()["detail"]
    assert detail["repairAttempts"] == 2 and detail["retryable"] is True and "gap_claimed" in detail["violations"]
    assert "AWS" not in response.text and set(detail) == {"code", "message", "retryable", "violations", "repairAttempts"}


def test_page_budget_is_validated_and_a_target_only(tmp_path) -> None:
    with complete_api(tmp_path) as (client, _, _, _):
        analysis_id = analyze(client)["analysisId"]
        assert generate(client, analysis_id, pageBudget=0).status_code == 422
        assert generate(client, analysis_id, pageBudget=5).status_code == 422
        tiny = generate(client, analysis_id, pageBudget=1)
    assert tiny.status_code == 200  # a target, never a cut-off


def test_the_development_provider_labels_a_complete_cv_as_a_fallback(tmp_path) -> None:
    from llm.complete_writers import DeterministicCompleteWriter, VerbatimSupportVerifier
    from main import app
    from llm.factory import get_llm_services

    with complete_api(tmp_path) as (client, _, _, _):
        current = app.dependency_overrides[get_llm_services]()
        dev = LLMServices(
            settings=settings(provider="dev", api_key=None), extraction_provider=current.extraction_provider,
            cv_writer=current.cv_writer, complete_writer=DeterministicCompleteWriter(), support_verifier=VerbatimSupportVerifier(),
        )
        app.dependency_overrides[get_llm_services] = lambda: dev
        body = generate(client, analyze(client)["analysisId"]).json()
    generation = body["generation"]
    assert (generation["provider"], generation["devFallback"], generation["mode"], generation["completeCv"]) == ("dev", True, "deterministic_fallback", True)
    assert generation["model"] is None and body["markdown"].startswith("# Mira Tannenbaum")


def test_services_built_without_a_complete_writer_fail_clearly_not_with_a_crash(tmp_path) -> None:
    from helpers import FakeStructuredClient
    from llm.groq_cv_writer import GroqCVWriter
    from main import app
    from llm.factory import get_llm_services

    with complete_api(tmp_path) as (client, _, _, _):
        current = app.dependency_overrides[get_llm_services]()
        bare = LLMServices(settings=settings(), extraction_provider=current.extraction_provider, cv_writer=GroqCVWriter(settings(), FakeStructuredClient({})))
        app.dependency_overrides[get_llm_services] = lambda: bare
        response = generate(client, analyze(client)["analysisId"])
    assert response.status_code == 503 and response.json()["detail"]["code"] == "llm_not_configured"


# ---- privacy on the wire: the real Groq SDK over a mocked transport ----------------------------------------------------------------------------------


def test_contact_details_and_the_name_never_reach_groq_during_tailoring_and_the_key_stays_server_side(tmp_path) -> None:
    profile_holder: dict = {}

    def write(request):
        return ok(valid_complete_draft(profile_holder["profile"]))

    def verify(request):
        claims = json.loads(json.loads(request.content)["messages"][1]["content"])["claims"]
        return ok({"verdicts": [{"ref": c["ref"], "verdict": "supported"} for c in claims]})

    transport = RecordingTransport([write, verify])
    sdk = client_over(transport, [])
    with complete_api(tmp_path) as (client, service, _, _):
        profile_holder["profile"] = service.get()
        services_writer = GroqCompleteWriter(settings(), sdk)
        services_verifier = GroqSupportVerifier(settings(), sdk)
        from llm.factory import get_llm_services
        from main import app

        current = app.dependency_overrides[get_llm_services]()
        app.dependency_overrides[get_llm_services] = lambda: LLMServices(
            settings=settings(), extraction_provider=current.extraction_provider, cv_writer=current.cv_writer,
            complete_writer=services_writer, support_verifier=services_verifier,
        )
        body = generate(client, analyze(client)["analysisId"])
    assert body.status_code == 200

    assert [json.loads(r.content)["response_format"]["json_schema"]["name"] for r in transport.requests] == ["complete_cv_draft", "claim_support"]
    wire = "\n".join(r.content.decode("utf-8") for r in transport.requests)
    for private in (EMAIL, PHONE, NAME, "Tannenbaum", "linkedin.com", "github.com", "miratannenbaum", FAKE_KEY):
        assert private not in wire, private
    assert "Acme Analytics" in wire and "Warehouse Supervisor" in wire  # the career itself is what the model needs
    assert FAKE_KEY not in body.text
    for request in transport.requests:
        assert json.loads(request.content)["response_format"]["json_schema"]["strict"] is True
