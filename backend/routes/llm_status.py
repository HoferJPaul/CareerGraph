"""Read-only, non-sensitive view of the LLM configuration, so the UI can show which provider
and model are active and label development fallbacks. Never includes the API key -- or
anything derived from it beyond a `configured` boolean."""
from fastapi import APIRouter, Depends

from api_schemas import LlmStatus
from llm.config import LLMSettings, load_settings

router = APIRouter(prefix="/api/llm", tags=["llm"])


def get_llm_settings() -> LLMSettings:
    """FastAPI dependency. Tests override it with app.dependency_overrides."""
    return load_settings()


@router.get("/status", response_model=LlmStatus)
def llm_status(settings: LLMSettings = Depends(get_llm_settings)) -> LlmStatus:
    return LlmStatus(**settings.public_view())
