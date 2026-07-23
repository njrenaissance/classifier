import pytest
from pydantic import ValidationError

from config import DatabaseSettings, GraphSettings, Settings

pytestmark = pytest.mark.unit

# The ``_isolate_settings_env`` autouse fixture (conftest.py) clears every
# settings env var before each test, so these helpers only set what a case needs.


def _anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")


def _foundry_env(monkeypatch):
    """Foundry with managed identity — the keyless production shape."""
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "foundry")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY", "true")


def test_loads_from_env(monkeypatch):
    _anthropic_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_N", "7")
    monkeypatch.setenv("CLASSIFIER_TEMPERATURE", "0.3")
    monkeypatch.setenv("CLASSIFIER_CONFIDENCE_THRESHOLD", "0.5")
    s = Settings(_env_file=None)
    assert s.provider == "anthropic"
    assert s.anthropic is not None
    assert s.anthropic.api_key.get_secret_value() == "sk-test-123"
    assert s.self_consistency_n == 7
    assert s.temperature == 0.3
    assert s.confidence_threshold == 0.5


def test_defaults_applied(monkeypatch):
    _anthropic_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.provider == "anthropic"
    assert s.self_consistency_n == 5
    assert s.temperature == 0.4
    assert s.confidence_threshold == 0.6
    assert s.anthropic is not None
    assert s.anthropic.model == "claude-haiku-4-5"


def test_unconfigured_sections_are_none():
    # No credentials for anything: Settings loads, every section resolves to None,
    # and the provider requirement is deferred to create_classifier.
    s = Settings(_env_file=None)
    assert s.anthropic is None
    assert s.foundry is None
    assert s.database is None
    assert s.graph is None


def test_foundry_managed_identity_loads_without_anthropic_key(monkeypatch):
    _foundry_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.provider == "foundry"
    assert s.foundry is not None
    assert s.foundry.resource == "my-resource"
    assert s.foundry.use_managed_identity is True
    assert s.foundry.api_key is None
    assert s.anthropic is None


def test_foundry_partial_config_errors(monkeypatch):
    # A resource with no credential is a half-configured section — fail loudly.
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_managed_identity_without_resource_errors(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_api_key_loads_when_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fk-123")
    s = Settings(_env_file=None)
    assert s.foundry is not None
    assert s.foundry.api_key.get_secret_value() == "fk-123"
    assert s.foundry.use_managed_identity is False


def test_per_provider_model_overrides(monkeypatch):
    _anthropic_env(monkeypatch)
    _foundry_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("CLASSIFIER_FOUNDRY_MODEL", "anthropic.claude-haiku-4-5")
    s = Settings(_env_file=None)
    assert s.anthropic is not None
    assert s.foundry is not None
    assert s.anthropic.model == "claude-sonnet-5"
    assert s.foundry.model == "anthropic.claude-haiku-4-5"


def test_unknown_provider_rejected(monkeypatch):
    _anthropic_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "bedrock")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("CLASSIFIER_N", "0", id="n_below_min"),
        pytest.param("CLASSIFIER_TEMPERATURE", "1.5", id="temperature_above_max"),
        pytest.param("CLASSIFIER_TEMPERATURE", "-0.1", id="temperature_below_min"),
        pytest.param("CLASSIFIER_CONFIDENCE_THRESHOLD", "1.2", id="confidence_above_max"),
        pytest.param("CLASSIFIER_CONFIDENCE_THRESHOLD", "-0.5", id="confidence_below_min"),
    ],
)
def test_out_of_bounds_rejected(monkeypatch, field, value):
    _anthropic_env(monkeypatch)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_loads_from_dotenv(tmp_path):
    # The autouse fixture already switched the cwd to this tmp_path.
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\nCLASSIFIER_N=9\n")
    s = Settings()
    assert s.anthropic is not None
    assert s.anthropic.api_key.get_secret_value() == "sk-dotenv"
    assert s.self_consistency_n == 9


# --- database section (ADR-0013) -------------------------------------------


def test_database_section_is_none_without_a_url():
    # An absent DB URL is not an error at load — the section is simply None, so
    # a job that never touches the database need supply nothing.
    assert DatabaseSettings().is_configured is False
    assert Settings(_env_file=None).database is None


def test_database_section_populated_from_env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__DATABASE_URL", "postgresql+psycopg://u:pw@db:5432/prod")
    s = Settings(_env_file=None)
    assert s.database is not None
    assert s.database.url.get_secret_value() == "postgresql+psycopg://u:pw@db:5432/prod"


def test_database_url_loaded_from_dotenv(tmp_path):
    # Regression: the DB URL must be honored from a .env file, not only real env vars.
    (tmp_path / ".env").write_text("CLASSIFIER__DATABASE_URL=postgresql+psycopg://u:pw@dotenv:5432/db\n")
    assert DatabaseSettings().url.get_secret_value() == "postgresql+psycopg://u:pw@dotenv:5432/db"


def test_database_url_is_kept_secret(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__DATABASE_URL", "postgresql+psycopg://u:pw@db:5432/prod")
    assert "pw" not in repr(DatabaseSettings())


# --- graph section (ADR-0007/0015) -----------------------------------------


def test_graph_section_is_none_without_config():
    assert GraphSettings().is_configured is False
    assert Settings(_env_file=None).graph is None


def test_graph_section_managed_identity(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY", "true")
    s = Settings(_env_file=None)
    assert s.graph is not None
    assert s.graph.use_managed_identity is True


def test_graph_section_client_credentials(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__GRAPH_TENANT_ID", "tenant")
    monkeypatch.setenv("CLASSIFIER__GRAPH_CLIENT_ID", "client")
    monkeypatch.setenv("CLASSIFIER__GRAPH_CLIENT_SECRET", "s3cr3t-value")
    s = Settings(_env_file=None)
    assert s.graph is not None
    assert s.graph.tenant_id == "tenant"
    assert s.graph.client_secret.get_secret_value() == "s3cr3t-value"
    assert "s3cr3t-value" not in repr(s.graph)  # SecretStr keeps the value out of repr


def test_graph_partial_config_errors(monkeypatch):
    # A tenant id with no client id/secret is half-configured — fail loudly.
    monkeypatch.setenv("CLASSIFIER__GRAPH_TENANT_ID", "tenant")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
