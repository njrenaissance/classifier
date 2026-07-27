"""Tests for the walker entry point (ADR-0014).

Every boundary the walker touches — Microsoft Graph, the queue, the database and
the clock — is injected, so the enqueue-decision and budget logic is exercised as
fast unit tests with no tenant, queue, real DB or wall clock. Graph delta pages
come from a stub generator; the DB is a lightweight fake whose ``scalars`` returns
scripted lookups (query correctness is covered by the ``db`` integration tests);
the queue is a ``pytest-mock`` mock; time is a scripted callable.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import walker
from db import Document, DocumentStatus, SyncState, WalkStatus
from errors import GraphError
from graph_client import DeltaPage
from walker import Walker, WalkRequest

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)
_MATTERS_PATH = "/drives/b!x/root:/Matters/Smith-2026-001/Discovery"


# --- fixtures / stand-ins --------------------------------------------------


def _file(item_id, *, name="doc.pdf", mime="application/pdf", quick_xor="H", path=_MATTERS_PATH):
    """A Graph driveItem for a hashed file under ``path``."""
    return {
        "id": item_id,
        "name": name,
        "file": {"mimeType": mime, "hashes": {"quickXorHash": quick_xor}},
        "parentReference": {"path": path},
    }


def _folder(item_id):
    """A Graph driveItem for a folder (no ``file`` facet, so no content hash)."""
    return {"id": item_id, "folder": {"childCount": 1}, "parentReference": {"path": _MATTERS_PATH}}


def _deleted(item_id):
    """A Graph delta tombstone for a removed item."""
    return {"id": item_id, "deleted": {"state": "deleted"}, "parentReference": {"path": _MATTERS_PATH}}


def _document(drive_item_id, *, status, content_hash="H", override=None, previous_hash=None):
    """A persisted ``documents`` row stand-in with a fixed id."""
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


class _FakeGraph:
    """A stub Graph client whose ``iter_delta_pages`` replays scripted pages."""

    def __init__(self, pages, *, delta_link="DELTA"):
        self._pages = pages
        self._delta_link = delta_link
        self.start_url = "unset"
        self.root_path = "unset"

    def iter_delta_pages(self, _drive_id, start_url, *, root_path=None):
        self.start_url = start_url
        self.root_path = root_path
        yield from self._pages
        return self._delta_link


class _One:
    """The ``scalars(...)`` result wrapper, returning a single scripted value."""

    def __init__(self, value):
        self._value = value

    def one_or_none(self):
        return self._value


class _FakeSession:
    """A minimal SQLAlchemy ``Session`` fake for the walker's access pattern.

    ``scalars`` ignores the statement and returns the next scripted lookup: the
    ``sync_state`` first, then one ``documents`` row (or ``None``) per item the
    walker looks up, in order. ``flush``/``commit`` assign ids to added rows so
    the walker can build a queue message.
    """

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


def _clock(*times):
    """A scripted clock returning ``times`` in order across calls."""
    ticks = iter(times)
    return lambda: next(ticks)


def _walk(mocker, pages, *, sync_state=None, documents=(), now=None):
    """Run one walk over ``pages`` (600s budget) and return ``(status, session, queue, graph)``."""
    graph = _FakeGraph(pages)
    session = _FakeSession(sync_state, documents)
    queue = mocker.Mock()
    request = WalkRequest(drive_id="drive-1", root_path="/Matters", budget_seconds=600)
    status = Walker(session, graph, queue, request, now=now or (lambda: _NOW)).walk()
    return status, session, queue, graph


def _enqueued_ids(queue):
    """The drive_item_ids of every message the walker enqueued, in order."""
    return [call.args[0].drive_item_id for call in queue.enqueue.call_args_list]


# --- enqueue decisions -----------------------------------------------------


def test_new_and_changed_matters_files_are_each_enqueued_exactly_once(mocker):
    items = [
        _file("A", quick_xor="HA"),  # new -> enqueue
        _file("B", quick_xor="HB"),  # unchanged -> skip
        _file("C", quick_xor="HC-new"),  # in flight -> skip despite change
        _file("D", quick_xor="HD-new"),  # hash changed -> enqueue
    ]
    documents = (
        None,
        _document("B", status=DocumentStatus.completed, content_hash="HB"),
        _document("C", status=DocumentStatus.queued, content_hash="HC-old"),
        _document("D", status=DocumentStatus.completed, content_hash="HD-old"),
    )

    _status, _session, queue, _graph = _walk(mocker, [DeltaPage(items, None)], documents=documents)

    assert _enqueued_ids(queue) == ["A", "D"]


def test_hash_change_rotates_the_previous_hash_and_requeues(mocker):
    changed = _document("D", status=DocumentStatus.completed, content_hash="old")
    item = _file("D", quick_xor="new")

    _status, _session, queue, _graph = _walk(mocker, [DeltaPage([item], None)], documents=(changed,))

    assert changed.previous_hash == "old"
    assert changed.content_hash == "new"
    assert changed.status is DocumentStatus.queued
    assert _enqueued_ids(queue) == ["D"]


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(DocumentStatus.queued, id="queued"),
        pytest.param(DocumentStatus.processing, id="processing"),
    ],
)
def test_in_flight_files_are_never_enqueued(mocker, status):
    in_flight = _document("C", status=status, content_hash="old")
    item = _file("C", quick_xor="new")  # even a real change must not re-enqueue

    _status, _session, queue, _graph = _walk(mocker, [DeltaPage([item], None)], documents=(in_flight,))

    queue.enqueue.assert_not_called()
    assert in_flight.previous_hash is None  # untouched while in flight


def test_unchanged_file_is_not_enqueued(mocker):
    unchanged = _document("B", status=DocumentStatus.completed, content_hash="H")
    item = _file("B", quick_xor="H")

    _status, _session, queue, _graph = _walk(mocker, [DeltaPage([item], None)], documents=(unchanged,))

    queue.enqueue.assert_not_called()


def test_pending_reset_is_reenqueued_preserving_the_override(mocker):
    reset = _document("P", status=DocumentStatus.pending, content_hash="H", override="Correspondence")
    item = _file("P", quick_xor="H")  # unchanged hash — the pending reset still re-queues

    _status, _session, queue, _graph = _walk(mocker, [DeltaPage([item], None)], documents=(reset,))

    assert _enqueued_ids(queue) == ["P"]
    assert reset.status is DocumentStatus.queued
    assert reset.classification_override == "Correspondence"  # never clobbered
    assert reset.previous_hash is None  # unchanged hash, so nothing rotated


def test_folders_and_deletions_are_ignored(mocker):
    items = [_folder("F"), _deleted("G")]

    _status, session, queue, _graph = _walk(mocker, [DeltaPage(items, None)])

    queue.enqueue.assert_not_called()
    assert [row for row in session.added if isinstance(row, Document)] == []


def test_enqueued_message_carries_the_row_identity(mocker):
    item = _file("A", name="brief.pdf", mime="application/pdf", quick_xor="QXH")

    _status, _session, queue, _graph = _walk(mocker, [DeltaPage([item], None)], documents=(None,))

    message = queue.enqueue.call_args.args[0]
    assert (message.drive_id, message.drive_item_id) == ("drive-1", "A")
    assert (message.file_name, message.mime_type, message.content_hash) == ("brief.pdf", "application/pdf", "QXH")
    assert (message.sync_state_id, message.document_id) == (1000, 1001)  # ids assigned by the flush before enqueue
    assert message.enqueued_at == _NOW


# --- completion vs interruption --------------------------------------------


def test_completed_walk_persists_the_delta_token_and_clears_resume(mocker):
    sync = SyncState(drive_id="drive-1", resume_token="RESUME", delta_token=None)

    status, session, _queue, graph = _walk(mocker, [DeltaPage([], None)], sync_state=sync)

    assert status is WalkStatus.completed
    assert graph.start_url == "RESUME"  # resume_token wins as the start URL
    assert sync.delta_token == "DELTA"
    assert sync.resume_token is None
    assert sync.walk_status is WalkStatus.completed
    assert sync.last_synced_at == _NOW
    assert session.commits >= 2  # walking, then completed


def test_budget_exhaustion_persists_resume_token_and_leaves_delta_untouched(mocker):
    sync = SyncState(drive_id="drive-1", delta_token="PREV-DELTA")
    pages = [DeltaPage([], "PAGE2"), DeltaPage([], None)]  # second page never fetched
    clock = _clock(_NOW, _NOW + timedelta(seconds=601))  # deadline check trips after page 1

    status, _session, queue, _graph = _walk(mocker, pages, sync_state=sync, now=clock)

    assert status is WalkStatus.interrupted
    assert sync.resume_token == "PAGE2"
    assert sync.delta_token == "PREV-DELTA"  # deltaLink is never stored on interruption
    assert sync.walk_status is WalkStatus.interrupted
    queue.enqueue.assert_not_called()


def test_interrupted_walk_resumes_from_the_saved_page(mocker):
    sync = SyncState(drive_id="drive-1", resume_token="PAGE2")

    _status, _session, _queue, graph = _walk(mocker, [DeltaPage([], None)], sync_state=sync)

    assert graph.start_url == "PAGE2"


def test_first_walk_starts_from_a_full_enumeration(mocker):
    _status, _session, _queue, graph = _walk(mocker, [DeltaPage([], None)])

    assert graph.start_url is None  # no tokens yet -> initial /root/delta URL


def test_walk_scopes_the_delta_to_the_configured_root_path(mocker):
    graph = _FakeGraph([DeltaPage([], None)])
    request = WalkRequest(drive_id="drive-1", root_path="/Matters/TestSubset", budget_seconds=600)

    Walker(_FakeSession(), graph, mocker.Mock(), request, now=lambda: _NOW).walk()

    assert graph.root_path == "/Matters/TestSubset"  # subtree scoping is threaded to the Graph client


# --- run() boundary --------------------------------------------------------


def test_run_wires_the_walk_and_returns_zero(mocker):
    settings = mocker.patch("walker.get_settings").return_value
    settings.walker.drive_id = "drive-1"
    settings.walker.root_path = "/Matters/TestSubset"
    settings.walker.time_budget_seconds = 900
    mocker.patch("walker.create_graph_client")
    mocker.patch("walker.create_message_queue")
    mocker.patch("walker.get_sessionmaker")
    walker_cls = mocker.patch("walker.Walker")
    walker_cls.return_value.walk.return_value = WalkStatus.completed

    exit_code = walker.run([])

    assert exit_code == 0
    assert walker_cls.call_args.args[3] == WalkRequest(
        drive_id="drive-1", root_path="/Matters/TestSubset", budget_seconds=900
    )
    walker_cls.return_value.walk.assert_called_once_with()


def test_run_returns_one_on_app_error(mocker, caplog):
    settings = mocker.patch("walker.get_settings").return_value
    settings.walker.drive_id = "drive-1"
    mocker.patch("walker.create_graph_client", side_effect=GraphError("token boom"))

    with caplog.at_level("ERROR"):
        exit_code = walker.run([])

    assert exit_code == 1
    assert "Walk failed" in caplog.text


def test_run_fails_loudly_when_the_walker_section_is_unconfigured(mocker):
    settings = mocker.patch("walker.get_settings").return_value
    settings.walker = None  # a deploy-time misconfiguration must not be a silent no-op

    with pytest.raises(ValueError, match="Walker is not configured"):
        walker.run([])


def test_run_returns_one_on_invalid_settings(mocker, caplog):
    mocker.patch("walker.get_settings", side_effect=ValidationError.from_exception_data("Settings", []))

    with caplog.at_level("ERROR"):
        exit_code = walker.run([])

    assert exit_code == 1
    assert "Walk failed" in caplog.text
