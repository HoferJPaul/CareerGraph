"""FastAPI wiring for the Source CV service.

The service is built lazily and takes its parser through a callable, so the endpoints that need no model
(status, review, edit, delete) keep working when GROQ_API_KEY is missing; only an upload or re-parse
reports `llm_not_configured`. Tests override `get_source_cv_service` with app.dependency_overrides.
"""
from functools import lru_cache

from llm.config import load_settings
from llm.factory import get_llm_services
from source_cv.service import SourceCvService
from source_cv.store import FileSourceCvStore, private_data_dir


@lru_cache(maxsize=1)
def _default_service() -> SourceCvService:
    settings = load_settings()
    return SourceCvService(
        FileSourceCvStore(private_data_dir()),
        lambda: get_llm_services().source_parser,
        max_text_chars=settings.max_source_cv_chars,
    )


def get_source_cv_service() -> SourceCvService:
    return _default_service()
