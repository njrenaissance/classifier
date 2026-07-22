import pytest
from pydantic import ValidationError

from config import DatabaseSettings, Settings

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
    assert s.anthropic.model == "claude-haiku-4-5"
    assert s.foundry.model == "claude-haiku-4-5"


def test_missing_anthropic_key_errors():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_managed_identity_loads_without_anthropic_key(monkeypatch):
    _foundry_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.provider == "foundry"
    assert s.foundry.resource == "my-resource"
    assert s.foundry.use_managed_identity is True
    assert s.foundry.api_key is None
    assert s.anthropic.api_key is None


def test_foundry_provider_requires_resource(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "foundry")
    monkeypatch.setenv("CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_requires_credential_without_managed_identity(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "foundry")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_api_key_loads_when_present(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "foundry")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fk-123")
    s = Settings(_env_file=None)
    assert s.foundry.api_key.get_secret_value() == "fk-123"
    assert s.foundry.use_managed_identity is False


def test_per_provider_model_overrides(monkeypatch):
    _foundry_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("CLASSIFIER_FOUNDRY_MODEL", "anthropic.claude-haiku-4-5")
    s = Settings(_env_file=None)
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
    assert s.anthropic.api_key.get_secret_value() == "sk-dotenv"
    assert s.self_consistency_n == 9


# --- database settings (ADR-0013) ------------------------------------------


def test_database_url_is_required_with_no_default():
    # A missing DB URL is a loud startup crash, never a silent fallback.
    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_database_url_read_from_env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__DATABASE_URL", "postgresql+psycopg://u:pw@db:5432/prod")
    assert DatabaseSettings().url.get_secret_value() == "postgresql+psycopg://u:pw@db:5432/prod"


def test_database_url_loaded_from_dotenv(tmp_path):
    # Regression: the DB URL must be honored from a .env file, not only real env vars.
    (tmp_path / ".env").write_text("CLASSIFIER__DATABASE_URL=postgresql+psycopg://u:pw@dotenv:5432/db\n")
    assert DatabaseSettings().url.get_secret_value() == "postgresql+psycopg://u:pw@dotenv:5432/db"


def test_database_url_is_kept_secret(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__DATABASE_URL", "postgresql+psycopg://u:pw@db:5432/prod")
    assert "pw" not in repr(DatabaseSettings())
