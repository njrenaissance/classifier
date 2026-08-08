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
  ``classification_override``.

The walk is scoped to a configurable library subtree (``WalkerSettings.root_path``,
default ``/Matters``) at the Graph delta level, so the walker sees only in-scope
items — the single config-driven scoping knob that supersedes the old hard-coded
``/Matters`` post-walk filter (ADR-0019).

The delta pagination, path/hash parsing (``graph_client``), the queue producer
(``message_queue``), and the state-store models (``db``) are reused as-is; the
new/changed/in-flight/pending enqueue decision and the commit-before-enqueue
persistence live in the shared source-neutral :class:`~enqueuer.Enqueuer`
(ADR-0020), so this module owns only the budgeted delta loop and turning each
driveItem into an :class:`~enqueuer.DocumentCandidate`. :class:`Walker` is the
testable core (every boundary — Graph, queue, session, clock — is injected);
:func:`run` is the system boundary that wires them, catches domain failures once,
and converts them into an exit code.
"""

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Settings, get_settings
from db import SyncState, WalkStatus, get_sessionmaker
from enqueuer import DocumentCandidate, Enqueuer, _utc_now
from errors import AppError
from filesystem_walker import FilesystemWalker
from graph_client import GraphClient, content_hash, create_graph_client, folder_path
from message_queue import MessageQueue, create_message_queue
from models import MessageSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkRequest:
    """The parameters of one walk: which drive, from where, and its time budget.

    A resolved, non-optional view of :class:`~config.WalkerSettings` that
    :func:`run` builds once the settings are known to be present, so the core
    :class:`Walker` never deals with unconfigured state. ``root_path`` scopes the
    walk to a library subtree at the Graph delta level (ADR-0019).
    """

    drive_id: str
    root_path: str
    budget_seconds: int


def _mime_type(item: Mapping[str, Any]) -> str | None:
    """Return the driveItem's ``file.mimeType``, or ``None`` if it has none."""
    file_facet = item.get("file")
    if isinstance(file_facet, Mapping):
        mime = file_facet.get("mimeType")
        if isinstance(mime, str):
            return mime
    return None


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
        self._drive_id = request.drive_id
        self._root_path = request.root_path
        self._budget = timedelta(seconds=request.budget_seconds)
        self._now = now
        self._enqueuer = Enqueuer(session, queue, now=now)

    def walk(self) -> WalkStatus:
        """Walk the drive's delta under the time budget; return the final status.

        Pages are processed until the delta is exhausted (``completed``) or the
        budget runs out between pages (``interrupted``). The budget is only
        checked when a further page exists, so the terminal page never trips a
        spurious interruption and any persisted ``resume_token`` is a real page.
        """
        sync = self._begin_walk()
        deadline = self._now() + self._budget
        pages = self._graph.iter_delta_pages(self._drive_id, self._start_url(sync), root_path=self._root_path)
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
        """Build a candidate from one driveItem and hand it to the shared enqueuer (ADR-0014/0019).

        Deletion tombstones, folders and hashless items carry no content signal
        and are ignored. The walk is already scoped to the configured subtree at
        the Graph level, so every remaining file is in scope; the source-neutral
        :class:`~enqueuer.Enqueuer` then applies the new/changed/in-flight/pending
        decision (identical to the filesystem producer, ADR-0020).
        """
        if item.get("deleted") is not None:
            return
        new_hash = content_hash(item)
        if new_hash is None:
            return
        candidate = DocumentCandidate(
            drive_item_id=item["id"],
            content_hash=new_hash,
            file_name=item.get("name"),
            mime_type=_mime_type(item),
            folder_path=folder_path(item),
        )
        self._enqueuer.enqueue_if_needed(sync, self._drive_id, MessageSource.sharepoint, candidate)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (the drive and budget come from settings)."""
    return argparse.ArgumentParser(
        prog="walker",
        description="Enumerate a SharePoint drive's delta and enqueue changed files for classification.",
    )


def configure_logging() -> None:
    """Configure stdlib logging once, at the application entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _run_sharepoint_walk(settings: Settings) -> tuple[WalkStatus, str]:
    """Wire and run one Graph delta walk; return its status and a log label (ADR-0014)."""
    walker = settings.walker
    if walker is None or walker.drive_id is None:
        raise ValueError("Walker is not configured; set CLASSIFIER__WALKER_DRIVE_ID.")
    request = WalkRequest(
        drive_id=walker.drive_id,
        root_path=walker.root_path,
        budget_seconds=walker.time_budget_seconds,
    )
    with create_graph_client() as graph, create_message_queue() as queue, get_sessionmaker()() as session:
        status = Walker(session, graph, queue, request).walk()
    return status, f"drive={walker.drive_id}"


def _run_filesystem_walk(settings: Settings) -> tuple[WalkStatus, str]:
    """Wire and run one filesystem re-enumeration; return its status and a log label (ADR-0020).

    The filesystem source needs no Graph credentials and no ``drive_id`` — only the
    mounted root — so no :class:`~graph_client.GraphClient` is constructed here.
    """
    filesystem = settings.filesystem
    if filesystem is None or filesystem.root is None:
        raise ValueError("Filesystem source is not configured; set CLASSIFIER__FILESYSTEM_ROOT.")
    with create_message_queue() as queue, get_sessionmaker()() as session:
        status = FilesystemWalker(session, queue, filesystem.root).walk()
    return status, f"root={filesystem.root}"


def run(argv: list[str]) -> int:
    """Parse ``argv``, run one walk against the configured source, and return an exit code.

    ``CLASSIFIER_SOURCE`` selects the producer: the Graph delta walker (default) or
    the filesystem re-enumeration (ADR-0020). Configuration errors (an unconfigured
    section) are deploy-time programmer errors and are left to fail loudly; only
    domain failures (:class:`AppError`) and invalid settings (:class:`ValidationError`)
    are converted here into a clean, logged exit code — the single system boundary
    (see error-handling standard).
    """
    build_parser().parse_args(argv)
    try:
        settings = get_settings()
        if settings.source == "filesystem":
            status, label = _run_filesystem_walk(settings)
        else:
            status, label = _run_sharepoint_walk(settings)
    except (AppError, ValidationError):
        logger.exception("Walk failed")
        return 1
    logger.info("Walk finished: %s status=%s", label, status.value)
    return 0


def main() -> None:
    """CLI entry point: configure logging, run, and exit with the status code."""
    configure_logging()
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
