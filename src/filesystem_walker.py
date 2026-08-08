"""Filesystem walker entry point — full re-enumeration producer (ADR-0020).

The producer half of the two-job pipeline when ``CLASSIFIER_SOURCE=filesystem``:
enumerate a mounted directory, hash each supported file, and enqueue the new or
changed ones for the processor — the filesystem counterpart of the Graph delta
:class:`~walker.Walker`, sharing its enqueue/idempotency core via
:class:`~enqueuer.Enqueuer`.

Unlike the delta walker there is **no** delta/resume token: a filesystem has no
change feed, so every run is a **full re-enumeration**. Idempotency comes entirely
from the shared content-hash decision — an unchanged file's hash matches the stored
one and is skipped, so re-walking an unchanged tree enqueues nothing. The synthetic
:class:`~db.SyncState` row (keyed ``filesystem:<root>``) exists only to satisfy the
``documents`` foreign key and to record ``last_synced_at``; its ``delta_token`` /
``resume_token`` stay ``NULL``.

Each file becomes a :class:`~enqueuer.DocumentCandidate` whose ``drive_item_id`` is
the file's path **relative to the root** (the stable identity and the message's
locator), ``content_hash`` is the ``sha256`` of its bytes (:func:`~content_source.hash_bytes`
— the same function the processor's :class:`~content_source.FilesystemContentSource`
re-checks with), and ``mime_type`` is derived from the suffix. Enumeration and the
supported-suffix filter are reused from :class:`~sources.LocalFileSystemSource`
(ADR-0010), so registering a new format stays a single change in ``extraction``.

:class:`FilesystemWalker` is the testable core (session, queue and clock injected);
:func:`run` in :mod:`walker` selects it from configuration.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from content_source import hash_bytes
from db import SyncState, WalkStatus
from enqueuer import DocumentCandidate, Enqueuer, _utc_now
from errors import SourceError
from extraction import mime_type_for_suffix
from message_queue import MessageQueue
from models import MessageSource
from sources import LocalFileSystemSource

logger = logging.getLogger(__name__)


class FilesystemWalker:
    """A single full re-enumeration of a mounted directory (ADR-0020).

    The DB session, the queue and the clock are injected so the whole producer is
    unit-testable without a database, a queue or real time. One instance performs one
    walk via :meth:`walk`; the shared :class:`~enqueuer.Enqueuer` owns the (re-)queue
    decision, so idempotency is identical to the Graph walker's.
    """

    def __init__(
        self,
        session: Session,
        queue: MessageQueue,
        root: Path,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session = session
        self._root = root
        self._drive_id = f"filesystem:{root.resolve().as_posix()}"
        self._now = now
        self._enqueuer = Enqueuer(session, queue, now=now)

    def walk(self) -> WalkStatus:
        """Enumerate the root and enqueue each new/changed file; return ``completed``.

        A full re-enumeration always runs to the end (no time budget, no resume): the
        hash-based decision in the shared enqueuer suppresses unchanged files.
        """
        sync = self._begin_walk()
        for path in LocalFileSystemSource(self._root).documents():
            self._enqueuer.enqueue_if_needed(sync, self._drive_id, MessageSource.filesystem, self._candidate(path))
        return self._complete(sync)

    def _begin_walk(self) -> SyncState:
        """Load (or create) the synthetic ``sync_state`` for this root; mark ``walking``."""
        sync = self._session.scalars(select(SyncState).where(SyncState.drive_id == self._drive_id)).one_or_none()
        if sync is None:
            sync = SyncState(drive_id=self._drive_id)
            self._session.add(sync)
        sync.walk_status = WalkStatus.walking
        self._session.commit()
        return sync

    def _complete(self, sync: SyncState) -> WalkStatus:
        """Record a completed re-enumeration: mark ``completed`` and stamp ``last_synced_at``.

        The delta/resume tokens are never touched — a filesystem has no delta point.
        """
        sync.walk_status = WalkStatus.completed
        sync.last_synced_at = self._now()
        self._session.commit()
        return WalkStatus.completed

    def _candidate(self, path: Path) -> DocumentCandidate:
        """Build a source-neutral candidate from one enumerated file.

        ``drive_item_id`` is the POSIX path relative to the root (stable across runs
        and the processor's locator); ``folder_path`` is the relative parent, or
        ``None`` at the root; ``content_hash`` is the ``sha256`` of the file bytes.
        """
        relative = path.relative_to(self._root)
        parent = relative.parent
        return DocumentCandidate(
            drive_item_id=relative.as_posix(),
            content_hash=hash_bytes(self._read_bytes(path)),
            file_name=path.name,
            mime_type=mime_type_for_suffix(path.suffix),
            folder_path=None if parent == Path() else parent.as_posix(),
        )

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        """Read a file's bytes, translating an I/O failure into a domain error."""
        try:
            return path.read_bytes()
        except OSError as err:
            raise SourceError(f"Cannot read document file: {path}") from err
