import pytest
from pydantic import ValidationError

from config import (
    BlobSettings,
    DatabaseSettings,
    FilesystemSettings,
    GraphSettings,
    QueueSettings,
    Settings,
    WalkerSettings,
)

pytestmark = pytest.mark.unit

# The ``_isolate_settings_env`` autouse fixture (conftest.py) clears every
# settings env var before each test, so these helpers only set what a case needs.


def _jev_env(monkeypatch, *, api_key="jev-key", base_url="https://jev.example.com/v1"):
    monkeypatch.setenv("CLASSIFIER__JEV_API_KEY", api_key)
    monkeypatch.setenv("CLASSIFIER__JEV_BASE_URL", base_url)


def test_loads_with_no_environment():
    # Nothing configured: Settings loads and every section resolves to None.
    # Jev's requirement is deferred to create_classifier.
    s = Settings(_env_file=None)
    assert s.jev is None
    assert s.database is None
    assert s.graph is None


def test_source_and_jev_load_from_env(monkeypatch):
    _jev_env(monkeypatch)
    monkeypatch.setenv("CLASSIFIER_SOURCE", "filesystem")
    s = Settings(_env_file=None)
    assert s.source == "filesystem"
    assert s.jev is not None
    assert s.jev.api_key.get_secret_value() == "jev-key"


def test_claude_era_settings_are_no_longer_read(monkeypatch):
    # Claude/Foundry and the self-consistency knobs were removed (ADR-0005 amended, ADR-0022):
    # their variables have no effect and no field carries them.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ignored")
    monkeypatch.setenv("CLASSIFIER_N", "7")
    monkeypatch.setenv("CLASSIFIER_PROVIDER", "anthropic")
    s = Settings(_env_file=None)
    assert not hasattr(s, "anthropic")
    assert not hasattr(s, "self_consistency_n")
    assert not hasattr(s, "provider")


def test_loads_from_dotenv(tmp_path):
    # The autouse fixture already switched the cwd to this tmp_path.
    (tmp_path / ".env").write_text(
        "CLASSIFIER__JEV_API_KEY=jev-dotenv\nCLASSIFIER__JEV_BASE_URL=https://j.example.com\n"
    )
    s = Settings()
    assert s.jev is not None
    assert s.jev.api_key.get_secret_value() == "jev-dotenv"


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


def test_queue_section_is_none_without_config():
    assert QueueSettings().is_configured is False
    assert Settings(_env_file=None).queue is None


def test_queue_section_connection_string(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__QUEUE_NAME", "work-items")
    monkeypatch.setenv("CLASSIFIER__QUEUE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=x")
    s = Settings(_env_file=None)
    assert s.queue is not None
    assert s.queue.name == "work-items"
    assert s.queue.connection_string.get_secret_value() == "DefaultEndpointsProtocol=https;AccountName=x"
    assert "AccountName=x" not in repr(s.queue)  # SecretStr keeps the value out of repr


def test_queue_section_managed_identity(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__QUEUE_NAME", "work-items")
    monkeypatch.setenv("CLASSIFIER__QUEUE_ACCOUNT_URL", "https://acct.queue.core.windows.net")
    monkeypatch.setenv("CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY", "true")
    s = Settings(_env_file=None)
    assert s.queue is not None
    assert s.queue.use_managed_identity is True
    assert s.queue.account_url == "https://acct.queue.core.windows.net"


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"CLASSIFIER__QUEUE_NAME": "work-items"}, id="name_without_any_credential"),
        pytest.param({"CLASSIFIER__QUEUE_CONNECTION_STRING": "conn"}, id="connection_string_without_a_queue_name"),
        pytest.param(
            {"CLASSIFIER__QUEUE_NAME": "work-items", "CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY": "true"},
            id="managed_identity_without_an_account_url",
        ),
    ],
)
def test_queue_partial_config_errors(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- walker section (ADR-0014/0019) ----------------------------------------


def test_walker_section_is_none_without_a_drive_id():
    assert WalkerSettings().is_configured is False
    assert Settings(_env_file=None).walker is None


def test_walker_section_configured_by_drive_id(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__WALKER_DRIVE_ID", "b!drive-1")
    s = Settings(_env_file=None)
    assert s.walker is not None
    assert s.walker.drive_id == "b!drive-1"


def test_walker_root_path_defaults_to_matters(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__WALKER_DRIVE_ID", "b!drive-1")
    s = Settings(_env_file=None)
    assert s.walker is not None
    assert s.walker.root_path == "/Matters"


def test_walker_root_path_overrides_from_env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER__WALKER_DRIVE_ID", "b!drive-1")
    monkeypatch.setenv("CLASSIFIER__WALKER_ROOT_PATH", "/Matters/Smith-2026-001")
    s = Settings(_env_file=None)
    assert s.walker is not None
    assert s.walker.root_path == "/Matters/Smith-2026-001"


# --- source toggle + filesystem section (ADR-0020) -------------------------


def test_source_defaults_to_sharepoint():
    assert Settings(_env_file=None).source == "sharepoint"


def test_source_overrides_from_env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_SOURCE", "filesystem")
    assert Settings(_env_file=None).source == "filesystem"


def test_invalid_source_rejected(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_SOURCE", "sharepont")  # typo
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_filesystem_section_is_none_without_a_root():
    assert FilesystemSettings().is_configured is False
    assert Settings(_env_file=None).filesystem is None


def test_filesystem_section_configured_by_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CLASSIFIER__FILESYSTEM_ROOT", str(tmp_path))
    s = Settings(_env_file=None)
    assert s.filesystem is not None
    assert s.filesystem.root == tmp_path


def test_jev_section_is_none_without_config():
    s = Settings(_env_file=None)
    assert s.jev is None


def test_jev_section_loads_from_env(monkeypatch):
    _jev_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.jev is not None
    assert str(s.jev.base_url) == "https://jev.example.com/v1"


def test_jev_api_key_is_kept_secret(monkeypatch):
    _jev_env(monkeypatch, api_key="jev-secret-123")
    s = Settings(_env_file=None)
    assert s.jev is not None
    assert "jev-secret-123" not in repr(s.jev)
    assert s.jev.api_key.get_secret_value() == "jev-secret-123"


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({"CLASSIFIER__JEV_API_KEY": "jev-key"}, id="key_without_base_url"),
        pytest.param({"CLASSIFIER__JEV_BASE_URL": "https://jev.example.com/v1"}, id="base_url_without_key"),
    ],
)
def test_jev_partial_config_errors(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_jev_base_url_must_be_a_url(monkeypatch):
    _jev_env(monkeypatch, base_url="not a url")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_blob_section_is_none_without_a_container():
    assert BlobSettings().is_configured is False
    assert Settings(_env_file=None).blob is None


def test_blob_source_is_selected_and_its_section_configured_from_env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_SOURCE", "blob")
    monkeypatch.setenv("CLASSIFIER__BLOB_ACCOUNT_URL", "https://acct.blob.core.windows.net")
    monkeypatch.setenv("CLASSIFIER__BLOB_CONTAINER", "matters")
    s = Settings(_env_file=None)
    assert s.source == "blob"
    assert s.blob is not None
    assert s.blob.container == "matters"
    assert s.blob.prefix == ""
