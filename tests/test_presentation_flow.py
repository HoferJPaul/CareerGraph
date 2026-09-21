"""Structural regression tests for the autonomous-flow frontend contract:

  - The primary flow is Job -> Match -> CV inside the app. There is NO manual Claude hand-off
    anywhere: no prompt to copy, no requirements.json to upload, no response to paste back, no
    instruction to leave the application.
  - The Job page only ever calls the server-side analyze endpoint; the Match page only ever
    generates the CV from the server-side analysisId (the browser never sends evidence).
  - Development-fallback output is labelled, provider/model appear only in a collapsed
    technical-details section, and no API key / system prompt exists in frontend code.
  - The graph explorer, the match-review stage and cv_evidence.py's optional layer survive.

No JS test framework is configured for this project (see frontend/package.json), so these are
plain text/structural checks against the .tsx/.ts source -- the same static-guard style used by
backend/test_api.py's write-Cypher AST scan, adapted to source text since these are TypeScript.

Runs offline (no Neo4j, no Groq):
    python tests/test_presentation_flow.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend" / "src"


def _source(*parts: str) -> str:
    return (FRONTEND_SRC.joinpath(*parts)).read_text(encoding="utf-8")


def _all_frontend_source() -> dict[str, str]:
    return {
        p.relative_to(FRONTEND_SRC).as_posix(): p.read_text(encoding="utf-8")
        for p in FRONTEND_SRC.rglob("*")
        if p.is_file() and p.suffix in {".ts", ".tsx", ".css"}
    }


def _function_body(source: str, fn_name: str) -> str:
    """The body of `function fn_name` / `async function fn_name`, found by brace matching (so
    JSX and helpers that follow the function are not swept in)."""
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(fn_name)}\b", source)
    assert match, f"could not find function {fn_name!r} in source"
    start = source.index("{", match.end())
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces in {fn_name!r}")


def test_primary_flow_contains_no_manual_claude_handoff() -> None:
    forbidden = [
        "Generate Claude Prompt", "Copy for Claude", "Upload Claude", "Copy CV Prompt for Claude",
        "Paste this into Claude", "hand it to Claude", "Hand it to Claude", "requirements.json",
        "cv_context.json", "buildExtractionPrompt", "buildCvPrompt", "Upload .json", "Validate & Match",
    ]
    for name, text in _all_frontend_source().items():
        assert "claude" not in text.lower(), f"{name} mentions Claude -- no manual hand-off may remain"
        for phrase in forbidden:
            assert phrase not in text, f"{name} still contains {phrase!r}"
        assert "navigator.clipboard" not in text or name == "pages/CvPreview.tsx", (
            f"{name}: clipboard use is only for copying the finished CV, never a prompt"
        )


def test_obsolete_manual_handoff_code_is_removed_not_left_dead() -> None:
    for gone in ("prompts.ts", "pages/RequirementsUpload.tsx", "pages/CvContext.tsx"):
        assert not (FRONTEND_SRC / gone).exists(), f"{gone} is an unused manual-hand-off path and must be deleted"
    client = _source("api", "client.ts")
    assert "analyzeRequirements" not in client, "the requirements-upload call is no longer part of the UI"


def test_navigation_is_job_match_cv_and_the_graph_explorer_remains() -> None:
    app = _source("App.tsx")
    for label in ('"CareerGraph"', '"1 Job"', '"2 Match"', '"3 CV"'):
        assert f"label={label}" in app, f"nav label {label} missing"
    for route in ('path="/"', 'path="/new-cv"', 'path="/match-review"', 'path="/cv"'):
        assert route in app
    for gone in ("/requirements", "/cv-context", "/cv-preview", 'label="Debug"', '"2 Claude"', '"4 Claude"'):
        assert gone not in app, f"{gone} belongs to the old manual flow"
    steps = _source("components", "StepIndicator.tsx")
    assert '"Job"' in steps and '"Match"' in steps and '"CV"' in steps and "Claude" not in steps
    assert 'label: "Neo4j"' not in steps


def test_job_page_analyzes_server_side_with_progress_and_duplicate_protection() -> None:
    source = _source("pages", "NewCv.tsx")
    assert "Analyze job" in source
    handler = _function_body(source, "handleAnalyze")
    assert "api.analyzeJob" in handler
    assert "inFlight.current" in handler, "a ref guard must stop duplicate submissions"
    assert "disabled={analyzing" in source, "the button must be disabled while the request runs"
    assert "<Progress" in source and "ErrorNotice" in source
    assert "describeApiError" in handler, "errors are shown through the human-readable formatter"


def test_match_page_keeps_review_and_generates_the_cv_from_the_server_side_analysis() -> None:
    source = _source("pages", "MatchReview.tsx")
    for section in ('title: "Matched"', 'title: "Transferable"', 'title: "Gaps"'):
        assert section in source, "the match-review stage must be preserved"
    assert "Generate evidence-backed CV" in source
    handler = _function_body(source, "handleGenerate")
    assert "api.generateCv(analysis.analysisId" in handler, "only the analysisId is sent, never evidence"
    assert "inFlight.current" in handler
    assert "cvContext" not in handler


def test_cv_generation_request_never_carries_evidence_from_the_browser() -> None:
    client = _source("api", "client.ts")
    generate = client[client.index("generateCv:"):]
    assert "analysisId" in generate and "cvContext" not in generate.split("};")[0]
    cv_page = _source("pages", "CvPreview.tsx")
    assert "api.generateCv(analysis.analysisId" in cv_page and "JSON.stringify(cvContext" not in cv_page


def test_fallback_output_is_labelled_and_provider_details_are_collapsed() -> None:
    details = _source("components", "TechnicalDetails.tsx")
    assert "Development fallback" in details and "<details" in details and "Technical details" in details
    for page in ("NewCv.tsx", "MatchReview.tsx", "CvPreview.tsx"):
        assert "FallbackBanner" in _source("pages", page), f"{page} must label dev-fallback output"
    # Provider / model only ever render inside the collapsed technical-details component.
    for page in ("NewCv.tsx", "MatchReview.tsx", "CvPreview.tsx"):
        text = _source("pages", page)
        assert ".model" not in text and "extractionModel" not in text and "writingModel" not in text, page


def test_provenance_is_inspectable_and_never_shows_internal_ids() -> None:
    panel = _source("components", "ProvenancePanel.tsx")
    assert "<details" in panel and "Transferable" in panel and "does <strong>not</strong>" in panel
    assert "evidenceIds" not in panel and "evidenceId" not in panel


def test_no_key_prompt_or_provider_error_object_exists_in_frontend_code() -> None:
    for name, text in _all_frontend_source().items():
        for needle in ("GROQ", "gsk_", "api.groq.com", "system prompt", "You are the ", "Authorization"):
            assert needle not in text, f"{name} contains {needle!r}"
        assert "dangerouslySetInnerHTML" not in text
    client = _source("api", "client.ts")
    assert "JSON.stringify(err" not in client, "provider/error objects are never dumped into the UI"


def test_cv_evidence_kept_as_optional_layer_not_deleted() -> None:
    assert (ROOT / "pipeline" / "cv_evidence.py").exists()
    assert (ROOT / "tests" / "test_cv_evidence.py").exists()
    # Not wired into the frontend flow's API client.
    assert "cv_evidence" not in _source("api", "client.ts").lower()


def test_deterministic_writer_and_renderer_still_exist_as_labelled_dev_fallback() -> None:
    assert (ROOT / "pipeline" / "cv_writer.py").exists()
    assert (ROOT / "backend" / "routes" / "cv.py").exists()
    assert (ROOT / "backend" / "cv_markdown.py").exists()


# ---- Source CV ---------------------------------------------------------------------------------------------


def test_source_cv_card_sits_above_the_job_description_input_and_explains_itself() -> None:
    page = _source("pages", "NewCv.tsx")
    assert page.index("<SourceCvCard") < page.index('id="jd"'), "the Source CV card belongs above the job-description input"
    card = _source("components", "SourceCvCard.tsx")
    for phrase in (
        "complete base CV", "contact details", "education", "employment history",  # what it is for
        "Drag and drop", "Choose a file", "PDF", "DOCX",  # both ways to add a file
        "sent to the AI provider",  # the processing disclosure
        "Delete source CV", "Replace CV", "Review and correct",
    ):
        assert phrase in card, f"SourceCvCard is missing {phrase!r}"
    assert "maxUploadBytes" in card, "the size limit shown comes from the server, not a hard-coded number"


def test_first_page_still_allows_analysis_without_a_source_cv_and_says_the_result_is_incomplete() -> None:
    page = _source("pages", "NewCv.tsx")
    assert "Without a source CV the generated CV may be incomplete" in page and "complete CV" in page
    handler = _function_body(page, "handleAnalyze")
    assert "sourceCv" not in handler, "analysis must not depend on a source CV being present"
    assert "disabled={analyzing || !jobDescription.trim() || tooLong}" in page


def test_source_cv_processing_shows_every_stage_prevents_duplicates_and_offers_retry() -> None:
    card = _source("components", "SourceCvCard.tsx")
    for stage in ('"uploading"', '"extracting"', '"parsing"', '"validating"', '"saving"'):
        assert stage in card
    for label in ("Uploading the file", "Reading the text", "Parsing it", "Checking every value", "Saving it privately"):
        assert label in card
    run = _function_body(card, "run")
    assert "inFlight.current" in run, "a ref guard must stop duplicate submissions"
    assert "ErrorNotice" in card and "retryLabel" in card and "canRetry" in card
    client = _source("api", "client.ts")
    assert "application/x-ndjson" in client and "xhr.upload.onload" in client, "stages come from the server, not a timer"
    assert "setInterval" not in card and "setTimeout" not in card, "no simulated progress"


def test_the_destructive_delete_is_separated_and_confirmed() -> None:
    card = _source("components", "SourceCvCard.tsx")
    assert 'className="danger-zone"' in card and "confirmingDelete" in card
    assert "Yes, delete source CV" in card and "alertdialog" in card
    assert card.index("danger-zone") > card.index("Replace CV"), "delete is not next to the everyday actions"


def test_source_cv_ui_never_displays_internal_ids_or_the_key() -> None:
    for name in ("SourceCvCard.tsx", "SourceCvEditor.tsx", "SourceCvAnalysisPanel.tsx", "ProvenancePanel.tsx"):
        text = _source("components", name)
        assert not re.search(r"(?<!key=)\{\s*[\w.?]+\.(?:id|entryId|evidenceIds?)\s*\}", text), f"{name} renders an internal id"
        assert "sha256" not in text and "excerpt" not in text, f"{name} touches server-only provenance fields"
    types = _source("types", "career.ts")
    view = types[types.index("export interface SourceProfileView") : types.index("export interface SourceCvStatus")]
    assert "excerpt" not in view and "sha256" not in view


def test_source_cv_calls_go_through_the_api_client_and_generation_still_sends_only_an_analysis_id() -> None:
    client = _source("api", "client.ts")
    for path in ('"/api/source-cv"', '"/api/source-cv/status"', '"/api/source-cv/reparse"'):
        assert path in client
    for verb in ("method: \"PUT\"", "method: \"DELETE\""):
        assert verb in client
    generate = client[client.index("generateCv:") : client.index("// ---- Source CV")]
    assert "analysisId" in generate and "pageBudget" in generate
    for forbidden in ("sourceProfile", "cvContext", "evidence", "employment"):
        assert forbidden not in generate, f"generation must not send {forbidden}"
    for page in ("MatchReview.tsx", "CvPreview.tsx"):
        assert "sourceProfile" not in _source("pages", page) and "toEdit" not in _source("pages", page)


def test_match_and_cv_pages_show_how_the_source_cv_was_used_and_when_it_is_stale() -> None:
    panel = _source("components", "SourceCvAnalysisPanel.tsx")
    assert "changed after this analysis" in panel and "deleted after this analysis" in panel
    assert "No source CV was used" in panel and "Possible gaps in your timeline" in panel
    assert "Additional experience" in panel and "Left out until resolved" in panel
    assert "SourceCvAnalysisPanel" in _source("pages", "MatchReview.tsx")
    cv_page = _source("pages", "CvPreview.tsx")
    assert "SourceCvAnalysisPanel" in cv_page and "layoutWarnings" in cv_page and "pageBudget" in cv_page
    assert "never cut" in cv_page or "nothing is ever cut" in cv_page.lower()


def test_provenance_labels_source_evidence_apart_from_graph_evidence() -> None:
    panel = _source("components", "ProvenancePanel.tsx")
    assert 'e.origin === "source"' in panel and "Your CV" in panel and "Additional experience" in panel


def test_rejected_cvs_explain_which_rules_failed_using_codes_only() -> None:
    notice = _source("components", "ErrorNotice.tsx")
    for code in ("gap_claimed", "unsupported_source_claim", "unsupported_number", "unknown_evidence_id"):
        assert code in notice
    assert "repairAttempts" in notice and "automatic repair" in notice


def test_processing_disclosure_names_the_provider_only_in_words_and_never_a_key() -> None:
    card = _source("components", "SourceCvCard.tsx")
    assert "Zero Data Retention" in card and "there is no login" in card


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print()
    print("All test_presentation_flow.py checks passed." if failures == 0 else f"{failures} check(s) failed.")
    raise SystemExit(1 if failures else 0)
