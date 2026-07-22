"""Pydantic row models exchanged across the persistence seam (ADR-0013).

These are the plain, validated data-transfer objects the pipeline passes to the
DB layer — distinct from the SQLAlchemy ORM models in :mod:`db`, which own the
table mapping. Keeping them separate means a caller can build and validate a
result without importing the ORM or holding a session.

For now this is just :class:`DocumentClassification`, the input to
:meth:`~writer.DatabaseWriter.write`. The queue-message model consumed by the
walker/processor entry points is added by those issues.
"""

from pydantic import BaseModel

from db import DocumentStatus


class DocumentClassification(BaseModel):
    """A classification result to UPSERT into ``documents``.

    Identifies the target row by its unique ``(sync_state_id, drive_item_id)``
    pair and carries the label/confidence to persist. ``status`` defaults to
    :attr:`~db.DocumentStatus.completed` — the terminal state after a successful
    classification.
    """

    sync_state_id: int
    drive_item_id: str
    category: str
    confidence: float
    status: DocumentStatus = DocumentStatus.completed
