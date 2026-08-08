"""Tests for the shared enqueue core (ADR-0020).

The :class:`~enqueuer.Enqueuer` owns the source-neutral (re-)queue decision, the
``documents`` UPSERT, and the commit-before-enqueue ordering — extracted from the
walker so both producers share it. The DB session is a lightweight fake whose
``scalars`` returns a scripted row lookup (query correctness is covered by the
``db`` integration tests); the queue is a ``pytest-mock`` mock.
"""

from datetime import UTC, datetime

import pytest

from db import Document, DocumentStatus, SyncState
from enqueuer import DocumentCandidate, Enqueuer
from models import MessageSource

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


def _sync() -> SyncState:
    sync = SyncState(drive_id="drive-1")
    sync.id = 1
    return sync


def _candidate(
    drive_item_id: str = "A",
    *,
    content_hash: str = "H",
    file_name: str | None = "doc.pdf",
    mime_type: str | None = "application/pdf",
    folder_path: str | None = "/Matters/Smith",
) -> DocumentCandidate:
    return DocumentCandidate(
        drive_item_id=drive_item_id,
        content_hash=content_hash,
        file_name=file_name,
        mime_type=mime_type,
        folder_path=folder_path,
    )


def _document(drive_item_id, *, status, content_hash="H", override=None, previous_hash=None) -> Document:
    doc = Document(
        sync_state_id=1,
        drive_item_id=drive_item_id,
        content_hash=content_hash,
        previous_hash=previous_hash,
        classification_override=override,
        status=status,
    )
    doc.id = 500
    return doc


class _One:
    """The ``scalars(...)`` result wrapper, returning a single scripted value."""

    def __init__(self, value):
        self._value = value

    def one_or_none(self):
        return self._value


class _FakeSession:
    """A minimal Session fake: ``scalars`` returns the next scripted document lookup.

    ``flush``/``commit`` assign ids to added rows so a message can be built. ``events``
    records commit calls so a test can assert the commit-before-enqueue ordering.
    """

    def __init__(self, documents=(), *, events=None):
        self._results = iter(documents)
        self.added = []
        self.commits = 0
        self._next_id = 1000
        self._events = events

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
        if self._events is not None:
            self._events.append("commit")


def _run(mocker, candidate, *, existing=None, source=MessageSource.sharepoint, events=None):
    """Feed one candidate to a fresh Enqueuer; return ``(session, queue)``."""
    session = _FakeSession((existing,), events=events)
    queue = mocker.Mock()
    if events is not None:
        queue.enqueue.side_effect = lambda _message: events.append("enqueue")
    Enqueuer(session, queue, now=lambda: _NOW).enqueue_if_needed(_sync(), "drive-1", source, candidate)
    return session, queue


def test_new_file_is_enqueued(mocker):
    _session, queue = _run(mocker, _candidate("A"), existing=None)

    assert [call.args[0].drive_item_id for call in queue.enqueue.call_args_list] == ["A"]


def test_unchanged_file_is_not_enqueued(mocker):
    existing = _document("B", status=DocumentStatus.completed, content_hash="H")

    _session, queue = _run(mocker, _candidate("B", content_hash="H"), existing=existing)

    queue.enqueue.assert_not_called()


def test_hash_change_rotates_previous_hash_and_requeues(mocker):
    existing = _document("D", status=DocumentStatus.completed, content_hash="old")

    _session, queue = _run(mocker, _candidate("D", content_hash="new"), existing=existing)

    assert existing.previous_hash == "old"
    assert existing.content_hash == "new"
    assert existing.status is DocumentStatus.queued
    queue.enqueue.assert_called_once()


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(DocumentStatus.queued, id="queued"),
        pytest.param(DocumentStatus.processing, id="processing"),
    ],
)
def test_in_flight_file_is_never_enqueued(mocker, status):
    in_flight = _document("C", status=status, content_hash="old")

    _session, queue = _run(mocker, _candidate("C", content_hash="new"), existing=in_flight)

    queue.enqueue.assert_not_called()
    assert in_flight.previous_hash is None  # untouched while in flight


def test_pending_reset_is_reenqueued_preserving_the_override(mocker):
    reset = _document("P", status=DocumentStatus.pending, content_hash="H", override="Correspondence")

    _session, queue = _run(mocker, _candidate("P", content_hash="H"), existing=reset)

    queue.enqueue.assert_called_once()
    assert reset.status is DocumentStatus.queued
    assert reset.classification_override == "Correspondence"  # never clobbered
    assert reset.previous_hash is None  # unchanged hash, so nothing rotated


def test_row_is_committed_before_the_message_is_enqueued(mocker):
    # A queue send cannot be rolled back, so the row must be durable first (ADR-0014).
    events: list[str] = []

    _run(mocker, _candidate("A"), existing=None, events=events)

    assert events == ["commit", "enqueue"]


def test_enqueued_message_carries_identity_and_source(mocker):
    candidate = _candidate("A", file_name="brief.pdf", mime_type="application/pdf", content_hash="QXH")

    _session, queue = _run(mocker, candidate, existing=None, source=MessageSource.filesystem)

    message = queue.enqueue.call_args.args[0]
    assert message.source is MessageSource.filesystem
    assert (message.drive_id, message.drive_item_id) == ("drive-1", "A")
    assert (message.file_name, message.mime_type, message.content_hash) == ("brief.pdf", "application/pdf", "QXH")
    assert message.sync_state_id == 1
    assert message.enqueued_at == _NOW
