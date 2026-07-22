"""initial state-store schema: sync_state, documents, processing_log

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-22

Creates the three tables of the PostgreSQL state store (ADR-0013): the walker's
``sync_state``, the per-file ``documents`` row (with the unique
``(sync_state_id, drive_item_id)`` UPSERT key), and the ``processing_log`` audit
trail. Hand-authored to match the ORM models in ``src/db.py``.
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

WALK_STATUS = sa.Enum("idle", "walking", "interrupted", "completed", name="walk_status")
DOCUMENT_STATUS = sa.Enum("queued", "processing", "completed", "skipped", "pending", name="document_status")


def upgrade() -> None:
    op.create_table(
        "sync_state",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("drive_id", sa.String(), nullable=False),
        sa.Column("delta_token", sa.String(), nullable=True),
        sa.Column("resume_token", sa.String(), nullable=True),
        sa.Column("walk_status", WALK_STATUS, server_default="idle", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_id"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("sync_state_id", sa.BigInteger(), nullable=False),
        sa.Column("drive_item_id", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("matter_folder", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("previous_hash", sa.String(), nullable=True),
        sa.Column("status", DOCUMENT_STATUS, server_default="queued", nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("classification_override", sa.String(), nullable=True),
        sa.Column("classified_by", sa.String(), nullable=True),
        sa.Column("graph_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sync_state_id"], ["sync_state.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_state_id", "drive_item_id", name="uq_documents_sync_state_drive_item"),
    )
    op.create_table(
        "processing_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_log_document_id", "processing_log", ["document_id"])


def downgrade() -> None:
    # Fix-forward only: we never roll a migration backwards (see CLAUDE.md).
    raise NotImplementedError("Downgrades are not supported; fix forward with a new migration.")
