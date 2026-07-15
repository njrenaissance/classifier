"""Centralised application configuration.

Single source of truth for runtime settings, loaded once from the
environment (and an optional ``.env`` file) via ``pydantic-settings``.
Import :func:`get_settings` wherever configuration is needed rather than
reading ``os.environ`` directly.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings resolved from the environment and ``.env``.

    The Anthropic credential is read from the **unprefixed**
    ``ANTHROPIC_API_KEY`` (matching the Anthropic SDK's own resolution),
    while the classifier-specific knobs use the ``CLASSIFIER_`` prefix.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: SecretStr = Field(validation_alias="ANTHROPIC_API_KEY")
    self_consistency_n: int = Field(default=5, ge=1, validation_alias="CLASSIFIER_N")
    temperature: float = Field(default=0.4, ge=0.0, le=1.0, validation_alias="CLASSIFIER_TEMPERATURE")
    confidence_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, validation_alias="CLASSIFIER_CONFIDENCE_THRESHOLD"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton (loaded once)."""
    return Settings()  # type: ignore[call-arg]  # values are resolved from env/.env
