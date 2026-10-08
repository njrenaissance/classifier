"""Azure Blob Storage source seam: location, hashing and retrieval (issue #70, ADR-0023).

The blob counterpart of :mod:`~filesystem_walker`'s root and
:class:`~content_source.FilesystemContentSource`. A blob container is the
authoritative store; each blob's identity is its name relative to the configured
prefix, and its content hash is a ``sha256`` the evidence workflow stores as blob
metadata — read at enqueue time without downloading the blob.

When a blob carries no ``sha256`` metadata, both the walker and the processor fall
back to downloading and hashing it with :func:`~content_source.hash_bytes`, so the
enqueue-time hash and the processor's re-check hash are always comparable.

Authentication is managed identity (``DefaultAzureCredential``) with read-only
access; the container must sit in an online tier (Hot/Cool/Cold) because Archive
blobs cannot be downloaded.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

from content_source import hash_bytes
from errors import SourceError
from models import Message

SHA256_METADATA_KEY = "sha256"


@dataclass(frozen=True)
class BlobLocation:
    """The container and virtual-directory prefix a blob walk enumerates."""

    account_url: str
    container: str
    prefix: str = ""

    @property
    def drive_id(self) -> str:
        """The synthetic ``sync_state`` key for this location (ADR-0020 pattern)."""
        return f"blob:{self.account_url}/{self.container}/{self.prefix}"


def create_container_client(account_url: str, container: str) -> ContainerClient:
    """Build a read-only container client authenticated by managed identity."""
    return ContainerClient(account_url, container, credential=DefaultAzureCredential())


def stored_sha256(metadata: Mapping[str, str] | None) -> str | None:
    """The ``sha256`` the evidence workflow recorded as blob metadata, if any."""
    return (metadata or {}).get(SHA256_METADATA_KEY)


def read_blob(container: ContainerClient, name: str) -> bytes:
    """Download a blob's bytes, translating a service failure into a domain error."""
    try:
        return container.get_blob_client(name).download_blob().readall()
    except AzureError as err:
        raise SourceError(f"Cannot read blob: {name}") from err


def read_blob_metadata(container: ContainerClient, name: str) -> Mapping[str, str]:
    """Fetch a blob's metadata without downloading its content."""
    try:
        return container.get_blob_client(name).get_blob_properties().metadata
    except AzureError as err:
        raise SourceError(f"Cannot read blob properties: {name}") from err


class BlobContentSource:
    """Retrieval from an Azure Blob container — the blob path (issue #70, ADR-0023).

    The message's ``drive_item_id`` is the blob name relative to the prefix; the
    hash re-check prefers stored metadata and otherwise hashes the downloaded bytes,
    matching :class:`~blob_walker.BlobWalker`'s enqueue-time hash.
    """

    def __init__(self, container: ContainerClient, prefix: str = "") -> None:
        self._container = container
        self._prefix = prefix

    def fetch_content_hash(self, message: Message) -> str | None:
        stored = stored_sha256(read_blob_metadata(self._container, self._name(message)))
        return stored or hash_bytes(self.download(message))

    def download(self, message: Message) -> bytes:
        return read_blob(self._container, self._name(message))

    def _name(self, message: Message) -> str:
        return f"{self._prefix}{message.drive_item_id}"
