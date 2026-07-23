"""replace documents.matter_folder with documents.folder_path

Revision ID: 0002_folder_path
Revises: 0001_initial
Create Date: 2026-07-23

The pipeline classifies by document type, not by matter/case, so a derived
matter folder is no longer stored. The raw ``parentReference.path`` is kept
instead (``folder_path``), from which a matter can be reconstructed later if
needed (ADR-0018). Forward-only, matching the project's migration policy.
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_folder_path"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("folder_path", sa.String(), nullable=True))
    op.drop_column("documents", "matter_folder")


def downgrade() -> None:
    # Fix-forward only: we never roll a migration backwards (see CLAUDE.md).
    raise NotImplementedError("Downgrades are not supported; fix forward with a new migration.")
