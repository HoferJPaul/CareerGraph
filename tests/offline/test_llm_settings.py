"""Configuration: model ids are centralized, a missing key fails clearly, and the key can never
leave the server through settings, reprs or the status view."""
from pathlib import Path

import pytest

from helpers import FAKE_KEY, settings
from llm.config import DEFAULT_MODEL, load_settings
from llm.errors import LLMConfigurationError
from llm.factory import build_services
from llm.groq_client import GroqStructuredClient

NO_FILE = Path("does-not-exist.env")


def test_defaults_match_the_documented_env_example() -> None:
    s = load_settings({}, NO_FILE)
    assert s.provider == "groq"
    assert s.extraction_model == DEFAULT_MODEL == "openai/gpt-oss-120b"
    assert s.writing_model == "openai/gpt-oss-120b"
    assert s.timeout_seconds == 60 and s.max_retries == 3 and s.strict_schema is True


def test_extraction_and_writing_models_are_independently_configurable() -> None:
    s = load_settings(
        {"LLM_EXTRACTION_MODEL": "openai/gpt-oss-20b", "LLM_WRITING_MODEL": "openai/gpt-oss-120b"}, NO_FILE
    )
    assert s.extraction_model == "openai/gpt-oss-20b"
    assert s.writing_model == "openai/gpt-oss-120b"


def test_missing_api_key_is_a_clear_non_sensitive_configuration_error() -> None:
    s = load_settings({"LLM_PROVIDER": "groq", "GROQ_API_KEY": ""}, NO_FILE)  # blank == unset
    assert not s.configured
    with pytest.raises(LLMConfigurationError) as exc:
        s.require_api_key()
    assert "GROQ_API_KEY" in exc.value.message and "LLM_PROVIDER=dev" in exc.value.message
    assert exc.value.http_status == 503 and exc.value.code == "llm_not_configured"


def test_building_live_services_without_a_key_fails_at_request_time_not_silently() -> None:
    with pytest.raises(LLMConfigurationError):
        build_services(load_settings({"LLM_PROVIDER": "groq"}, NO_FILE))


def test_client_without_a_key_never_constructs_the_sdk() -> None:
    client = GroqStructuredClient(load_settings({"LLM_PROVIDER": "groq"}, NO_FILE))
    with pytest.raises(LLMConfigurationError):
        client.complete(
            operation="x", model="m", system_prompt="s", user_content="u", schema_name="n",
            schema={}, max_completion_tokens=1, temperature=0.0,
        )


def test_dev_provider_needs_no_key_and_is_labelled_a_fallback() -> None:
    s = load_settings({"LLM_PROVIDER": "dev"}, NO_FILE)
    assert s.configured and s.dev_fallback
    assert s.public_view()["extractionModel"] is None


def test_unknown_provider_and_bad_numbers_are_rejected_with_safe_messages() -> None:
    for env in ({"LLM_PROVIDER": "openai"}, {"LLM_MAX_RETRIES": "many"}, {"LLM_REQUEST_TIMEOUT_SECONDS": "0"}):
        with pytest.raises(LLMConfigurationError):
            load_settings(env, NO_FILE)


def test_process_environment_overrides_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('LLM_EXTRACTION_MODEL="from-file"\nGROQ_API_KEY=file-key\n# comment\n', encoding="utf-8")
    assert load_settings({}, env_file).extraction_model == "from-file"  # quotes stripped
    assert load_settings({"LLM_EXTRACTION_MODEL": "from-env"}, env_file).extraction_model == "from-env"


def test_api_key_never_appears_in_repr_dump_or_public_view() -> None:
    s = settings()
    assert s.api_key.get_secret_value() == FAKE_KEY
    for rendered in (repr(s), str(s), s.model_dump_json(), str(s.model_dump()), str(s.public_view())):
        assert FAKE_KEY not in rendered
    assert s.public_view()["configured"] is True
