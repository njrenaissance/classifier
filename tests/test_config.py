import pytest
from pydantic import ValidationError

from config import Settings

_ALL_VARS = (
    "CLASSIFIER_PROVIDER",
    "ANTHROPIC_API_KEY",
    "CLASSIFIER_ANTHROPIC_MODEL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "CLASSIFIER_FOUNDRY_MODEL",
    "CLASSIFIER_FOUNDRY_TOKEN_SCOPE",
    "CLASSIFIER_N",
    "CLASSIFIER_TEMPERATURE",
    "CLASSIFIER_CONFIDENCE_THRESHOLD",
)

pytestmark = pytest.mark.unit


def _clear_env(monkeypatch):
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def _anthropic_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")


def _foundry_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "foundry")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")


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


def test_missing_anthropic_key_errors(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_provider_loads_without_anthropic_key(monkeypatch):
    _foundry_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.provider == "foundry"
    assert s.foundry.resource == "my-resource"
    assert s.anthropic.api_key is None


def test_foundry_provider_requires_resource(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "foundry")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_api_key_is_optional_for_managed_identity(monkeypatch):
    _foundry_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.foundry.api_key is None


def test_foundry_api_key_loads_when_present(monkeypatch):
    _foundry_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fk-123")
    s = Settings(_env_file=None)
    assert s.foundry.api_key.get_secret_value() == "fk-123"


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


def test_loads_from_dotenv(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-dotenv\nCLASSIFIER_N=9\n")
    s = Settings()
    assert s.anthropic.api_key.get_secret_value() == "sk-dotenv"
    assert s.self_consistency_n == 9
