"""Wires the configured provider into the two pipeline abstractions.

    LLM_PROVIDER=groq  (default)  GroqLLMProvider + GroqCVWriter        -- production
    LLM_PROVIDER=dev              DevLLMProvider  + DeterministicCVWriter -- explicit fallback

Selection is explicit: a failed Groq call never falls back to the dev implementations. When
groq is selected without GROQ_API_KEY, building the services raises a clear, non-sensitive
LLMConfigurationError -- at request time, so the graph explorer still works without a key.
"""
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from llm.config import ROOT_DIR, LLMSettings, load_settings
from llm.groq_client import GroqStructuredClient
from llm.groq_cv_writer import GroqCVWriter
from llm.groq_extraction import CapabilityVerifier, GroqLLMProvider, graph_capability_verifier
from llm.source_profile import DevSourceCvParser, GroqSourceCvParser, SourceCvParser
from llm.writer_base import DeterministicWriterAdapter, ReportingCVWriter
from llm_provider import DevLLMProvider, LLMProvider

log = logging.getLogger("careergraph.llm")


@dataclass(frozen=True)
class LLMServices:
    settings: LLMSettings
    extraction_provider: LLMProvider
    cv_writer: ReportingCVWriter
    # Source CV parsing. Optional so services built by hand (tests, tools) need not supply one.
    source_parser: Optional[SourceCvParser] = None


def build_services(
    settings: LLMSettings,
    *,
    sdk_client: Any = None,
    capability_verifier: CapabilityVerifier = graph_capability_verifier,
    root: Optional[Path] = None,
) -> LLMServices:
    if settings.provider == "dev":
        return LLMServices(
            settings=settings,
            extraction_provider=DevLLMProvider(root or ROOT_DIR),
            cv_writer=DeterministicWriterAdapter(),
            source_parser=DevSourceCvParser(),
        )

    if sdk_client is None:
        settings.require_api_key()  # fail early with the clear configuration error
    client = GroqStructuredClient(settings, sdk_client=sdk_client)
    return LLMServices(
        settings=settings,
        extraction_provider=GroqLLMProvider(settings, client, capability_verifier=capability_verifier),
        cv_writer=GroqCVWriter(settings, client),
        source_parser=GroqSourceCvParser(settings, client),
    )


@lru_cache(maxsize=1)
def _default_services() -> LLMServices:
    return build_services(load_settings())


def get_llm_services() -> LLMServices:
    """FastAPI dependency. Tests override it with app.dependency_overrides."""
    return _default_services()


def log_startup_status() -> None:
    """One non-sensitive line at startup, so a missing key is visible immediately."""
    try:
        settings = load_settings()
    except Exception as exc:  # LLMConfigurationError carries a safe message
        log.warning("llm_config_invalid: %s", getattr(exc, "message", "invalid LLM configuration"))
        return
    if settings.provider == "groq" and not settings.configured:
        log.warning(
            "llm_not_configured: LLM_PROVIDER=groq but GROQ_API_KEY is not set; "
            "analysis and CV generation will return a configuration error until it is."
        )
    else:
        log.info(
            "llm_ready provider=%s extraction_model=%s writing_model=%s",
            settings.provider,
            settings.extraction_model if settings.provider == "groq" else "-",
            settings.writing_model if settings.provider == "groq" else "-",
        )
