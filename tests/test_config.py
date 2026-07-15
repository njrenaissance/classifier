import pytest
from pydantic import ValidationError

from config import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    for var in ("CLASSIFIER_N", "CLASSIFIER_TEMPERATURE", "CLASSIFIER_CONFIDENCE_THRESHOLD"):
        monkeypatch.delenv(var, raising=False)


def test_loads_from_env(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_N", "7")
    monkeypatch.setenv("CLASSIFIER_TEMPERATURE", "0.3")
    monkeypatch.setenv("CLASSIFIER_CONFIDENCE_THRESHOLD", "0.5")
    s = Settings(_env_file=None)
    assert s.anthropic_api_key.get_secret_value() == "sk-test-123"
    assert s.self_consistency_n == 7
    assert s.temperature == 0.3
    assert s.confidence_threshold == 0.5


def test_missing_api_key_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_defaults_applied(monkeypatch):
    _base_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.self_consistency_n == 5
    assert s.temperature == 0.4
    assert s.confidence_threshold == 0.6


@pytest.mark.parametrize("field,value", [
    ("CLASSIFIER_N", "0"),
    ("CLASSIFIER_TEMPERATURE", "1.5"),
    ("CLASSIFIER_TEMPERATURE", "-0.1"),
    ("CLASSIFIER_CONFIDENCE_THRESHOLD", "1.2"),
    ("CLASSIFIER_CONFIDENCE_THRESHOLD", "-0.5"),
])
def test_out_of_bounds_rejected(monkeypatch, field, value):
    _base_env(monkeypatch)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_loads_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-dotenv\nCLASSIFIER_N=9\n")
    s = Settings(_env_file=str(env))
    assert s.anthropic_api_key.get_secret_value() == "sk-dotenv"
    assert s.self_consistency_n == 9
