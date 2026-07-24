"""add sync_state.last_synced_at

Revision ID: 0003_last_synced_at
Revises: 0002_folder_path
Create Date: 2026-07-24

The walker stamps the completion time of each successful walk on
``sync_state.last_synced_at`` (ADR-0014). It is kept distinct from ``updated_at``
— which every write bumps, including an interruption — so it is a true "last
successful sync" marker. Nullable: a library that has never completed a walk has
no value yet. Forward-only, matching the project's migration policy.
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_last_synced_at"
down_revision = "0002_folder_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_state", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Fix-forward only: we never roll a migration backwards (see CLAUDE.md).
    raise NotImplementedError("Downgrades are not supported; fix forward with a new migration.")
