"""Walker entry point — budgeted delta loop, sync_state, idempotent enqueue (E5).

The producer half of the two-job cloud pipeline (ADR-0012): a scheduled job,
invoked as ``python -m walker``, that enumerates a SharePoint document library via
Microsoft Graph delta queries and enqueues one work item per changed/new file for
the classifier to process (ADR-0014).

Two properties define it:

- **Resumable.** A walk runs under a time budget; a large first enumeration is
  spread across scheduled slots. On budget exhaustion mid-pagination the walker
  persists the current page's ``@odata.nextLink`` as ``resume_token`` and marks
  the walk ``interrupted``. On completion it persists the terminal
  ``@odata.deltaLink`` as ``delta_token`` (only then), clears ``resume_token``,
  marks ``completed`` and stamps ``last_synced_at``. The next walk starts from
  ``resume_token`` > ``delta_token`` > a full enumeration.
- **Idempotent.** A file already ``queued``/``processing`` (in flight) or whose
  content hash is unchanged is never re-enqueued. A hash change rotates the old
  hash into ``previous_hash`` and re-queues; a ``pending`` reset re-queues through
  the same path (a re-classification), always preserving a manual
  ``classification_override``. Files outside ``/Matters/`` are recorded
  ``skipped`` and never enqueued.

The delta pagination, path/hash parsing (``graph_client``), the queue producer
(``message_queue``), and the state-store models (``db``) are reused as-is; this
module owns only the budgeted loop and the enqueue decisions. :class:`Walker` is
the testable core (every boundary — Graph, queue, session, clock — is injected);
:func:`run` is the system boundary that wires them, catches domain failures once,
and converts them into an exit code.
"""

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from db import Document, DocumentStatus, SyncState, WalkStatus, get_sessionmaker
from errors import AppError
from graph_client import GraphClient, content_hash, create_graph_client, folder_path
from message_queue import MessageQueue, create_message_queue
from models import Message

logger = logging.getLogger(__name__)

# A file already queued or processing is "in flight"; re-enqueuing it would
# duplicate work (ADR-0014). This is the walker's cross-walk idempotency guard.
_IN_FLIGHT = frozenset({DocumentStatus.queued, DocumentStatus.processing})

# The library layout the walker ingests: only files under the ``/Matters/`` root
# of the drive are classified; everything else is skipped (ADR-0014). The raw
# Graph path is ``.../root:/Matters/...``, so the check is on the segment after
# the ``root:`` marker.
_LIBRARY_ROOT_MARKER = "root:"
_MATTERS_PREFIX = "/Matters"


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class WalkRequest:
    """The parameters of one walk: which drive to enumerate and its time budget.

    A resolved, non-optional view of :class:`~config.WalkerSettings` that
    :func:`run` builds once the settings are known to be present, so the core
    :class:`Walker` never deals with unconfigured state.
    """

    drive_id: str
    budget_seconds: int


def _is_in_matters(path: str | None) -> bool:
    """True when ``path`` names a location under the drive's ``/Matters/`` root.

    ``path`` is the raw Graph ``parentReference.path``
    (``/drives/{id}/root:/Matters/Smith-2026-001/Discovery``); the part after the
    ``root:`` marker is the drive-relative path. A file directly in ``/Matters``
    or in any descendant matches; ``/MattersArchive`` (a different top folder)
    does not.
    """
    if path is None:
        return False
    _, marker, relative = path.partition(_LIBRARY_ROOT_MARKER)
    if not marker:
        return False
    return relative == _MATTERS_PREFIX or relative.startswith(_MATTERS_PREFIX + "/")


def _mime_type(item: Mapping[str, Any]) -> str | None:
    """Return the driveItem's ``file.mimeType``, or ``None`` if it has none."""
    file_facet = item.get("file")
    if isinstance(file_facet, Mapping):
        mime = file_facet.get("mimeType")
        if isinstance(mime, str):
            return mime
    return None


