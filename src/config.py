"""Centralised application configuration.

Single source of truth for runtime settings, loaded once from the
environment (and an optional ``.env`` file) via ``pydantic-settings``.
Import :func:`get_settings` wherever configuration is needed rather than
reading ``os.environ`` directly.

:class:`Settings` aggregates every configuration section as an **optional**
nested model: the inference provider (``anthropic`` / ``foundry``), the
PostgreSQL state store (``database``), Microsoft Graph (``graph``), the
work queue (``queue``), the walker job (``walker``), and the processor job
(``processor``). A section is ``None`` when its environment is absent and a
validated model when present, so each job supplies only what it uses — the walker
needs ``database``/``graph``/``queue``/``walker`` but no inference credentials,
the processor needs ``database``/``graph``/``queue``/``processor`` plus the
selected provider, Alembic migrations need only ``database``, and the local CLI
needs only a provider. The *selected*
provider's credentials are enforced where a client is built
(``classifier.create_classifier``), not at load time, so loading ``Settings``
never demands credentials a job does not use.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["anthropic", "foundry"]

DEFAULT_MODEL = "claude-haiku-4-5"  # ADR-0002 — pinned; never append a date suffix.

DEFAULTS: dict[str, Any] = {
    "provider": "anthropic",
    "self_consistency_n": 5,
    "temperature": 0.4,
    "confidence_threshold": 0.6,
    "anthropic_model": DEFAULT_MODEL,
    "foundry_model": DEFAULT_MODEL,
    "foundry_use_managed_identity": False,
    "foundry_token_scope": "https://cognitiveservices.azure.com/.default",
    "graph_use_managed_identity": False,
    "graph_token_scope": "https://graph.microsoft.com/.default",
    "graph_base_url": "https://graph.microsoft.com/v1.0",
    "queue_use_managed_identity": False,
    "walker_time_budget_seconds": 600,
}


class AnthropicSettings(BaseSettings):
    """Credentials/model for the first-party Anthropic API.

    ``api_key`` is read from the **unprefixed** ``ANTHROPIC_API_KEY`` (matching
    the Anthropic SDK's own resolution). The section is considered *configured*
    only when a key is present; :meth:`is_configured` drives whether
    :class:`Settings` keeps it or resolves it to ``None``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = Field(default=DEFAULTS["anthropic_model"], validation_alias="CLASSIFIER_ANTHROPIC_MODEL")

    @property
    def is_configured(self) -> bool:
        """True once an API key is present — the only credential this provider needs."""
        return self.api_key is not None


class FoundrySettings(BaseSettings):
    """Credentials/model for Microsoft Foundry (formerly Azure AI Foundry).

    ``resource`` and ``api_key`` reuse the Foundry SDK's own env var names.
    Authentication is **explicit**: set ``use_managed_identity`` for Entra ID /
    managed identity, otherwise an ``api_key`` is required. A section that is
    *partially* configured (e.g. a resource with no credential) fails loudly at
    load; a wholly absent section resolves to ``None``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    resource: str | None = Field(default=None, validation_alias="ANTHROPIC_FOUNDRY_RESOURCE")
    api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_FOUNDRY_API_KEY")
    use_managed_identity: bool = Field(
        default=DEFAULTS["foundry_use_managed_identity"], validation_alias="CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY"
    )
    model: str = Field(default=DEFAULTS["foundry_model"], validation_alias="CLASSIFIER_FOUNDRY_MODEL")
    token_scope: str = Field(default=DEFAULTS["foundry_token_scope"], validation_alias="CLASSIFIER_FOUNDRY_TOKEN_SCOPE")

    @property
    def is_configured(self) -> bool:
        """True when a resource and a usable credential (key or managed identity) are set."""
        return self.resource is not None and (self.api_key is not None or self.use_managed_identity)

    @model_validator(mode="after")
    def _reject_partial(self) -> "FoundrySettings":
        """Fail loudly on a half-configured section, so a typo isn't silently ignored."""
        intended = bool(self.resource or self.api_key or self.use_managed_identity)
        if intended and not self.is_configured:
            raise ValueError(
                "Foundry requires ANTHROPIC_FOUNDRY_RESOURCE and either ANTHROPIC_FOUNDRY_API_KEY, "
                "or CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=true for managed identity"
            )
        return self


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings for the cloud pipeline state store (ADR-0013).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__DATABASE_`` prefix, so the URL is read from
    ``CLASSIFIER__DATABASE_URL``. ``url`` is a :class:`~pydantic.SecretStr`
    because it may embed a password; call ``url.get_secret_value()`` to build the
    engine. The section is *configured* only when a URL is present — a job that
    never touches the database (the local CLI) simply gets ``Settings.database is
    None`` instead of a spurious requirement.
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__DATABASE_", env_file=".env", extra="ignore")

    url: SecretStr | None = None

    @property
    def is_configured(self) -> bool:
        """True once a connection URL is present."""
        return self.url is not None


class GraphSettings(BaseSettings):
    """Microsoft Graph app-only credentials for the SharePoint pipeline (ADR-0007/0015).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__GRAPH_`` prefix. Authentication is **explicit** via
    ``use_managed_identity``: set it for Entra ID / managed identity, otherwise
    the client-credentials trio (``tenant_id``/``client_id``/``client_secret``)
    is required. A *partially* configured section fails loudly at load; a wholly
    absent one resolves to ``None``.
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__GRAPH_", env_file=".env", extra="ignore")

    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    use_managed_identity: bool = DEFAULTS["graph_use_managed_identity"]
    token_scope: str = DEFAULTS["graph_token_scope"]
    base_url: str = DEFAULTS["graph_base_url"]

    @property
    def is_configured(self) -> bool:
        """True with managed identity, or the full client-credentials trio."""
        has_trio = bool(self.tenant_id and self.client_id and self.client_secret)
        return self.use_managed_identity or has_trio

    @model_validator(mode="after")
    def _reject_partial(self) -> "GraphSettings":
        """Fail loudly on a half-configured section (e.g. a tenant but no secret)."""
        if self.is_configured:
            return self
        intended = bool(self.tenant_id or self.client_id or self.client_secret or self.use_managed_identity)
        if intended:
            raise ValueError(
                "Graph app-only auth requires CLASSIFIER__GRAPH_TENANT_ID, CLASSIFIER__GRAPH_CLIENT_ID and "
                "CLASSIFIER__GRAPH_CLIENT_SECRET, or CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=true for managed identity"
            )
        return self


class QueueSettings(BaseSettings):
    """Azure Queue Storage settings for the walker→processor work queue (ADR-0012/0014).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__QUEUE_`` prefix. A queue ``name`` is always required; authentication
    is **explicit** — either a ``connection_string`` (local dev / Azurite) or, for
    production, an ``account_url`` with ``use_managed_identity`` set (Entra ID /
    managed identity). A *partially* configured section fails loudly at load; a
    wholly absent one resolves to ``None``.
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__QUEUE_", env_file=".env", extra="ignore")

    name: str | None = None
    connection_string: SecretStr | None = None
    account_url: str | None = None
    use_managed_identity: bool = DEFAULTS["queue_use_managed_identity"]

    @property
    def is_configured(self) -> bool:
        """True when a queue name and a usable credential (connection string or managed identity) are set."""
        has_managed = self.use_managed_identity and self.account_url is not None
        has_auth = self.connection_string is not None or has_managed
        return self.name is not None and has_auth

    @model_validator(mode="after")
    def _reject_partial(self) -> "QueueSettings":
        """Fail loudly on a half-configured section (e.g. a name but no credential)."""
        if self.is_configured:
            return self
        intended = bool(self.name or self.connection_string or self.account_url or self.use_managed_identity)
        if intended:
            raise ValueError(
                "Queue requires CLASSIFIER__QUEUE_NAME and either CLASSIFIER__QUEUE_CONNECTION_STRING, or "
                "CLASSIFIER__QUEUE_ACCOUNT_URL with CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY=true for managed identity"
            )
        return self


class WalkerSettings(BaseSettings):
    """Walker job settings: which drive to enumerate and its time budget (ADR-0014).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__WALKER_`` prefix. ``drive_id`` identifies the SharePoint
    document library to walk and is the section's one required input, so a job
    that never walks (the local CLI) simply gets ``Settings.walker is None``.
    ``time_budget_seconds`` bounds one scheduled run so a large first enumeration
    is resumed across slots rather than forced to finish in a single slot (default
    10 min).
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__WALKER_", env_file=".env", extra="ignore")

    drive_id: str | None = None
    time_budget_seconds: int = Field(default=DEFAULTS["walker_time_budget_seconds"], gt=0)

    @property
    def is_configured(self) -> bool:
        """True once a target drive id is present — the walk's one required input."""
        return self.drive_id is not None


class ProcessorSettings(BaseSettings):
    """Processor job settings: where the classifier reads its taxonomy (ADR-0012).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__PROCESSOR_`` prefix. ``category_file`` points at the
    category-definition Markdown the processor loads to build its voter; it is the
    section's one required input, so a job that never classifies (the walker, the
    local CLI which takes ``-c`` instead) simply gets ``Settings.processor is
    None``. The queue-triggered processor has no CLI args, so this is how the ACA
    job discovers the file.
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__PROCESSOR_", env_file=".env", extra="ignore")

    category_file: Path | None = None

    @property
    def is_configured(self) -> bool:
        """True once the category-definition file path is present — the job's one required input."""
        return self.category_file is not None


def _load_section[T: BaseSettings](section_type: type[T]) -> T | None:
    """Construct a nested settings section, or ``None`` when it is unconfigured.

    Each section parses its own env slice on construction and reports presence
    via ``is_configured``; a *partially* configured section raises from its own
    validator, so only a wholly absent section becomes ``None``.
    """
    section = section_type()  # type: ignore[call-arg]  # fields resolve from env/.env
    return section if section.is_configured else None  # type: ignore[attr-defined]  # every section defines is_configured


class Settings(BaseSettings):
    """Application settings resolved from the environment and ``.env``.

    Every section is optional and resolved to ``None`` when its environment is
    absent (see :func:`_load_section`). ``provider`` selects which inference
    section a classifier uses; that section's credentials are enforced in
    :func:`~classifier.create_classifier`, not here, so building ``Settings``
    never demands credentials a job does not use.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    provider: Provider = Field(default=DEFAULTS["provider"], validation_alias="CLASSIFIER_PROVIDER")

    anthropic: AnthropicSettings | None = Field(default_factory=lambda: _load_section(AnthropicSettings))
    foundry: FoundrySettings | None = Field(default_factory=lambda: _load_section(FoundrySettings))
    database: DatabaseSettings | None = Field(default_factory=lambda: _load_section(DatabaseSettings))
    graph: GraphSettings | None = Field(default_factory=lambda: _load_section(GraphSettings))
    queue: QueueSettings | None = Field(default_factory=lambda: _load_section(QueueSettings))
    walker: WalkerSettings | None = Field(default_factory=lambda: _load_section(WalkerSettings))
    processor: ProcessorSettings | None = Field(default_factory=lambda: _load_section(ProcessorSettings))

    self_consistency_n: int = Field(default=DEFAULTS["self_consistency_n"], ge=1, validation_alias="CLASSIFIER_N")
    temperature: float = Field(
        default=DEFAULTS["temperature"], ge=0.0, le=1.0, validation_alias="CLASSIFIER_TEMPERATURE"
    )
    confidence_threshold: float = Field(
        default=DEFAULTS["confidence_threshold"], ge=0.0, le=1.0, validation_alias="CLASSIFIER_CONFIDENCE_THRESHOLD"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton (loaded once)."""
    return Settings()  # type: ignore[call-arg]  # values are resolved from env/.env
