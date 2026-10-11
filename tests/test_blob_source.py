"""Tests for the blob retrieval seam and container factory (issue #70, ADR-0023).

The Azure ``ContainerClient`` is the external boundary and is mocked with
``pytest-mock``; the hash definition is the real :func:`~content_source.hash_bytes`.
"""

from datetime import UTC, datetime

import pytest
from azure.core.exceptions import ResourceNotFoundError

from blob_source import BlobContentSource, create_container_client
from content_source import hash_bytes
from errors import SourceError
from models import Message, MessageSource

pytestmark = pytest.mark.unit

_ACCOUNT_URL = "https://acct.blob.core.windows.net"


def _message(drive_item_id="case-7/alpha.pdf"):
    return Message(
        source=MessageSource.blob,
        document_id=1,
        sync_state_id=1,
        drive_id="blob:https://acct.blob.core.windows.net/matters/evidence/",
        drive_item_id=drive_item_id,
        file_name="alpha.pdf",
        mime_type="application/pdf",
        content_hash="unused",
        enqueued_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _source(mocker, *, data=b"", metadata=None, error=None):
    """A content source over a mocked container whose blob client returns ``data``/``metadata``."""
    blob_client = mocker.Mock()
    blob_client.download_blob.return_value.readall.return_value = data
    blob_client.get_blob_properties.return_value.metadata = metadata or {}
    if error is not None:
        blob_client.download_blob.side_effect = error
    container = mocker.Mock()
    container.get_blob_client.return_value = blob_client
    return BlobContentSource(container), container


def test_download_returns_the_blob_bytes(mocker):
    source, container = _source(mocker, data=b"pdf-bytes")

    assert source.download(_message()) == b"pdf-bytes"
    container.get_blob_client.assert_called_once_with("case-7/alpha.pdf")


def test_fetch_content_hash_prefers_the_stored_sha256_and_skips_the_download(mocker):
    source, container = _source(mocker, metadata={"sha256": "f" * 64})

    assert source.fetch_content_hash(_message()) == "f" * 64
    container.get_blob_client.return_value.download_blob.assert_not_called()


def test_fetch_content_hash_without_metadata_matches_the_walker_fallback(mocker):
    source, _container = _source(mocker, data=b"pdf-bytes", metadata={})

    assert source.fetch_content_hash(_message()) == hash_bytes(b"pdf-bytes")


def test_download_translates_an_azure_failure_into_a_source_error(mocker):
    source, _container = _source(mocker, error=ResourceNotFoundError("gone"))

    with pytest.raises(SourceError, match="Cannot read blob"):
        source.download(_message())


def test_create_container_client_uses_managed_identity_and_the_configured_container(mocker):
    container_client = mocker.patch("blob_source.ContainerClient")
    credential = mocker.patch("blob_source.DefaultAzureCredential")

    create_container_client(_ACCOUNT_URL, "matters")

    container_client.assert_called_once_with(_ACCOUNT_URL, "matters", credential=credential.return_value)
