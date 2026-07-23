"""Shared pytest configuration."""

import pytest

from config import get_database_settings, get_graph_settings, get_settings

# Every environment variable any settings section reads. Kept here as the single
# source of truth so each test starts from a known-empty config.
_SETTINGS_ENV_VARS = (
    "CLASSIFIER_PROVIDER",
    "ANTHROPIC_API_KEY",
    "CLASSIFIER_ANTHROPIC_MODEL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY",
    "CLASSIFIER_FOUNDRY_MODEL",
    "CLASSIFIER_FOUNDRY_TOKEN_SCOPE",
    "CLASSIFIER_N",
    "CLASSIFIER_TEMPERATURE",
    "CLASSIFIER_CONFIDENCE_THRESHOLD",
    "CLASSIFIER__DATABASE_URL",
    "CLASSIFIER__GRAPH_TENANT_ID",
    "CLASSIFIER__GRAPH_CLIENT_ID",
    "CLASSIFIER__GRAPH_CLIENT_SECRET",
    "CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY",
    "CLASSIFIER__GRAPH_TOKEN_SCOPE",
    "CLASSIFIER__GRAPH_BASE_URL",
)


def pytest_configure(config):
    """Show live log output at INFO under `pytest -v`, unless a log-cli level was set explicitly."""
    if config.option.verbose > 0 and config.option.log_cli_level is None:
        config.option.log_cli_level = "INFO"


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch, tmp_path):
    """Isolate every test from ambient configuration.

    Clears all settings env vars and switches to an empty working directory so a
    developer's local ``.env`` cannot leak into ``Settings`` construction — the
    nested ``BaseSettings`` sections each read ``.env`` relative to the cwd, which
    a parent ``_env_file=None`` does not disable. Also resets the cached
    ``get_settings`` singleton so one test's settings never bleed into the next.
    """
    for var in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    get_database_settings.cache_clear()
    get_graph_settings.cache_clear()
