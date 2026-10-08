"""Centralised application configuration.

Single source of truth for runtime settings, loaded once from the
environment (and an optional ``.env`` file) via ``pydantic-settings``.
Import :func:`get_settings` wherever configuration is needed rather than
reading ``os.environ`` directly.

:class:`Settings` aggregates every configuration section as an **optional**
nested model: the inference provider (Jev, ADR-0022), the
PostgreSQL state store (``database``), Microsoft Graph (``graph``), the
work queue (``queue``), the walker job (``walker``), and the processor job
(``processor``). A section is ``None`` when its environment is absent and a
validated model when present, so each job supplies only what it uses — the walker
needs ``database``/``graph``/``queue``/``walker`` but no inference credentials,
the processor needs ``database``/``graph``/``queue``/``processor`` plus the
Alembic migrations need only ``database``, and the local CLI needs only the Jev
provider. Jev's credentials are enforced where its client is
built (``classifier.create_classifier``), not at load time, so loading ``Settings``
never demands credentials a job does not use.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Source = Literal["sharepoint", "filesystem"]

DEFAULTS: dict[str, Any] = {
    "source": "sharepoint",
    "graph_use_managed_identity": False,
    "graph_token_scope": "https://graph.microsoft.com/.default",
    "graph_base_url": "https://graph.microsoft.com/v1.0",
    "queue_use_managed_identity": False,
    "walker_root_path": "/Matters",
    "walker_time_budget_seconds": 600,
}


class JevSettings(BaseSettings):
    """Credentials/endpoint for TypeSafe Jev (ADR-0022).

    Parses its own slice of the environment under the ``CLASSIFIER__JEV_``
    prefix. Both ``api_key`` and ``base_url`` are required together: the
    endpoint is only known after early access, so there is no default. A
    *partially* configured section fails loudly at load; a wholly absent one
    resolves to ``None``.
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__JEV_", env_file=".env", extra="ignore")

    api_key: SecretStr | None = None
    base_url: AnyHttpUrl | None = None

    @property
    def is_configured(self) -> bool:
        """True when both the API key and the endpoint are set."""
        return self.api_key is not None and self.base_url is not None

    @model_validator(mode="after")
    def _reject_partial(self) -> "JevSettings":
        """Fail loudly on a half-configured section, so a typo isn't silently ignored."""
        intended = bool(self.api_key or self.base_url)
        if intended and not self.is_configured:
            raise ValueError("Jev requires both CLASSIFIER__JEV_API_KEY and CLASSIFIER__JEV_BASE_URL")
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
    """Walker job settings: which drive to enumerate, from where, and its budget (ADR-0014/0019).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__WALKER_`` prefix. ``drive_id`` identifies the SharePoint
    document library to walk and is the section's one required input, so a job
    that never walks (the local CLI) simply gets ``Settings.walker is None``.
    ``root_path`` scopes the walk to a library subtree at the Graph delta level
    (default ``/Matters``; set it to ``/`` or empty to walk the whole drive) — the
    single, config-driven scoping knob that supersedes the old hard-coded
    ``/Matters`` filter (ADR-0019), and handy for pointing an integration test at
    a small subset of files. ``time_budget_seconds`` bounds one scheduled run so a
    large first enumeration is resumed across slots rather than forced to finish in
    a single slot (default 10 min).
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__WALKER_", env_file=".env", extra="ignore")

    drive_id: str | None = None
    root_path: str = Field(default=DEFAULTS["walker_root_path"])
    time_budget_seconds: int = Field(default=DEFAULTS["walker_time_budget_seconds"], gt=0)

    @property
    def is_configured(self) -> bool:
        """True once a target drive id is present — the walk's one required input."""
        return self.drive_id is not None


class ProcessorSettings(BaseSettings):
    """Processor job settings: where the classifier reads its taxonomy (ADR-0012).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__PROCESSOR_`` prefix. ``category_file`` points at the
    category-definition Markdown the processor loads to build its classifier; it is the
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


class FilesystemSettings(BaseSettings):
    """Local-filesystem source settings: the mounted root to enumerate (ADR-0020).

    Parses its own slice of the environment (and ``.env``) under the
    ``CLASSIFIER__FILESYSTEM_`` prefix. ``root`` is the directory the filesystem
    walker enumerates and the filesystem retrieval seam resolves relative locators
    against; it is the section's one required input, so a job running against
    SharePoint simply gets ``Settings.filesystem is None``. Selected by
    ``CLASSIFIER_SOURCE=filesystem`` — the walker/processor never construct a
    ``GraphClient`` on this path.
    """

    model_config = SettingsConfigDict(env_prefix="CLASSIFIER__FILESYSTEM_", env_file=".env", extra="ignore")

    root: Path | None = None

    @property
    def is_configured(self) -> bool:
        """True once the mounted root directory is present — the source's one required input."""
        return self.root is not None


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
    absent (see :func:`_load_section`). The Jev section's credentials are enforced
    in :func:`~classifier.create_classifier`, not here, so building ``Settings``
    never demands credentials a job does not use. ``source`` (ADR-0020) selects
    whether the walker/processor bind to SharePoint/Graph (``sharepoint``, the
    default) or a mounted directory (``filesystem``, requiring
    :class:`FilesystemSettings`); the wiring branch lives in ``walker.run`` /
    ``processor.run``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    source: Source = Field(default=DEFAULTS["source"], validation_alias="CLASSIFIER_SOURCE")

    jev: JevSettings | None = Field(default_factory=lambda: _load_section(JevSettings))
    database: DatabaseSettings | None = Field(default_factory=lambda: _load_section(DatabaseSettings))
    graph: GraphSettings | None = Field(default_factory=lambda: _load_section(GraphSettings))
    queue: QueueSettings | None = Field(default_factory=lambda: _load_section(QueueSettings))
    walker: WalkerSettings | None = Field(default_factory=lambda: _load_section(WalkerSettings))
    processor: ProcessorSettings | None = Field(default_factory=lambda: _load_section(ProcessorSettings))
    filesystem: FilesystemSettings | None = Field(default_factory=lambda: _load_section(FilesystemSettings))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton (loaded once)."""
    return Settings()  # type: ignore[call-arg]  # values are resolved from env/.env