def _build_message(document: Document, drive_id: str, enqueued_at: datetime) -> Message:
    """Build the queue work item for a just-persisted ``queued`` document row.

    Every field the processor needs is read off the ``documents`` row (populated
    from the driveItem moments earlier), so the message carries the file's
    identity without a further DB read (ADR-0014).
    """
    return Message(
        document_id=document.id,
        sync_state_id=document.sync_state_id,
        drive_id=drive_id,
        drive_item_id=document.drive_item_id,
        file_name=document.file_name or "",
        mime_type=document.mime_type or "",
        content_hash=document.content_hash or "",
        enqueued_at=enqueued_at,
    )


class Walker:
    """A single budgeted, resumable walk of one document library (ADR-0014).

    The Graph client, the queue, the DB session and the clock are all injected so
    the whole decision core is unit-testable without a tenant, a queue, a database
    or real time. One instance performs one walk via :meth:`walk`.
    """

    def __init__(
        self,
        session: Session,
        graph: GraphClient,
        queue: MessageQueue,
        request: WalkRequest,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session = session
        self._graph = graph
        self._queue = queue
        self._drive_id = request.drive_id
        self._budget = timedelta(seconds=request.budget_seconds)
        self._now = now

    def walk(self) -> WalkStatus:
        """Walk the drive's delta under the time budget; return the final status.

        Pages are processed until the delta is exhausted (``completed``) or the
        budget runs out between pages (``interrupted``). The budget is only
        checked when a further page exists, so the terminal page never trips a
        spurious interruption and any persisted ``resume_token`` is a real page.
        """
        sync = self._begin_walk()
        deadline = self._now() + self._budget
        pages = self._graph.iter_delta_pages(self._drive_id, self._start_url(sync))
        while True:
            try:
                page = next(pages)
            except StopIteration as stop:
                return self._complete(sync, stop.value)
            for item in page.items:
                self._process_item(sync, item)
            if page.next_link is not None and self._now() >= deadline:
                return self._interrupt(sync, page.next_link)

    def _begin_walk(self) -> SyncState:
        """Load (or create) this drive's ``sync_state`` and mark it ``walking``."""
        sync = self._session.scalars(select(SyncState).where(SyncState.drive_id == self._drive_id)).one_or_none()
        if sync is None:
            sync = SyncState(drive_id=self._drive_id)
            self._session.add(sync)
        sync.walk_status = WalkStatus.walking
        self._session.commit()
        return sync

    @staticmethod
    def _start_url(sync: SyncState) -> str | None:
        """The delta start URL: ``resume_token`` > ``delta_token`` > full walk."""
        return sync.resume_token or sync.delta_token

    def _complete(self, sync: SyncState, delta_link: str) -> WalkStatus:
        """Record a completed walk: store the deltaLink, clear the resume token."""
        sync.delta_token = delta_link
        sync.resume_token = None
        sync.walk_status = WalkStatus.completed
        sync.last_synced_at = self._now()
        self._session.commit()
        return WalkStatus.completed

    def _interrupt(self, sync: SyncState, resume_link: str) -> WalkStatus:
        """Record a budget-interrupted walk: store the resume token only.

        The ``delta_token`` is deliberately left untouched — storing the deltaLink
        on interruption would advance the sync point past unprocessed pages and
        silently drop changes (ADR-0014).
        """
        sync.resume_token = resume_link
        sync.walk_status = WalkStatus.interrupted
        self._session.commit()
        return WalkStatus.interrupted

    def _process_item(self, sync: SyncState, item: dict[str, Any]) -> None:
        """Apply the enqueue decision for one driveItem (ADR-0014).

        Deletion tombstones, folders and hashless items carry no content signal
        and are ignored. A file outside ``/Matters/`` is recorded ``skipped``; a
        file inside is enqueued only when it is new, changed, or a ``pending``
        re-classification — never when in flight or unchanged.
        """
        if item.get("deleted") is not None:
            return
        new_hash = content_hash(item)
        if new_hash is None:
            return
        document = self._existing_document(sync, item["id"])
        if not _is_in_matters(folder_path(item)):
            self._mark_skipped(sync, item, document)
            return
        if self._should_enqueue(document, new_hash):
            self._queue_document(sync, item, document, new_hash)

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
        """Decide whether a driveItem inside ``/Matters/`` needs (re-)queuing.

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

    def _mark_skipped(self, sync: SyncState, item: dict[str, Any], document: Document | None) -> None:
        """Record a non-``/Matters`` file as ``skipped`` without enqueuing it."""
        if document is None:
            document = Document(sync_state_id=sync.id, drive_item_id=item["id"])
            self._session.add(document)
        document.file_name = item.get("name")
        document.folder_path = folder_path(item)
        document.status = DocumentStatus.skipped
        self._session.commit()

    def _queue_document(self, sync: SyncState, item: dict[str, Any], document: Document | None, new_hash: str) -> None:
        """Persist the row as ``queued`` then enqueue its work item.

        The row is committed *before* the enqueue because a queue send cannot be
        rolled back: committing first means a rare send failure leaves an
        at-most-once gap (a stuck ``queued`` row, recoverable via a ``pending``
        reset), never a duplicate message. The in-flight guard then skips the row
        on any later walk.
        """
        document = self._upsert_queued_row(sync, item, document, new_hash)
        message = _build_message(document, self._drive_id, self._now())
        self._session.commit()
        self._queue.enqueue(message)

    def _upsert_queued_row(
        self, sync: SyncState, item: dict[str, Any], document: Document | None, new_hash: str
    ) -> Document:
        """Insert or update the ``documents`` row to ``queued``, flushing its id.

        On a genuine hash change the previous hash is rotated into
        ``previous_hash``; a ``pending`` reset with an unchanged hash keeps it.
        ``classification_override`` is never touched — the walker must not clobber
        a human decision (ADR-0014).
        """
        if document is None:
            document = Document(sync_state_id=sync.id, drive_item_id=item["id"])
            self._session.add(document)
        elif new_hash != document.content_hash:
            document.previous_hash = document.content_hash
        document.file_name = item.get("name")
        document.mime_type = _mime_type(item)
        document.folder_path = folder_path(item)
        document.content_hash = new_hash
        document.status = DocumentStatus.queued
        self._session.flush()  # assign document.id for the message
        return document


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (the drive and budget come from settings)."""
    return argparse.ArgumentParser(
        prog="walker",
        description="Enumerate a SharePoint drive's delta and enqueue changed files for classification.",
    )


def configure_logging() -> None:
    """Configure stdlib logging once, at the application entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run(argv: list[str]) -> int:
    """Parse ``argv``, run one budgeted walk, and return an exit code.

    Configuration errors (an unconfigured section) are deploy-time programmer
    errors and are left to fail loudly; only domain failures (:class:`AppError`)
    and invalid settings (:class:`ValidationError`) are converted here into a
    clean, logged exit code — the single system boundary (see error-handling
    standard).
    """
    build_parser().parse_args(argv)
    try:
        settings = get_settings()
        walker = settings.walker
        if walker is None or walker.drive_id is None:
            raise ValueError("Walker is not configured; set CLASSIFIER__WALKER_DRIVE_ID.")
        request = WalkRequest(drive_id=walker.drive_id, budget_seconds=walker.time_budget_seconds)
        with create_graph_client() as graph, create_message_queue() as queue, get_sessionmaker()() as session:
            status = Walker(session, graph, queue, request).walk()
    except (AppError, ValidationError):
        logger.exception("Walk failed")
        return 1
    logger.info("Walk finished: drive=%s status=%s", walker.drive_id, status.value)
    return 0


def main() -> None:
    """CLI entry point: configure logging, run, and exit with the status code."""
    configure_logging()
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
