"""Pydantic row models exchanged across the persistence seam (ADR-0013).

These are the plain, validated data-transfer objects the pipeline passes to the
DB layer — distinct from the SQLAlchemy ORM models in :mod:`db`, which own the
table mapping. Keeping them separate means a caller can build and validate a
result without importing the ORM or holding a session.

Two models live here: :class:`DocumentClassification`, the input to
:meth:`~writer.DatabaseWriter.write`; and :class:`Message`, the queue work item
the walker (producer) enqueues and the processor (consumer) reads (ADR-0014).
"""

from pydantic import AwareDatetime, BaseModel

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


class Message(BaseModel):
    """The walker→processor work item enqueued per changed file (ADR-0014).

    Decoupling the two jobs through an Azure Queue means the message *is* the
    only thing they share (ADR-0012): it carries the identity the processor needs
    to download and classify without a DB read — never the file bytes, which
    exceed the queue's 64 KB cap (ADR-0015). Retry count is Azure Queue's
    ``dequeueCount``, so it is deliberately not a field here.

    The wire format is Pydantic JSON: the walker enqueues ``model_dump_json()``
    and the processor reads ``model_validate_json()``. ``enqueued_at`` is required
    to be timezone-aware (:class:`~pydantic.AwareDatetime`) so a naive stamp is
    rejected at the boundary rather than misread as local time downstream.
    """

    document_id: int
    sync_state_id: int
    drive_id: str
    drive_item_id: str
    file_name: str
    mime_type: str
    matter_folder: str
    content_hash: str
    enqueued_at: AwareDatetime
