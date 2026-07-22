"""Tests for the PostgreSQL state store (ADR-0013).

The engine/session factories are exercised as fast unit tests with the driver
faked. The ORM models and the ``DatabaseWriter`` UPSERT semantics — which depend
on real PostgreSQL ``ON CONFLICT`` behaviour — are exercised as ``integration``
tests against a throwaway Postgres started with ``testcontainers``; those skip
automatically when Docker is unavailable (e.g. a local sandbox).
"""

from collections.abc import Iterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import db
from alembic import command
from config import get_database_settings
from db import Document, DocumentStatus, ProcessingLog, SyncState, WalkStatus
from models import DocumentClassification
from writer import DatabaseWriter

# --- unit: engine/session factories ----------------------------------------


@pytest.mark.unit
def test_get_engine_builds_from_settings_url(mocker, monkeypatch):
    monkeypatch.setenv("CLASSIFIER__DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    get_database_settings.cache_clear()
    db.get_engine.cache_clear()
    fake_engine = mocker.Mock()
    create = mocker.patch("db.create_engine", return_value=fake_engine)

    engine = db.get_engine()

    create.assert_called_once_with("postgresql+psycopg://u:p@h:5432/db", pool_pre_ping=True)
    assert engine is fake_engine
    db.get_engine.cache_clear()
    get_database_settings.cache_clear()


@pytest.mark.unit
def test_get_sessionmaker_is_bound_to_the_engine(mocker):
    db.get_sessionmaker.cache_clear()
    fake_engine = mocker.Mock()
    mocker.patch("db.get_engine", return_value=fake_engine)

    factory = db.get_sessionmaker()

    assert factory.kw["bind"] is fake_engine
    db.get_sessionmaker.cache_clear()


# --- integration: real Postgres round-trip ---------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """A throwaway Postgres with the schema created; skips without Docker."""
    postgres = pytest.importorskip("testcontainers.postgres").PostgresContainer("postgres:16-alpine", driver="psycopg")
    docker_error = pytest.importorskip("docker.errors").DockerException
    try:
        postgres.start()
    except docker_error as exc:
        pytest.skip(f"Docker/Postgres unavailable: {exc}")
    try:
        created = create_engine(postgres.get_connection_url())
        db.Base.metadata.create_all(created)
        yield created
        created.dispose()
    finally:
        postgres.stop()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as active:
        yield active


def _sync_state(session: Session, drive_id: str) -> SyncState:
    row = SyncState(drive_id=drive_id)
    session.add(row)
    session.commit()
    return row


@pytest.mark.integration
def test_models_round_trip(session: Session):
    parent = _sync_state(session, "drive-round-trip")
    document = Document(sync_state_id=parent.id, drive_item_id="item-1", content_hash="abc123")
    session.add(document)
    session.commit()
    session.add(ProcessingLog(document_id=document.id, attempt=1, status="succeeded", input_tokens=42))
    session.commit()

    stored = session.get(SyncState, parent.id)
    assert stored is not None
    assert stored.walk_status is WalkStatus.idle  # server default applied
    fetched = session.scalars(select(Document).where(Document.drive_item_id == "item-1")).one()
    assert fetched.status is DocumentStatus.queued  # server default applied
    assert fetched.content_hash == "abc123"
    log = session.scalars(select(ProcessingLog).where(ProcessingLog.document_id == document.id)).one()
    assert log.input_tokens == 42


@pytest.mark.integration
def test_write_inserts_then_upserts_one_row(session: Session):
    parent = _sync_state(session, "drive-upsert")
    writer = DatabaseWriter(session)

    def classify(category: str, confidence: float) -> DocumentClassification:
        return DocumentClassification(
            sync_state_id=parent.id, drive_item_id="item-x", category=category, confidence=confidence
        )

    writer.write(classify("invoice", 0.6))
    writer.write(classify("receipt", 0.9))

    rows = session.scalars(select(Document).where(Document.sync_state_id == parent.id)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.category == "receipt"
    assert row.confidence == 0.9
    assert row.status is DocumentStatus.completed
    assert row.classified_at is not None
    assert row.processed_at is not None


@pytest.mark.integration
def test_write_never_overwrites_a_manual_override(session: Session):
    parent = _sync_state(session, "drive-override")
    existing = Document(
        sync_state_id=parent.id,
        drive_item_id="item-y",
        category="invoice",
        classification_override="correspondence",
    )
    session.add(existing)
    session.commit()

    DatabaseWriter(session).write(
        DocumentClassification(sync_state_id=parent.id, drive_item_id="item-y", category="receipt", confidence=1.0)
    )

    session.expire_all()
    row = session.scalars(select(Document).where(Document.drive_item_id == "item-y")).one()
    assert row.classification_override == "correspondence"
    assert row.category == "invoice"  # untouched by the writer


# --- integration: migration matches the ORM models -------------------------


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """A fresh Postgres brought up by running the Alembic migration (not create_all).

    Points env.py at the container via ``CLASSIFIER__DATABASE_URL`` and runs
    ``alembic upgrade head``, so this also exercises the real migration path.
    Skips when Docker is unavailable.
    """
    postgres = pytest.importorskip("testcontainers.postgres").PostgresContainer("postgres:16-alpine", driver="psycopg")
    docker_error = pytest.importorskip("docker.errors").DockerException
    try:
        postgres.start()
    except docker_error as exc:
        pytest.skip(f"Docker/Postgres unavailable: {exc}")
    monkeypatch = pytest.MonkeyPatch()
    try:
        url = postgres.get_connection_url()
        monkeypatch.setenv("CLASSIFIER__DATABASE_URL", url)
        command.upgrade(Config("alembic.ini"), "head")
        created = create_engine(url)
        yield created
        created.dispose()
    finally:
        monkeypatch.undo()
        postgres.stop()


def _missing_from_migration(diff: list) -> list:
    """The autogenerate ops that mean a model element is absent from the migration.

    ``compare_metadata`` reports ``add_*`` ops for objects present in the ORM
    metadata but missing from the live (migrated) schema — exactly the drift we
    guard against. Cosmetic type/default differences and DB-only extras are
    ignored, so the check stays robust across dialect representations.
    """
    added = []
    for entry in diff:
        for op in entry if isinstance(entry, list) else [entry]:
            if isinstance(op, tuple) and str(op[0]).startswith("add_"):
                added.append(op[0])
    return added


@pytest.mark.integration
def test_migration_matches_models(migrated_engine: Engine):
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, db.Base.metadata)

    assert _missing_from_migration(diff) == [], f"ORM models declare elements the migration does not create: {diff}"
