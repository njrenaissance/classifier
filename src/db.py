"""PostgreSQL persistence: engine/session factory + SQLAlchemy models (ADR-0013).

The cloud pipeline (ADR-0012) needs durable, queryable, concurrently-written
state that a per-run CSV cannot provide. This module owns that state store:

- the declarative :class:`Base` and the three ORM models — :class:`SyncState`
  (walker position per document library), :class:`Document` (one row per file:
  identity, content hash, processing status, classification result), and
  :class:`ProcessingLog` (per-attempt audit trail with token/cost accounting);
- lazily-built :func:`get_engine` / :func:`get_sessionmaker` singletons that read
  the connection URL from :class:`~config.Settings`.

The schema is designed here (ADR-0014 specifies column *semantics* in prose, not
DDL). The classifier UPSERTs a result into ``documents`` keyed on the unique
``(sync_state_id, drive_item_id)`` pair — see :class:`~writer.DatabaseWriter`.

The engine is built only when the DB is first used, so the local CSV CLI path
(ADR-0004) never opens a Postgres connection.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import get_database_settings


class WalkStatus(StrEnum):
    """Lifecycle of a walker's enumeration of one document library (ADR-0014)."""

    idle = "idle"
    walking = "walking"
    interrupted = "interrupted"
    completed = "completed"


class DocumentStatus(StrEnum):
    """Per-file processing state used for enqueue/skip logic (ADR-0014)."""

    queued = "queued"
    processing = "processing"
    completed = "completed"
    skipped = "skipped"
    pending = "pending"


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the state store."""


class SyncState(Base):
    """Walker position for one document library — two-token resume + status.

    ``delta_token`` (``@odata.deltaLink``) is set only when a walk completes;
    ``resume_token`` (``@odata.nextLink``) is set when a walk is interrupted. The
    next walk's start point is ``resume_token`` > ``delta_token`` > full
    enumeration (ADR-0014).
    """

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drive_id: Mapped[str] = mapped_column(unique=True)
    delta_token: Mapped[str | None] = mapped_column(default=None)
    resume_token: Mapped[str | None] = mapped_column(default=None)
    walk_status: Mapped[WalkStatus] = mapped_column(
        SAEnum(WalkStatus, name="walk_status"),
        default=WalkStatus.idle,
        server_default=WalkStatus.idle.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Document(Base):
    """One row per file: identity, change-detection hashes, status, and result.

    The unique ``(sync_state_id, drive_item_id)`` pair is the walker's enqueue key
    and the classifier's UPSERT conflict target (ADR-0013). ``content_hash`` is the
    precise change signal; on a change the walker rotates the old value into
    ``previous_hash``. ``classification_override`` is a manual label that the
    classifier must **never** overwrite (ADR-0014); ``classified_by`` records the
    model id (``classifier.MODEL``, ADR-0002).
    """

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("sync_state_id", "drive_item_id", name="uq_documents_sync_state_drive_item"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sync_state_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sync_state.id"))
    drive_item_id: Mapped[str] = mapped_column()

    file_name: Mapped[str | None] = mapped_column(default=None)
    mime_type: Mapped[str | None] = mapped_column(default=None)
    matter_folder: Mapped[str | None] = mapped_column(default=None)

    content_hash: Mapped[str | None] = mapped_column(default=None)
    previous_hash: Mapped[str | None] = mapped_column(default=None)

    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, name="document_status"),
        default=DocumentStatus.queued,
        server_default=DocumentStatus.queued.value,
    )

    category: Mapped[str | None] = mapped_column(default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    classification_override: Mapped[str | None] = mapped_column(default=None)
    classified_by: Mapped[str | None] = mapped_column(default=None)

    graph_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProcessingLog(Base):
    """Per-attempt audit trail for one document, with token/cost accounting.

    One row per classification attempt (retries append rows rather than mutating
    the ``documents`` result), capturing the outcome ``status``, the produced
    label/confidence, any error, and the tokens/cost the call consumed (ADR-0002
    pricing).
    """

    __tablename__ = "processing_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), index=True)
    attempt: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column()
    category: Mapped[str | None] = mapped_column(default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    input_tokens: Mapped[int | None] = mapped_column(default=None)
    output_tokens: Mapped[int | None] = mapped_column(default=None)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy :class:`~sqlalchemy.engine.Engine`.

    Built lazily on first use from ``DatabaseSettings.url`` with
    ``pool_pre_ping`` so stale pooled connections (a stopped Burstable instance —
    ADR-0013) are detected and recycled rather than surfacing mid-query.
    """
    url = get_database_settings().url.get_secret_value()
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory bound to :func:`get_engine`."""
    return sessionmaker(bind=get_engine())
