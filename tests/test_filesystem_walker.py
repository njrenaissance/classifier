"""Tests for the filesystem walker (ADR-0020).

The producer is exercised over a real ``tmp_path`` tree (the enumeration + hashing
are the point, so the filesystem is not mocked), while the DB is the same
lightweight session fake used by the other producer tests and the queue is a
``pytest-mock`` mock. The walker only reads and hashes bytes — it never parses a
document — so the fixture files hold arbitrary bytes with the right suffix.
"""

from datetime import UTC, datetime

import pytest

from content_source import hash_bytes
from db import Document, DocumentStatus, SyncState, WalkStatus
from filesystem_walker import FilesystemWalker
from models import MessageSource

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# --- fixtures / stand-ins --------------------------------------------------


class _One:
    def __init__(self, value):
        self._value = value

    def one_or_none(self):
        return self._value


class _FakeSession:
    """Returns scripted ``scalars`` lookups: the sync_state first, then one row per file."""

    def __init__(self, sync_state=None, documents=()):
        self._results = iter([sync_state, *documents])
        self.added = []
        self.commits = 0
        self._next_id = 1000

    def scalars(self, _statement):
        return _One(next(self._results))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    def commit(self):
        self.commits += 1
        self.flush()


def _document(drive_item_id, *, status=DocumentStatus.completed, content_hash="H"):
    doc = Document(sync_state_id=1, drive_item_id=drive_item_id, content_hash=content_hash, status=status)
    doc.id = 500
    return doc


def _existing_sync() -> SyncState:
    sync = SyncState(drive_id="filesystem:x")
    sync.id = 1
    return sync


def _walk(mocker, root, *, sync_state=None, documents=()):
    session = _FakeSession(sync_state, documents)
    queue = mocker.Mock()
    status = FilesystemWalker(session, queue, root, now=lambda: _NOW).walk()
    return status, session, queue


def _enqueued(queue):
    return [call.args[0] for call in queue.enqueue.call_args_list]


def _added_documents(session):
    return [obj for obj in session.added if isinstance(obj, Document)]


# --- enumeration + enqueue -------------------------------------------------


def test_new_files_are_enumerated_hashed_and_enqueued(mocker, tmp_path):
    (tmp_path / "alpha.pdf").write_bytes(b"pdf-bytes")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "beta.docx").write_bytes(b"docx-bytes")
    (tmp_path / "note.txt").write_text("unsupported")  # filtered out by LocalFileSystemSource

    _status, session, queue = _walk(mocker, tmp_path, documents=(None, None))

    messages = _enqueued(queue)
    assert [m.drive_item_id for m in messages] == ["alpha.pdf", "sub/beta.docx"]
    assert {m.source for m in messages} == {MessageSource.filesystem}
    assert all(m.drive_id.startswith("filesystem:") for m in messages)
    assert messages[0].content_hash == hash_bytes(b"pdf-bytes")
    assert messages[1].content_hash == hash_bytes(b"docx-bytes")
    assert (messages[0].mime_type, messages[1].mime_type) == ("application/pdf", _DOCX_MIME)
    # folder_path is persisted on the row (not the message): None at the root, "sub" nested.
    folder_paths = {doc.drive_item_id: doc.folder_path for doc in _added_documents(session)}
    assert folder_paths == {"alpha.pdf": None, "sub/beta.docx": "sub"}


def test_unchanged_file_is_not_reenqueued(mocker, tmp_path):
    (tmp_path / "alpha.pdf").write_bytes(b"pdf-bytes")
    existing = _document("alpha.pdf", status=DocumentStatus.completed, content_hash=hash_bytes(b"pdf-bytes"))

    _status, _session, queue = _walk(mocker, tmp_path, sync_state=_existing_sync(), documents=(existing,))

    queue.enqueue.assert_not_called()


def test_changed_file_is_reenqueued_and_rotates_previous_hash(mocker, tmp_path):
    (tmp_path / "alpha.pdf").write_bytes(b"new-bytes")
    existing = _document("alpha.pdf", status=DocumentStatus.completed, content_hash="old-hash")

    _status, _session, queue = _walk(mocker, tmp_path, sync_state=_existing_sync(), documents=(existing,))

    queue.enqueue.assert_called_once()
    assert existing.previous_hash == "old-hash"
    assert existing.content_hash == hash_bytes(b"new-bytes")
    assert existing.status is DocumentStatus.queued


def test_unsupported_only_directory_enqueues_nothing(mocker, tmp_path):
    (tmp_path / "note.txt").write_text("unsupported")

    status, _session, queue = _walk(mocker, tmp_path)

    queue.enqueue.assert_not_called()
    assert status is WalkStatus.completed


# --- synthetic sync_state --------------------------------------------------


def test_completed_walk_marks_synthetic_sync_state_without_delta_tokens(mocker, tmp_path):
    status, session, _queue = _walk(mocker, tmp_path)

    sync = next(obj for obj in session.added if isinstance(obj, SyncState))
    assert status is WalkStatus.completed
    assert sync.drive_id.startswith("filesystem:")
    assert sync.walk_status is WalkStatus.completed
    assert sync.last_synced_at == _NOW
    assert sync.delta_token is None  # a filesystem has no delta point
    assert sync.resume_token is None
