"""Shared enqueue core for the pipeline's producers (ADR-0020).

The source-neutral half of a walk: given a :class:`DocumentCandidate` — one file's
identity, content hash, and metadata, however it was discovered — decide whether it
needs (re-)queuing and, if so, UPSERT its ``documents`` row to ``queued`` and enqueue
the work item. Both producers depend on this collaborator so their idempotency is
byte-for-byte identical: the Graph delta :class:`~walker.Walker` (ADR-0014) and the
filesystem :class:`~filesystem_walker.FilesystemWalker` (ADR-0020).

The decision rules and the **commit-before-enqueue** ordering are exactly those the
walker owned previously (extracted here without behaviour change): a queue send cannot
be rolled back, so the row is committed *before* the send — a rare send failure then
leaves an at-most-once gap (a stuck ``queued`` row, recoverable via a ``pending``
reset), never a duplicate message. A manual ``classification_override`` is never
touched.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Document, DocumentStatus, SyncState
from message_queue import MessageQueue
from models import Message, MessageSource

# A file already queued or processing is "in flight"; re-enqueuing it would
# duplicate work (ADR-0014). This is the cross-walk idempotency guard.
_IN_FLIGHT = frozenset({DocumentStatus.queued, DocumentStatus.processing})


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class DocumentCandidate:
    """One discovered file's source-neutral identity + metadata for enqueue decisions.

    ``drive_item_id`` is the per-source stable identity and the message's locator: a
    Graph item id for SharePoint, a root-relative path for filesystem (ADR-0020). The
    remaining fields populate the ``documents`` row and the queue message.
    """

    drive_item_id: str
    content_hash: str
    file_name: str | None
    mime_type: str | None
    folder_path: str | None


class Enqueuer:
    """Applies the (re-)queue decision + persistence + enqueue for one candidate.

    The DB session and the queue are injected so the whole decision core is
    unit-testable without a database or a real queue. Stateless across calls beyond
    those collaborators; a producer holds one instance and feeds it candidates.
    """

    def __init__(self, session: Session, queue: MessageQueue, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._session = session
        self._queue = queue
        self._now = now

    def enqueue_if_needed(
        self, sync: SyncState, drive_id: str, source: MessageSource, candidate: DocumentCandidate
    ) -> None:
        """Enqueue ``candidate`` only if it is new, changed, or a ``pending`` reset.

        Looks up the existing ``documents`` row for ``(sync, drive_item_id)`` and
        applies :meth:`_should_enqueue`; an in-flight or unchanged file is left alone.
        """
        document = self._existing_document(sync, candidate.drive_item_id)
        if self._should_enqueue(document, candidate.content_hash):
            self._queue_document(sync, drive_id, source, candidate, document)

    def _existing_document(self, sync: SyncState, drive_item_id: str) -> Document | None:
        """Return the ``documents`` row for ``(sync, drive_item_id)``, or ``None``."""
        return self._session.scalars(
            select(Document).where(
                Document.sync_state_id == sync.id,
                Document.drive_item_id == drive_item_id,
            )
        ).one_or_none()

    @staticmethod
    def _should_enqueue(document: Document | None, new_hash: str) -> bool:
        """Decide whether a candidate needs (re-)queuing.

        New file → yes; in flight → no; ``pending`` reset → yes (re-classify);
        otherwise only when the content hash changed.
        """
        if document is None:
            return True
        if document.status in _IN_FLIGHT:
            return False
        if document.status is DocumentStatus.pending:
            return True
        return new_hash != document.content_hash

    def _queue_document(
        self,
        sync: SyncState,
        drive_id: str,
        source: MessageSource,
        candidate: DocumentCandidate,
        document: Document | None,
    ) -> None:
        """Persist the row as ``queued`` then enqueue its work item (commit-before-send).

        The row is committed *before* the enqueue because a queue send cannot be
        rolled back: committing first means a rare send failure leaves an at-most-once
        gap (a stuck ``queued`` row, recoverable via a ``pending`` reset), never a
        duplicate message. The in-flight guard then skips the row on any later walk.
        """
        document = self._upsert_queued_row(sync, candidate, document)
        message = self._build_message(document, drive_id, source, self._now())
        self._session.commit()
        self._queue.enqueue(message)

    def _upsert_queued_row(self, sync: SyncState, candidate: DocumentCandidate, document: Document | None) -> Document:
        """Insert or update the ``documents`` row to ``queued``, flushing its id.

        On a genuine hash change the previous hash is rotated into ``previous_hash``;
        a ``pending`` reset with an unchanged hash keeps it.
        ``classification_override`` is never touched — a producer must not clobber a
        human decision (ADR-0014).
        """
        if document is None:
            document = Document(sync_state_id=sync.id, drive_item_id=candidate.drive_item_id)
            self._session.add(document)
        elif candidate.content_hash != document.content_hash:
            document.previous_hash = document.content_hash
        document.file_name = candidate.file_name
        document.mime_type = candidate.mime_type
        document.folder_path = candidate.folder_path
        document.content_hash = candidate.content_hash
        document.status = DocumentStatus.queued
        self._session.flush()  # assign document.id for the message
        return document

    @staticmethod
    def _build_message(document: Document, drive_id: str, source: MessageSource, enqueued_at: datetime) -> Message:
        """Build the queue work item for a just-persisted ``queued`` document row.

        Every field the processor needs is read off the ``documents`` row (populated
        moments earlier), so the message carries the file's identity without a further
        DB read (ADR-0014).
        """
        return Message(
            source=source,
            document_id=document.id,
            sync_state_id=document.sync_state_id,
            drive_id=drive_id,
            drive_item_id=document.drive_item_id,
            file_name=document.file_name or "",
            mime_type=document.mime_type or "",
            content_hash=document.content_hash or "",
            enqueued_at=enqueued_at,
        )
