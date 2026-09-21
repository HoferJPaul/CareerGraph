"""Privacy and secret hygiene: what the model is (and is not) shown, and that no key can leak."""
import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from fixtures import build_cv_context
from llm.cv_input import build_writer_payload, redact_contact_details, render_writer_message
from llm.evidence_registry import build_registry

ROOT = Path(__file__).resolve().parent.parent.parent
LLM_PKG = ROOT / "backend" / "llm"
KEY_PATTERN = re.compile(r"gsk_[A-Za-z0-9]{20,}")


def _writer_message(ctx) -> str:
    return render_writer_message(build_writer_payload(ctx, build_registry(ctx)), 1_000_000)


def test_contact_details_are_redacted_from_model_input() -> None:
    ctx = build_cv_context()
    ctx.evidenceStories[0].strongestAchievements[0].description += (
        " Contact jane.doe@example.com or +43 660 1234567 or (555) 123-4567."
    )
    ctx.evidenceStories[1].description = "Reach me at builder@example.org"
    message = _writer_message(ctx)
    for private in ("jane.doe@example.com", "builder@example.org", "660 1234567", "123-4567"):
        assert private not in message
    assert "[redacted]" in message


def test_redaction_does_not_damage_dates_or_metrics() -> None:
    text = "From 2025-04-01 to 2025-09-30 the service handled 1,000,000 requests, a 60% cut, 30 students."
    assert redact_contact_details(text) == text


def test_model_input_excludes_names_scores_credentials_and_guidance() -> None:
    message = _writer_message(build_cv_context())
    payload = json.loads(message)
    assert set(payload) == {"targetRequirements", "literalGaps", "availableSkills", "stories"}

    lowered = message.lower()
    for internal in (
        "matchconfidence", "confidence", "recommendation", "evidencestrength", "cvguidance",
        "targetingprinciples", "matchtype", "neo4j", "password", "paul hofer",
    ):
        assert internal not in lowered, internal
    # Job-derived requirement wording is not needed to write the CV: only skill, importance, category.
    assert set(payload["targetRequirements"][0]) == {"skillQuery", "importance", "category"}


def test_model_input_is_only_the_selected_context_not_the_graph() -> None:
    ctx = build_cv_context()
    payload = json.loads(_writer_message(ctx))
    assert {s["storyId"] for s in payload["stories"]} == {f"story:{s.label}" for s in ctx.evidenceStories}
    assert payload["literalGaps"] == ["cloudwatch", "express", "aws"]
    # Every evidence id shown to the model is one the registry can resolve -- nothing else is citable.
    registry = build_registry(ctx)
    ids = re.findall(r'"evidenceId":"([^"]+)"', json.dumps(payload, separators=(",", ":")))
    assert ids and all(registry.resolve(i) is not None for i in ids)


def test_llm_package_never_touches_neo4j_credentials_or_writes_cypher() -> None:
    for path in LLM_PKG.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "NEO4J" not in source, f"{path.name} must not read Neo4j credentials"
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # No module in the LLM layer executes a query: model output is never run as Cypher.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                pytest.fail(f"{path.name} calls .run(...); the LLM layer must never execute Cypher")


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }


def test_deterministic_matching_modules_do_not_depend_on_any_llm_code() -> None:
    """The graph proves: matching/tailoring stay deterministic and provider-free."""
    forbidden = {"groq", "llm", "llm_provider", "openai", "anthropic"}
    modules = [ROOT / "pipeline" / n for n in (
        "matching.py", "tailor_cv.py", "match_job.py", "capability_suggest.py", "requirement_schema.py", "metrics.py"
    )] + [ROOT / "backend" / "pipeline.py"]
    for path in modules:
        assert not _imported_roots(path) & forbidden, f"{path.name} must not import LLM code"


def test_no_api_key_in_the_frontend_bundle_or_source() -> None:
    hits = []
    for path in (ROOT / "frontend" / "src").rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx", ".css", ".html"}:
            text = path.read_text(encoding="utf-8")
            for needle in ("GROQ_API_KEY", "gsk_", "api.groq.com", "VITE_GROQ", "Bearer "):
                if needle in text:
                    hits.append(f"{path.name}: {needle}")
    assert not hits, hits


def test_env_example_has_a_blank_key_and_no_real_secret() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^GROQ_API_KEY=\s*$", text, re.MULTILINE)
    assert not KEY_PATTERN.search(text)


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def test_no_secret_or_personal_file_is_tracked_by_git() -> None:
    tracked = _git("ls-files")
    if tracked is None:
        pytest.skip("not a git checkout")
    files = tracked.splitlines()
    assert ".env" not in files
    assert not [f for f in files if f.startswith("output/")]
    assert not {"data/career_seed.json", "data/jobs.txt", "data/requirements.json"} & set(files)
    for f in files:
        path = ROOT / f
        if path.suffix in {".py", ".ts", ".tsx", ".md", ".json", ".example", ".txt", ".css", ".html"} and path.is_file():
            assert not KEY_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")), f"key-like string in {f}"
