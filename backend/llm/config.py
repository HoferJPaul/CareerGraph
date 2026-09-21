"""Centralized LLM configuration -- the ONE place model ids, timeouts and limits are read.

Values come from the process environment first (deployments), then from the repo-root .env
file (local development). The API key exists only on the server: it is held as a pydantic
SecretStr (masked in repr/dumps), is never returned by any API, never logged, and never
accepted from the browser.
"""
import os
from pathlib import Path
from typing import Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, SecretStr

from llm.errors import LLMConfigurationError

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_JOB_DESCRIPTION_CHARS = 20_000
DEFAULT_MAX_CV_CONTEXT_CHARS = 60_000
DEFAULT_MAX_SOURCE_CV_CHARS = 40_000

Provider = Literal["groq", "dev"]


class LLMSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Provider = "groq"
    api_key: Optional[SecretStr] = None
    extraction_model: str = DEFAULT_MODEL
    writing_model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES  # retries AFTER the first attempt
    strict_schema: bool = True
    max_job_description_chars: int = DEFAULT_MAX_JOB_DESCRIPTION_CHARS
    max_cv_context_chars: int = DEFAULT_MAX_CV_CONTEXT_CHARS
    max_source_cv_chars: int = DEFAULT_MAX_SOURCE_CV_CHARS

    @property
    def dev_fallback(self) -> bool:
        """True when the deterministic/heuristic development fallbacks are selected."""
        return self.provider == "dev"

    @property
    def configured(self) -> bool:
        return self.provider == "dev" or bool(self.api_key and self.api_key.get_secret_value())

    def require_api_key(self) -> str:
        if self.provider != "groq":
            raise LLMConfigurationError("The Groq provider is not selected (LLM_PROVIDER).")
        key = self.api_key.get_secret_value() if self.api_key else ""
        if not key:
            raise LLMConfigurationError(
                "GROQ_API_KEY is not set on the server. Add it to the backend environment "
                "(see .env.example), or set LLM_PROVIDER=dev to use the development fallbacks."
            )
        return key

    def public_view(self) -> dict:
        """Safe-to-expose summary. Deliberately excludes the key and everything derived from it."""
        return {
            "provider": self.provider,
            "extractionModel": self.extraction_model if self.provider == "groq" else None,
            "writingModel": self.writing_model if self.provider == "groq" else None,
            "configured": self.configured,
            "devFallback": self.dev_fallback,
            "maxJobDescriptionChars": self.max_job_description_chars,
        }


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _number(raw: str, name: str, cast, minimum) -> float:
    try:
        value = cast(raw)
    except ValueError:
        raise LLMConfigurationError(f"{name} must be a number.") from None
    if value < minimum:
        raise LLMConfigurationError(f"{name} must be at least {minimum}.")
    return value


def load_settings(
    environ: Optional[Mapping[str, str]] = None, env_file: Optional[Path] = None
) -> LLMSettings:
    """Build settings from `environ` (default: os.environ) layered over `env_file`
    (default: <repo>/.env). Blank values count as unset, so the empty `GROQ_API_KEY=` line
    in .env.example means "not configured", never "configured with an empty key"."""
    merged: dict[str, str] = dict(_read_env_file(env_file if env_file is not None else ROOT_DIR / ".env"))
    merged.update({k: v for k, v in (environ if environ is not None else os.environ).items()})

    def get(name: str) -> Optional[str]:
        value = merged.get(name, "").strip()
        return value or None

    provider = (get("LLM_PROVIDER") or "groq").lower()
    if provider not in ("groq", "dev"):
        raise LLMConfigurationError("LLM_PROVIDER must be 'groq' or 'dev'.")

    kwargs: dict = {"provider": provider}
    if get("GROQ_API_KEY"):
        kwargs["api_key"] = SecretStr(get("GROQ_API_KEY"))
    if get("LLM_EXTRACTION_MODEL"):
        kwargs["extraction_model"] = get("LLM_EXTRACTION_MODEL")
    if get("LLM_WRITING_MODEL"):
        kwargs["writing_model"] = get("LLM_WRITING_MODEL")
    if get("LLM_REQUEST_TIMEOUT_SECONDS"):
        kwargs["timeout_seconds"] = _number(get("LLM_REQUEST_TIMEOUT_SECONDS"), "LLM_REQUEST_TIMEOUT_SECONDS", float, 1)
    if get("LLM_MAX_RETRIES"):
        kwargs["max_retries"] = int(_number(get("LLM_MAX_RETRIES"), "LLM_MAX_RETRIES", int, 0))
    if get("LLM_STRICT_SCHEMA"):
        kwargs["strict_schema"] = get("LLM_STRICT_SCHEMA").lower() not in ("0", "false", "no", "off")
    if get("LLM_MAX_JOB_DESCRIPTION_CHARS"):
        kwargs["max_job_description_chars"] = int(
            _number(get("LLM_MAX_JOB_DESCRIPTION_CHARS"), "LLM_MAX_JOB_DESCRIPTION_CHARS", int, 100)
        )
    if get("LLM_MAX_CV_CONTEXT_CHARS"):
        kwargs["max_cv_context_chars"] = int(
            _number(get("LLM_MAX_CV_CONTEXT_CHARS"), "LLM_MAX_CV_CONTEXT_CHARS", int, 1000)
        )
    if get("LLM_MAX_SOURCE_CV_CHARS"):
        kwargs["max_source_cv_chars"] = int(
            _number(get("LLM_MAX_SOURCE_CV_CHARS"), "LLM_MAX_SOURCE_CV_CHARS", int, 1000)
        )
    return LLMSettings(**kwargs)


def env_value(
    name: str, environ: Optional[Mapping[str, str]] = None, env_file: Optional[Path] = None
) -> Optional[str]:
    """One setting, read the same way load_settings() reads them (process environment first, then the
    repo-root .env). Blank counts as unset."""
    merged: dict[str, str] = dict(_read_env_file(env_file if env_file is not None else ROOT_DIR / ".env"))
    merged.update(environ if environ is not None else os.environ)
    return merged.get(name, "").strip() or None
