"""Blob walker — full re-enumeration producer for an Azure Blob container (issue #70, ADR-0023).

The blob counterpart of :class:`~filesystem_walker.FilesystemWalker` (ADR-0020): when
``CLASSIFIER_SOURCE=blob``, list every blob under the configured prefix, decide its
content hash, and enqueue the new or changed ones through the shared
:class:`~enqueuer.Enqueuer`. There is no delta or resume token — blob listing has no
delta point here — so every run is a full re-enumeration, and idempotency comes from
the shared content-hash decision.

A blob's ``drive_item_id`` is its name relative to the prefix, and its ``content_hash``
is the stored ``sha256`` metadata (:func:`~blob_source.stored_sha256`) or, when absent,
the ``sha256`` of its downloaded bytes — the same rule the processor's
:class:`~blob_source.BlobContentSource` re-checks with. Only blobs with a supported
suffix are enqueued, matching the filesystem source (ADR-0010).

:class:`BlobWalker` is the testable core (container, session, queue and clock injected);
:func:`~walker.run` selects it from configuration.
"""

import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import PurePosixPath

from azure.storage.blob import BlobProperties, ContainerClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from blob_source import BlobLocation, read_blob, stored_sha256
from content_source import hash_bytes
from db import SyncState, WalkStatus
from enqueuer import DocumentCandidate, Enqueuer, _utc_now
from extraction import mime_type_for_suffix, supported_suffixes
from message_queue import MessageQueue
from models import MessageSource

logger = logging.getLogger(__name__)


class BlobWalker:
    """A single full re-enumeration of one blob container prefix (issue #70, ADR-0023).

    One instance performs one walk via :meth:`walk`; the shared
    :class:`~enqueuer.Enqueuer` owns the (re-)queue decision, so idempotency is
    identical to the other producers'.
    """

    def __init__(
        self,
        session: Session,
        queue: MessageQueue,
        container: ContainerClient,
        location: BlobLocation,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session = session
        self._container = container
        self._location = location
        self._now = now
        self._enqueuer = Enqueuer(session, queue, now=now)

    def walk(self) -> WalkStatus:
        """Enumerate the prefix and enqueue each new/changed supported blob; return ``completed``."""
        sync = self._begin_walk()
        for blob in self._blobs():
            candidate = self._candidate(blob)
            if candidate is not None:
                self._enqueuer.enqueue_if_needed(sync, self._location.drive_id, MessageSource.blob, candidate)
        return self._complete(sync)

    def _blobs(self) -> Iterable[BlobProperties]:
        """Every blob under the prefix, with its metadata, as the service lists them."""
        return self._container.list_blobs(name_starts_with=self._location.prefix, include=["metadata"])

    def _begin_walk(self) -> SyncState:
        """Load (or create) the synthetic ``sync_state`` for this location; mark ``walking``."""
        drive_id = self._location.drive_id
        sync = self._session.scalars(select(SyncState).where(SyncState.drive_id == drive_id)).one_or_none()
        if sync is None:
            sync = SyncState(drive_id=drive_id)
            self._session.add(sync)
        sync.walk_status = WalkStatus.walking
        self._session.commit()
        return sync

    def _complete(self, sync: SyncState) -> WalkStatus:
        """Record a completed re-enumeration: mark ``completed`` and stamp ``last_synced_at``."""
        sync.walk_status = WalkStatus.completed
        sync.last_synced_at = self._now()
        self._session.commit()
        return WalkStatus.completed

    def _candidate(self, blob: BlobProperties) -> DocumentCandidate | None:
        """Build a candidate from one listed blob, or ``None`` when it is not a supported document.

        Directory-marker blobs (names ending in ``/``) and unsupported suffixes are
        skipped with a ``WARNING``, mirroring :class:`~sources.LocalFileSystemSource`.
        """
        relative = PurePosixPath(blob.name.removeprefix(self._location.prefix))
        if blob.name.endswith("/") or not self._is_supported(relative):
            logger.warning("Skipping unsupported blob (no extractor for %r): %s", relative.suffix, blob.name)
            return None
        parent = relative.parent
        return DocumentCandidate(
            drive_item_id=relative.as_posix(),
            content_hash=stored_sha256(blob.metadata) or hash_bytes(read_blob(self._container, blob.name)),
            file_name=relative.name,
            mime_type=blob.content_settings.content_type or mime_type_for_suffix(relative.suffix),
            folder_path=None if parent == PurePosixPath() else parent.as_posix(),
        )

    @staticmethod
    def _is_supported(relative: PurePosixPath) -> bool:
        """Whether the blob's suffix has a registered text extractor."""
        return relative.suffix.lower() in supported_suffixes()
