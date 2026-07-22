"""Centralised application configuration.

Single source of truth for runtime settings, loaded once from the
environment (and an optional ``.env`` file) via ``pydantic-settings``.
Import :func:`get_settings` wherever configuration is needed rather than
reading ``os.environ`` directly.

Inference is provider-selectable (:data:`Settings.provider`): the direct
first-party Anthropic API or Microsoft Foundry (ADR-0016). Each provider's
credentials live in their own nested ``BaseSettings`` so only the *selected*
provider's credentials are required — a Foundry-only Azure job does not need
an Anthropic key, and vice versa.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["anthropic", "foundry"]

DEFAULTS: dict[str, Any] = {
    "provider": "anthropic",
    "self_consistency_n": 5,
    "temperature": 0.4,
    "confidence_threshold": 0.6,
    "anthropic_model": "claude-haiku-4-5",
    "foundry_model": "claude-haiku-4-5",
    "foundry_use_managed_identity": False,
    "foundry_token_scope": "https://cognitiveservices.azure.com/.default",
}


class AnthropicSettings(BaseSettings):
    """Credentials/model for the first-party Anthropic API.

    ``api_key`` is read from the **unprefixed** ``ANTHROPIC_API_KEY`` (matching
    the Anthropic SDK's own resolution). It is optional here so this section can
    load even when Foundry is the selected provider; :class:`Settings` enforces
    its presence when ``provider == "anthropic"``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = Field(default=DEFAULTS["anthropic_model"], validation_alias="CLASSIFIER_ANTHROPIC_MODEL")


class FoundrySettings(BaseSettings):
    """Credentials/model for Microsoft Foundry (formerly Azure AI Foundry).

    ``resource`` and ``api_key`` reuse the Foundry SDK's own env var names so
    the SDK can also self-infer them. Authentication is **explicit**: set
    ``use_managed_identity`` for Entra ID / managed identity, otherwise an
    ``api_key`` is required. :class:`Settings` enforces both, plus ``resource``,
    when ``provider == "foundry"``, so a forgotten key fails at startup rather
    than silently falling back to a doomed managed-identity attempt.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    resource: str | None = Field(default=None, validation_alias="ANTHROPIC_FOUNDRY_RESOURCE")
    api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_FOUNDRY_API_KEY")
    use_managed_identity: bool = Field(
        default=DEFAULTS["foundry_use_managed_identity"], validation_alias="CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY"
    )
    model: str = Field(default=DEFAULTS["foundry_model"], validation_alias="CLASSIFIER_FOUNDRY_MODEL")
    token_scope: str = Field(default=DEFAULTS["foundry_token_scope"], validation_alias="CLASSIFIER_FOUNDRY_TOKEN_SCOPE")


class Settings(BaseSettings):
    """Application settings resolved from the environment and ``.env``.

    Inference provider is chosen by ``CLASSIFIER_PROVIDER`` (default
    ``anthropic``); each provider's credentials live in a nested section so only
    the selected provider's credentials are required. The classifier-specific
    knobs use the ``CLASSIFIER_`` prefix.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    provider: Provider = Field(default=DEFAULTS["provider"], validation_alias="CLASSIFIER_PROVIDER")
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    foundry: FoundrySettings = Field(default_factory=FoundrySettings)

    self_consistency_n: int = Field(default=DEFAULTS["self_consistency_n"], ge=1, validation_alias="CLASSIFIER_N")
    temperature: float = Field(
        default=DEFAULTS["temperature"], ge=0.0, le=1.0, validation_alias="CLASSIFIER_TEMPERATURE"
    )
    confidence_threshold: float = Field(
        default=DEFAULTS["confidence_threshold"], ge=0.0, le=1.0, validation_alias="CLASSIFIER_CONFIDENCE_THRESHOLD"
    )

    @model_validator(mode="after")
    def _require_selected_provider_credentials(self) -> "Settings":
        """Fail at startup if the *selected* provider is missing credentials.

        Only the chosen provider is checked, so the other provider's absent
        credentials never block startup.
        """
        if self.provider == "anthropic" and self.anthropic.api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required when CLASSIFIER_PROVIDER=anthropic")
        if self.provider == "foundry":
            if self.foundry.resource is None:
                raise ValueError("ANTHROPIC_FOUNDRY_RESOURCE is required when CLASSIFIER_PROVIDER=foundry")
            if self.foundry.api_key is None and not self.foundry.use_managed_identity:
                raise ValueError(
                    "Foundry requires ANTHROPIC_FOUNDRY_API_KEY, or "
                    "CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=true for managed identity"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton (loaded once)."""
    return Settings()  # type: ignore[call-arg]  # values are resolved from env/.env
