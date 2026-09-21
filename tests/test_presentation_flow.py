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
