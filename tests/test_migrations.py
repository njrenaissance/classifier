import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load_migration(filename: str):
    spec = importlib.util.spec_from_file_location(filename, VERSIONS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "filename",
    [pytest.param(path.name, id=path.stem) for path in sorted(VERSIONS_DIR.glob("*.py"))],
)
def test_every_downgrade_refuses_to_run(filename):
    # Fix-forward only (CLAUDE.md): no migration may be rolled back.
    migration = _load_migration(filename)
    with pytest.raises(NotImplementedError):
        migration.downgrade()


def test_raw_response_migration_is_chained_after_failed_status():
    migration = _load_migration("0005_add_documents_raw_response.py")
    assert migration.revision == "0005_raw_response"
    assert migration.down_revision == "0004_failed_status"
