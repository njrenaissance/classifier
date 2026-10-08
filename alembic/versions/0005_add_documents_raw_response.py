"""add documents.raw_response for the complete provider classification response

Revision ID: 0005_raw_response
Revises: 0004_failed_status
Create Date: 2026-10-08

The classifier returns the complete, unmodified provider response alongside
``category`` and ``confidence`` (issue #81, ADR-0022). The processor stores it on
the ``documents`` row so the raw evidence for each label is kept for audit. It is
nullable: rows classified before this migration have no raw response.

Forward-only, matching the project's migration policy.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_raw_response"
down_revision = "0004_failed_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("raw_response", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    # Fix-forward only: we never roll a migration backwards (see CLAUDE.md).
    raise NotImplementedError("Downgrades are not supported; fix forward with a new migration.")
