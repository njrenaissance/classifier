"""Content retrieval seam for the processor (ADR-0020).

The processor needs two things for one work item: the file's *current* content hash
(to re-check the walker's hash and skip a stale message) and the file's *bytes* (to
extract and classify). :class:`ContentSource` is the narrow protocol over exactly
those two operations, so the processor depends on it instead of binding to the
concrete Graph client — the source is then swappable by configuration.

Two implementations:

- :class:`GraphContentSource` forwards to the existing :class:`~graph_client.GraphClient`
  (SharePoint, ADR-0015), reading the Graph-provided hash and downloading via Graph.
- :class:`FilesystemContentSource` resolves the message's locator against a mounted
  root and reads the bytes from disk (ADR-0020).

Both take a whole :class:`~models.Message`, so each impl reads whichever locator
fields it needs (Graph ids vs. a relative path) without the processor knowing which.

The filesystem hash is defined here, once, as :func:`hash_bytes` (``sha256``) and is
imported by *both* this retrieval seam and the filesystem producer, so a file's
enqueue-time hash and its processor re-check hash are computed identically — the
invariant the walker's "hash mismatch → skip, re-enqueue" contract relies on.
"""

import hashlib
from pathlib import Path
from typing import Protocol

from errors import SourceError
from graph_client import GraphClient
from models import Message


def hash_bytes(data: bytes) -> str:
    """Return the ``sha256`` hex digest of ``data`` — the filesystem content hash (ADR-0020).

    The single definition of the local content-hash algorithm, shared by the
    filesystem producer (enqueue-time) and :class:`FilesystemContentSource` (the
    processor's re-check) so the two hashes are always comparable.
    """
    return hashlib.sha256(data).hexdigest()


class ContentSource(Protocol):
    """The processor's retrieval seam: current hash + bytes for one work item."""

    def fetch_content_hash(self, message: Message) -> str | None: ...

    def download(self, message: Message) -> bytes: ...


class GraphContentSource:
    """Retrieval via Microsoft Graph — the SharePoint path (ADR-0015)."""

    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    def fetch_content_hash(self, message: Message) -> str | None:
        return self._graph.fetch_content_hash(message.drive_id, message.drive_item_id)

    def download(self, message: Message) -> bytes:
        return self._graph.download(message.drive_id, message.drive_item_id)


class FilesystemContentSource:
    """Retrieval from a mounted directory — the filesystem path (ADR-0020).

    The message's ``drive_item_id`` is a root-relative POSIX path; it is resolved
    against the configured mount ``root`` and read from disk. The hash re-check reads
    and hashes the same bytes via :func:`hash_bytes`, so a file changed since the walk
    produces a mismatch and the processor skips it (the full-re-enumeration walker
    re-enqueues it next run).
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def fetch_content_hash(self, message: Message) -> str | None:
        return hash_bytes(self.download(message))

    def download(self, message: Message) -> bytes:
        path = self._resolve(message.drive_item_id)
        try:
            return path.read_bytes()
        except OSError as err:
            raise SourceError(f"Cannot read document file: {path}") from err

    def _resolve(self, locator: str) -> Path:
        """Resolve a root-relative locator to an in-root path, rejecting escapes.

        The locator is expected to be relative (the producer stores
        ``path.relative_to(root)``). An absolute path or one that escapes the root via
        ``..`` is a programmer/contract error — a misbuilt message or a tampered queue
        item — and is rejected loudly with :class:`~errors.SourceError` rather than
        allowed to read outside the mounted tree.
        """
        candidate = Path(locator)
        if candidate.is_absolute():
            raise SourceError(f"Filesystem locator must be relative to the mount root: {locator!r}")
        resolved = (self._root / candidate).resolve()
        root = self._root.resolve()
        if resolved != root and root not in resolved.parents:
            raise SourceError(f"Filesystem locator escapes the mount root: {locator!r}")
        return resolved
