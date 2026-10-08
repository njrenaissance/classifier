"""Tests for the blob walker (issue #70, ADR-0023).

The producer is exercised against a ``pytest-mock`` stand-in for the Azure
``ContainerClient`` — the blob service is the external boundary, so it is the
only thing mocked. The DB session and the queue are mocks too: the walker's
decisions are about which blobs become candidates, not about persistence.
"""

import itertools
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from blob_source import BlobLocation
from blob_walker import BlobWalker
from content_source import hash_bytes
from db import Document
from models import MessageSource

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
_PREFIX = "evidence/"
_LOCATION = BlobLocation(account_url="https://acct.blob.core.windows.net", container="matters", prefix=_PREFIX)


def _blob(name, *, sha256=None, content_type=None):
    metadata = {"sha256": sha256} if sha256 is not None else {}
    return SimpleNamespace(
        name=name,
        metadata=metadata,
        content_settings=SimpleNamespace(content_type=content_type),
    )


def _container(mocker, blobs, contents=None):
    """A container whose listing yields ``blobs`` and whose downloads return ``contents[name]``."""
    container = mocker.Mock()
    container.list_blobs.return_value = blobs
    contents = contents or {}
    container.get_blob_client.side_effect = lambda name: _blob_client(mocker, contents.get(name, b""))
    return container


def _blob_client(mocker, data):
    client = mocker.Mock()
    client.download_blob.return_value.readall.return_value = data
    return client


def _session(mocker):
    """A session that finds no existing rows and assigns ids to added rows, as a flush would."""
    session = mocker.Mock()
    session.scalars.return_value.one_or_none.return_value = None
    ids = itertools.count(1)

    def assign_id(obj):
        obj.id = next(ids)

    session.add.side_effect = assign_id
    return session


def _walk(mocker, container, location=_LOCATION):
    session = _session(mocker)
    queue = mocker.Mock()
    status = BlobWalker(session, queue, container, location, now=lambda: _NOW).walk()
    return status, session, queue


def _enqueued(queue):
    return [call.args[0] for call in queue.enqueue.call_args_list]


def _added_documents(session):
    return [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Document)]


def test_supported_blobs_are_enqueued_and_unsupported_ones_skipped(mocker):
    blobs = [
        _blob("evidence/alpha.pdf", sha256="a" * 64),
        _blob("evidence/sub/beta.docx", sha256="b" * 64),
        _blob("evidence/legacy.doc", sha256="c" * 64),  # no extractor for .doc
    ]
    _status, _session, queue = _walk(mocker, _container(mocker, blobs))

    assert [m.drive_item_id for m in _enqueued(queue)] == ["alpha.pdf", "sub/beta.docx"]
    assert {m.drive_id for m in _enqueued(queue)} == {_LOCATION.drive_id}


def test_message_source_is_blob_and_drive_id_is_the_synthetic_container_locator(mocker):
    _status, _session, queue = _walk(mocker, _container(mocker, [_blob("evidence/alpha.pdf", sha256="a" * 64)]))

    message = _enqueued(queue)[0]
    assert message.source is MessageSource.blob
    assert message.drive_id == "blob:https://acct.blob.core.windows.net/matters/evidence/"


def test_prefix_is_stripped_and_folder_path_is_the_virtual_directory(mocker):
    blob = _blob("evidence/case-7/notes/memo.txt", sha256="d" * 64)
    _status, session, queue = _walk(mocker, _container(mocker, [blob]))

    message = _enqueued(queue)[0]
    document = _added_documents(session)[0]
    assert message.drive_item_id == "case-7/notes/memo.txt"
    assert document.file_name == "memo.txt"
    assert document.folder_path == "case-7/notes"


def test_a_blob_at_the_prefix_root_has_no_folder_path(mocker):
    _status, session, _queue = _walk(mocker, _container(mocker, [_blob("evidence/alpha.pdf", sha256="a" * 64)]))

    assert _added_documents(session)[0].folder_path is None


def test_stored_sha256_metadata_is_used_without_downloading_the_blob(mocker):
    container = _container(mocker, [_blob("evidence/alpha.pdf", sha256="e" * 64)])

    _status, _session, queue = _walk(mocker, container)

    container.get_blob_client.assert_not_called()
    assert _enqueued(queue)[0].content_hash == "e" * 64


def test_missing_sha256_metadata_is_hashed_from_the_downloaded_bytes(mocker):
    container = _container(mocker, [_blob("evidence/alpha.pdf")], contents={"evidence/alpha.pdf": b"pdf-bytes"})

    _status, _session, queue = _walk(mocker, container)

    assert _enqueued(queue)[0].content_hash == hash_bytes(b"pdf-bytes")


def test_mime_type_comes_from_the_blob_content_type_when_present(mocker):
    blob = _blob("evidence/alpha.pdf", sha256="a" * 64, content_type="application/pdf")
    _status, _session, queue = _walk(mocker, _container(mocker, [blob]))

    assert _enqueued(queue)[0].mime_type == "application/pdf"


def test_mime_type_falls_back_to_the_suffix_when_the_blob_has_no_content_type(mocker):
    blob = _blob("evidence/beta.docx", sha256="b" * 64, content_type=None)
    _status, _session, queue = _walk(mocker, _container(mocker, [blob]))

    assert _enqueued(queue)[0].mime_type == ("application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_directory_marker_blobs_are_skipped(mocker):
    blobs = [_blob("evidence/case-7/", sha256="f" * 64), _blob("evidence/", sha256="f" * 64)]
    _status, _session, queue = _walk(mocker, _container(mocker, blobs))

    queue.enqueue.assert_not_called()


def test_every_walk_reenumerates_the_container_with_metadata(mocker):
    container = _container(mocker, [])

    _walk(mocker, container)

    container.list_blobs.assert_called_once_with(name_starts_with=_PREFIX, include=["metadata"])
