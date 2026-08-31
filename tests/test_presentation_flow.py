"""Structural regression tests for the presentation-flow frontend contract:

  - Screen 1 (Job Description) only ever generates a Claude prompt in its primary
    path; the raw-JD auto-analyze call is confined to an explicitly labeled debug
    panel, never the primary "Generate Claude Prompt" button.
  - Screen 2 (Upload Requirements) never re-runs extraction -- it only validates
    and matches an already-extracted RequirementList.
  - The generated CV-writing prompt actually embeds the real cv_context JSON
    (not a placeholder), and the extraction prompt actually embeds the real JD.
  - The primary flow's CV Context screen never mentions/routes through
    cv_evidence.json, while cv_evidence.py itself is NOT deleted (kept as an
    optional/experimental layer per its own CLI/tests).

No JS test framework is configured for this project (see frontend/package.json),
so these are plain text/structural checks against the .tsx/.ts source -- the same
static-guard style already used by backend/test_api.py's write-Cypher AST scan,
adapted to source text since these are TypeScript, not Python.

Usage:
    python tests/test_presentation_flow.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend" / "src"


def _function_body(source: str, fn_name: str) -> str:
    """Best-effort slice from `function fn_name` (or `async function fn_name`) to
    the next top-level `function`/`async function` keyword, or end of file."""
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(fn_name)}\b", source)
    assert match, f"could not find function {fn_name!r} in source"
    rest = source[match.end():]
    next_fn = re.search(r"\n\s*(?:async\s+)?function\s+\w+", rest)
    return rest[: next_fn.start()] if next_fn else rest


def test_job_description_screen_primary_path_only_generates_prompt() -> None:
    source = (FRONTEND_SRC / "pages" / "NewCv.tsx").read_text(encoding="utf-8")

    primary = _function_body(source, "handleGeneratePrompt")
    assert "buildExtractionPrompt" in primary
    assert "analyzeJob" not in primary, (
        "the primary 'Generate Claude Prompt' handler must never call the raw-JD "
        "auto-analyze endpoint"
    )

    debug = _function_body(source, "handleDebugAutoAnalyze")
    assert "analyzeJob" in debug, "the raw-JD auto-analyze call must live in the debug panel only"

    # The debug panel must be visually/structurally distinct, not the default view.
    assert "debugOpen" in source and "Debug tools" in source


def test_requirements_upload_screen_never_reruns_extraction() -> None:
    source = (FRONTEND_SRC / "pages" / "RequirementsUpload.tsx").read_text(encoding="utf-8")
    assert "buildExtractionPrompt" not in source, "Screen 2 must never call requirement extraction again"
    assert "analyzeJob" not in source, "Screen 2 must never call the raw-JD extraction endpoint"
    assert "analyzeRequirements" in source, "Screen 2 must validate+match via the already-extracted-requirements endpoint"


def test_cv_prompt_embeds_the_real_cv_context_json() -> None:
    source = (FRONTEND_SRC / "prompts.ts").read_text(encoding="utf-8")
    cv_prompt_body = _function_body(source, "buildCvPrompt")
    assert "JSON.stringify(cvContext" in cv_prompt_body, (
        "the CV-writing prompt must embed the actual cv_context JSON, not a placeholder"
    )

    extraction_prompt_body = _function_body(source, "buildExtractionPrompt")
    assert "${jobDescription}" in extraction_prompt_body, (
        "the extraction prompt must append the actual job description text"
    )


def test_cv_context_screen_never_routes_through_cv_evidence() -> None:
    source = (FRONTEND_SRC / "pages" / "CvContext.tsx").read_text(encoding="utf-8")
    assert "cv_evidence" not in source.lower()
    match_review = (FRONTEND_SRC / "pages" / "MatchReview.tsx").read_text(encoding="utf-8")
    assert "cv_evidence" not in match_review.lower()


def test_cv_evidence_kept_as_optional_layer_not_deleted() -> None:
    assert (ROOT / "pipeline" / "cv_evidence.py").exists()
    assert (ROOT / "tests" / "test_cv_evidence.py").exists()
    # Not wired into the primary frontend flow's API client.
    client_source = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert "cv_evidence" not in client_source.lower()


def test_deterministic_cv_renderer_kept_but_demoted_to_debug() -> None:
    """cv_writer.DeterministicCVWriter / backend/routes/cv.py must still exist
    (not deleted) but the frontend nav must label the page that uses it as a
    debug/secondary entry, not one of the four primary flow steps."""
    assert (ROOT / "pipeline" / "cv_writer.py").exists()
    assert (ROOT / "backend" / "routes" / "cv.py").exists()
    app_source = (FRONTEND_SRC / "App.tsx").read_text(encoding="utf-8")
    assert 'label="Debug"' in app_source
    for step_label in ('"1 Job"', '"2 Claude"', '"3 Neo4j"', '"4 Claude"'):
        assert step_label in app_source, f"primary flow step {step_label} missing from nav"


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
