"""Tests for the processor's content-retrieval seam (ADR-0020).

``GraphContentSource`` is a thin adapter over the injected Graph client (a
``pytest-mock`` double); ``FilesystemContentSource`` reads real bytes from a
``tmp_path`` root, so no mocking of the filesystem is needed. ``hash_bytes`` is the
one shared hash definition the producer and this seam agree on.
"""

from datetime import UTC, datetime

import pytest

from content_source import FilesystemContentSource, GraphContentSource, hash_bytes
from errors import SourceError
from models import Message, MessageSource

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)


def _message(**overrides):
    fields = {
        "source": MessageSource.filesystem,
        "document_id": 1,
        "sync_state_id": 1,
        "drive_id": "filesystem:/data",
        "drive_item_id": "sub/report.pdf",
        "file_name": "report.pdf",
        "mime_type": "application/pdf",
        "content_hash": "H",
        "enqueued_at": _NOW,
    }
    fields.update(overrides)
    return Message(**fields)


# --- GraphContentSource ----------------------------------------------------


def test_graph_source_forwards_drive_identifiers(mocker):
    graph = mocker.Mock()
    graph.fetch_content_hash.return_value = "QXH"
    graph.download.return_value = b"pdf-bytes"
    message = _message(source=MessageSource.sharepoint, drive_id="drive-1", drive_item_id="item-1")
    source = GraphContentSource(graph)

    assert source.fetch_content_hash(message) == "QXH"
    assert source.download(message) == b"pdf-bytes"
    graph.fetch_content_hash.assert_called_once_with("drive-1", "item-1")
    graph.download.assert_called_once_with("drive-1", "item-1")


# --- FilesystemContentSource -----------------------------------------------


def test_filesystem_source_reads_bytes_relative_to_root(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "report.pdf").write_bytes(b"real-pdf-bytes")
    source = FilesystemContentSource(tmp_path)

    assert source.download(_message(drive_item_id="sub/report.pdf")) == b"real-pdf-bytes"


def test_filesystem_source_hash_matches_hash_bytes(tmp_path):
    data = b"the-document-bytes"
    (tmp_path / "a.pdf").write_bytes(data)
    source = FilesystemContentSource(tmp_path)

    assert source.fetch_content_hash(_message(drive_item_id="a.pdf")) == hash_bytes(data)


def test_missing_file_raises_source_error(tmp_path):
    source = FilesystemContentSource(tmp_path)

    with pytest.raises(SourceError, match="Cannot read document file"):
        source.download(_message(drive_item_id="ghost.pdf"))


@pytest.mark.parametrize(
    "locator",
    [
        pytest.param("../secret.pdf", id="parent_escape"),
        pytest.param("sub/../../secret.pdf", id="nested_escape"),
    ],
)
def test_locator_escaping_the_root_is_rejected(tmp_path, locator):
    source = FilesystemContentSource(tmp_path)

    with pytest.raises(SourceError, match="escapes the mount root"):
        source.download(_message(drive_item_id=locator))


def test_absolute_locator_is_rejected(tmp_path):
    source = FilesystemContentSource(tmp_path)
    absolute = str((tmp_path / "a.pdf").resolve())

    with pytest.raises(SourceError, match="must be relative"):
        source.download(_message(drive_item_id=absolute))
