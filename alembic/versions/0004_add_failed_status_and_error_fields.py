"""add 'failed' document status and documents.error_message/retry_count

Revision ID: 0004_failed_status
Revises: 0003_last_synced_at
Create Date: 2026-07-26

The processor (E6) records a failed classification attempt on the ``documents``
row: it sets ``status='failed'``, stamps ``error_message``, and bumps
``retry_count`` (an observed per-document counter — the queue's ``dequeueCount``
still governs redelivery and poison shedding, ADR-0014). This migration adds the
new enum value and the two columns.

``retry_count`` is NOT NULL with a ``0`` server default so existing rows and
walker-created rows start at zero. ``error_message`` is nullable — only failed
attempts carry one. Forward-only, matching the project's migration policy.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_failed_status"
down_revision = "0003_last_synced_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The new value is only added here (never used in this migration), so PostgreSQL
    # 12+ accepts ``ALTER TYPE ... ADD VALUE`` inside Alembic's transaction.
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'failed'")
    op.add_column("documents", sa.Column("error_message", sa.String(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    # Fix-forward only: we never roll a migration backwards (see CLAUDE.md).
    raise NotImplementedError("Downgrades are not supported; fix forward with a new migration.")
