"""Where the Source CV lives and what can reach it: gitignored storage, no paths in any response, no Neo4j
writes, no CV data or credentials in tracked files, and safe behaviour when two writers collide."""
import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from source_cv.errors import ProfileVersionConflictError
from source_cv.service import Done
from source_cv.store import private_data_dir
from source_fixtures import EMAIL, NAME, PHONE
from source_helpers import cv_docx, cv_pdf, groq_parser, make_service, source_api, upload

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_PKG = ROOT / "backend" / "source_cv"


def _git(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    except OSError:
        return None


# ---- storage location ------------------------------------------------------------------------------------------


def test_the_default_private_directory_is_inside_the_repo_data_dir_and_is_gitignored() -> None:
    default = private_data_dir(environ={}, env_file=ROOT / "no-such-env-file")
    assert default == ROOT / "data" / "private"
    result = _git("check-ignore", "-q", "data/private/source_cv/profile.json")
    if result is None:
        pytest.skip("git is not available")
    assert result.returncode == 0, "the stored profile must be covered by .gitignore"
    for name in ("data/private/source_cv/upload-0123456789abcdef.pdf", "data/private/source_cv/upload-0123456789abcdef.docx",
                 "data/private/anything-else.json"):
        assert _git("check-ignore", "-q", name).returncode == 0, name


def test_a_custom_private_directory_can_be_configured_and_is_used(tmp_path) -> None:
    assert private_data_dir(environ={"CAREERGRAPH_PRIVATE_DIR": str(tmp_path)}, env_file=ROOT / "no-such-env-file") == tmp_path


def test_no_source_cv_data_is_tracked_by_git() -> None:
    result = _git("ls-files")
    if result is None or result.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = result.stdout.splitlines()
    assert not [f for f in tracked if f.startswith("data/private/") or "/source_cv/upload-" in f or f.endswith("source_cv/profile.json")]
    for f in tracked:  # the fictional test candidate is the only personal-looking data allowed, and only in tests/
        path = ROOT / f
        if path.suffix in {".py", ".ts", ".tsx", ".md", ".json", ".txt", ".css", ".html"} and path.is_file() and not f.startswith("tests/"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in (EMAIL, PHONE, "Tannenbaum"):
                assert needle not in text, f"{needle!r} found in {f}"


def test_the_working_tree_contains_no_source_cv_store_after_the_test_run(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
    assert not (ROOT / "data" / "private" / "source_cv").exists(), "tests must never write to the developer's private directory"


# ---- nothing leaks through the API ----------------------------------------------------------------------------------


def test_no_endpoint_response_contains_a_server_path_a_stored_file_name_or_raw_document_text(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        responses = [
            upload(client, cv_pdf(), "My CV.pdf"),
            client.get("/api/source-cv"),
            client.get("/api/source-cv/status"),
            client.post("/api/source-cv/reparse"),
            upload(client, b"nope" * 50),
            upload(client, cv_docx(), "second.docx"),
            client.put("/api/source-cv", json={"expectedRevision": 99}),
            client.delete("/api/source-cv"),
            client.get("/api/source-cv"),
        ]
    for response in responses:
        text = response.text
        assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text, response.request.url
        assert not re.search(r"upload-[0-9a-f]{16}", text) and "profile.json" not in text and "source_cv/" not in text and "source_cv" + chr(92) not in text
        assert "Links found in the document" not in text  # raw extracted text
        assert "\"excerpt\"" not in text and "sha256" not in text


def test_the_review_view_carries_the_profile_but_no_provenance_excerpts(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        body = upload(client, cv_pdf()).json()
    dumped = json.dumps(body)
    assert NAME in dumped and "excerpt" not in dumped


# ---- read-only Neo4j, no provider code in the deterministic layers ------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }


def test_the_source_cv_package_never_touches_neo4j_or_a_model_provider() -> None:
    for path in SOURCE_PKG.glob("*.py"):
        assert not _imports(path) & {"neo4j", "groq", "deps"}, f"{path.name} must stay free of Neo4j and provider code"
        source = path.read_text(encoding="utf-8")
        assert "NEO4J" not in source and "GROQ" not in source, path.name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run" and "session" in ast.dump(node.func.value):
                pytest.fail(f"{path.name} runs Cypher")


def test_the_source_cv_routes_take_no_neo4j_session_and_the_graph_flow_still_uses_the_read_only_one() -> None:
    routes = (ROOT / "backend" / "routes" / "source_cv.py").read_text(encoding="utf-8")
    assert "get_session" not in routes and "neo4j" not in routes.lower()
    jobs = (ROOT / "backend" / "routes" / "jobs.py").read_text(encoding="utf-8")
    assert "get_session" in jobs  # unchanged: the analysis still runs its read-only matching


def test_new_backend_modules_contain_no_cypher_at_all() -> None:
    cypher = re.compile(r"(MATCH|MERGE|CREATE|DETACH DELETE)\s*\(")
    for path in [*SOURCE_PKG.glob("*.py"), *(ROOT / "backend" / "llm").glob("complete_*.py"), ROOT / "backend" / "source_views.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and cypher.search(node.value):
                pytest.fail(f"{path.name} contains a Cypher-looking string")


def test_source_cv_endpoints_never_require_or_accept_the_api_key_and_the_key_is_not_in_the_profile(tmp_path) -> None:
    from helpers import FAKE_KEY

    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        stored = (tmp_path / "source_cv" / "profile.json").read_text(encoding="utf-8")
        status = client.get("/api/source-cv/status").text
    assert FAKE_KEY not in stored and FAKE_KEY not in status and "api_key" not in stored.lower()


# ---- writers colliding ------------------------------------------------------------------------------------------------


def test_two_uploads_racing_cannot_overwrite_each_other_silently(tmp_path) -> None:
    service = make_service(tmp_path)
    first = service.ingest_events(service.prepare("a.pdf", cv_pdf()))
    assert next(first).name == "parsing"  # the first upload has read revision "none" and is mid-parse

    second = make_service(tmp_path, groq_parser())  # a second writer over the same directory finishes first
    result = second.run(second.ingest_events(second.prepare("b.pdf", cv_docx())))
    assert result.revision == 1

    with pytest.raises(ProfileVersionConflictError):
        for event in first:  # ...so the first one must not blindly replace it
            if isinstance(event, Done):
                pytest.fail("a stale upload was committed")
    assert service.get().upload.originalFilename == "b.pdf"  # the writer that finished first is what is stored
    assert len([p for p in (tmp_path / "source_cv").iterdir()]) == 2  # one profile, one original: no orphan from the loser


def test_a_stale_edit_cannot_overwrite_a_newer_replacement(tmp_path) -> None:
    with source_api(make_service(tmp_path)) as client:
        upload(client, cv_pdf())
        stale_revision = client.get("/api/source-cv").json()["profile"]["revision"]
        upload(client, cv_docx(), "newer.docx")  # replaced: revision 2
        response = client.put("/api/source-cv", json={"expectedRevision": stale_revision})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "profile_version_conflict"
        assert client.get("/api/source-cv").json()["profile"]["upload"]["originalFilename"] == "newer.docx"
