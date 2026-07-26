"""Tests for the processor entry point (E6, ADR-0012/0015).

Every boundary the processor touches — the queue, Microsoft Graph, the extraction
and voting pipeline, the database, and the result writer — is injected, so the
per-message orchestration is exercised as fast unit tests with no queue, tenant,
real DB, or model call. The queue and Graph are ``pytest-mock`` doubles; the DB
is a lightweight fake whose ``get`` returns a scripted ``documents`` row;
``extract_text_from_bytes`` is patched on the processor module namespace to script
the extraction outcome without building real file bytes.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import processor
from db import Document, DocumentStatus, ProcessingLog
from errors import ClassificationError, ExtractionError, GraphError, PersistenceError, UnsupportedFormatError
from models import Message
from processor import Processor
from self_consistency import Verdict

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


# --- fixtures / stand-ins --------------------------------------------------


def _message(**overrides):
    """A walker→processor work item; ``content_hash`` defaults to ``"H"``."""
    fields = {
        "document_id": 500,
        "sync_state_id": 1,
        "drive_id": "drive-1",
        "drive_item_id": "item-1",
        "file_name": "doc.pdf",
        "mime_type": "application/pdf",
        "content_hash": "H",
        "enqueued_at": _NOW,
    }
    fields.update(overrides)
    return Message(**fields)


class _ReceivedMessage:
    """A stand-in for the queue's ``ReceivedMessage`` (message + delete handles)."""

    def __init__(self, message, *, dequeue_count=1):
        self.message = message
        self.message_id = "mid"
        self.pop_receipt = "pr"
        self.dequeue_count = dequeue_count


def _document(*, status=DocumentStatus.queued, override=None, content_hash="H", retry_count=0):
    """A persisted ``documents`` row stand-in with a fixed id.

    ``retry_count`` is set explicitly because the ORM column default is applied at
    flush time, not on Python-side construction — a DB-loaded row always has an int.
    """
    doc = Document(
        sync_state_id=1,
        drive_item_id="item-1",
        content_hash=content_hash,
        classification_override=override,
        status=status,
        retry_count=retry_count,
    )
    doc.id = 500
    return doc


class _FakeSession:
    """A minimal SQLAlchemy ``Session`` fake for the processor's access pattern.

    ``get`` returns the single scripted ``documents`` row (or ``None``); ``add``
    records staged rows; ``commit``/``rollback`` count calls.
    """

    def __init__(self, document):
        self._document = document
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def get(self, _model, _pk):
        return self._document

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeQueue:
    """A queue fake returning one scripted ``ReceivedMessage`` and recording deletes."""

    def __init__(self, received):
        self._received = received
        self.deleted = []

    def receive(self):
        return self._received

    def delete(self, received):
        self.deleted.append(received)


def _make(mocker, *, document, dequeue_count=1, message=None):
    """Wire a :class:`Processor` around fakes; return it plus every collaborator."""
    received = (
        None
        if document is None and message is None
        else _ReceivedMessage(message or _message(), dequeue_count=dequeue_count)
    )
    session = _FakeSession(document)
    queue = _FakeQueue(received)
    graph = mocker.Mock()
    voter = mocker.Mock()
    writer = mocker.Mock()
    proc = Processor(session, graph, queue, voter, writer)
    return proc, received, session, queue, graph, voter, writer


def _logs(session):
    """The ``processing_log`` rows staged on ``session``, in order."""
    return [obj for obj in session.added if isinstance(obj, ProcessingLog)]


# --- happy path ------------------------------------------------------------


def test_happy_path_classifies_and_completes(mocker):
    document = _document(status=DocumentStatus.queued)
    proc, received, session, queue, graph, voter, writer = _make(mocker, document=document, dequeue_count=1)
    graph.fetch_content_hash.return_value = "H"
    graph.download.return_value = b"pdf-bytes"
    extract = mocker.patch("processor.extract_text_from_bytes", return_value="hello world")
    voter.classify.return_value = Verdict(category="contract", confidence=0.8)

    proc.run_once()

    graph.download.assert_called_once_with("drive-1", "item-1")
    extract.assert_called_once_with(b"pdf-bytes", "application/pdf")
    # The completed result is UPSERTed via the E1 writer, carrying the verdict.
    writer.write.assert_called_once()
    record = writer.write.call_args.args[0]
    assert record.sync_state_id == 1
    assert record.drive_item_id == "item-1"
    assert record.category == "contract"
    assert record.confidence == 0.8
    assert record.status is DocumentStatus.completed
    # Exactly one success processing_log row.
    logs = _logs(session)
    assert len(logs) == 1
    assert logs[0].status == "completed"
    assert logs[0].category == "contract"
    assert logs[0].confidence == 0.8
    assert logs[0].attempt == 1
    # Terminal success deletes the message.
    assert queue.deleted == [received]


# --- skip outcomes ---------------------------------------------------------


def test_content_hash_mismatch_skips_without_classifying(mocker):
    document = _document(content_hash="OLD")
    proc, received, session, queue, graph, voter, writer = _make(mocker, document=document)
    graph.fetch_content_hash.return_value = "NEW"  # differs from message content_hash "H"
    extract = mocker.patch("processor.extract_text_from_bytes")

    proc.run_once()

    assert document.status is DocumentStatus.skipped
    graph.download.assert_not_called()
    extract.assert_not_called()
    voter.classify.assert_not_called()
    writer.write.assert_not_called()
    logs = _logs(session)
    assert len(logs) == 1
    assert logs[0].status == "skipped"
    assert queue.deleted == [received]


def test_unsupported_mime_type_skips(mocker):
    document = _document()
    message = _message(mime_type="application/x-weird")
    proc, received, session, queue, graph, voter, writer = _make(mocker, document=document, message=message)
    graph.fetch_content_hash.return_value = "H"
    graph.download.return_value = b"bytes"
    mocker.patch(
        "processor.extract_text_from_bytes",
        side_effect=UnsupportedFormatError("no extractor for application/x-weird"),
    )

    proc.run_once()

    assert document.status is DocumentStatus.skipped
    voter.classify.assert_not_called()
    writer.write.assert_not_called()
    assert _logs(session)[0].status == "skipped"
    assert queue.deleted == [received]


# --- failure outcome -------------------------------------------------------


def _fail_at_download(mocker, graph, _voter, error):
    graph.download.side_effect = error
    mocker.patch("processor.extract_text_from_bytes")


def _fail_at_extraction(mocker, graph, _voter, error):
    graph.download.return_value = b"bytes"
    mocker.patch("processor.extract_text_from_bytes", side_effect=error)


def _fail_at_classification(mocker, graph, voter, error):
    graph.download.return_value = b"bytes"
    mocker.patch("processor.extract_text_from_bytes", return_value="text")
    voter.classify.side_effect = error


@pytest.mark.parametrize(
    ("configure", "error"),
    [
        pytest.param(_fail_at_download, GraphError("download failed"), id="graph_download"),
        pytest.param(_fail_at_extraction, ExtractionError("cannot parse pdf"), id="extraction"),
        pytest.param(_fail_at_classification, ClassificationError("api 500"), id="classification"),
    ],
)
def test_failure_marks_failed_bumps_retry_and_reraises(mocker, configure, error):
    document = _document(retry_count=2)
    proc, received, session, queue, graph, voter, writer = _make(mocker, document=document, dequeue_count=3)
    graph.fetch_content_hash.return_value = "H"
    configure(mocker, graph, voter, error)

    with pytest.raises(type(error)) as excinfo:
        proc.run_once()

    assert excinfo.value is error  # re-raised, never swallowed
    assert document.status is DocumentStatus.failed
    assert document.error_message == str(error)
    assert document.retry_count == 3  # bumped from 2
    log = _logs(session)[-1]
    assert log.status == "failed"
    assert log.error == str(error)
    assert log.attempt == 3  # the queue's dequeue_count
    writer.write.assert_not_called()
    assert queue.deleted == []  # not deleted → redelivered


def test_persistence_failure_on_commit_is_translated(mocker):
    document = _document()
    proc, received, session, queue, graph, voter, writer = _make(mocker, document=document)
    graph.fetch_content_hash.return_value = "H"
    graph.download.return_value = b"bytes"
    mocker.patch("processor.extract_text_from_bytes", return_value="text")
    voter.classify.return_value = Verdict(category="contract", confidence=0.9)
    from sqlalchemy.exc import SQLAlchemyError

    mocker.patch.object(session, "commit", side_effect=SQLAlchemyError("connection reset"))

    with pytest.raises(PersistenceError):
        proc.run_once()
    assert session.rollbacks == 1
    assert queue.deleted == []


# --- override protection ---------------------------------------------------


def test_classification_override_is_not_overwritten(mocker):
    document = _document(status=DocumentStatus.queued, override="hand-labelled")
    proc, received, session, queue, graph, voter, writer = _make(mocker, document=document)
    extract = mocker.patch("processor.extract_text_from_bytes")

    proc.run_once()

    writer.write.assert_not_called()
    voter.classify.assert_not_called()
    graph.download.assert_not_called()
    graph.fetch_content_hash.assert_not_called()
    extract.assert_not_called()
    assert document.classification_override == "hand-labelled"
    assert document.category is None  # the classifier never touched the label
    assert document.status is DocumentStatus.completed
    assert _logs(session)[0].status == "skipped"
    assert queue.deleted == [received]


# --- run_once edge cases ---------------------------------------------------


def test_run_once_is_a_no_op_on_empty_queue(mocker):
    session = _FakeSession(None)
    queue = _FakeQueue(None)
    graph, voter, writer = mocker.Mock(), mocker.Mock(), mocker.Mock()

    Processor(session, graph, queue, voter, writer).run_once()

    writer.write.assert_not_called()
    graph.download.assert_not_called()
    assert queue.deleted == []


def test_missing_documents_row_raises_persistence_error(mocker):
    message = _message()
    session = _FakeSession(None)
    queue = _FakeQueue(_ReceivedMessage(message))
    graph, voter, writer = mocker.Mock(), mocker.Mock(), mocker.Mock()

    with pytest.raises(PersistenceError, match="No documents row for id 500"):
        Processor(session, graph, queue, voter, writer).run_once()

    graph.fetch_content_hash.assert_not_called()
    assert queue.deleted == []


# --- run() boundary --------------------------------------------------------


def test_run_wires_the_processor_and_returns_zero(mocker):
    settings = mocker.patch("processor.get_settings").return_value
    settings.processor.category_file = Path("categories.md")
    parse = mocker.patch("processor.parse_category_file")
    voter = mocker.patch("processor.create_self_consistency_classifier")
    mocker.patch("processor.create_graph_client")
    mocker.patch("processor.create_message_queue")
    mocker.patch("processor.get_sessionmaker")
    mocker.patch("processor.DatabaseWriter")
    processor_cls = mocker.patch("processor.Processor")

    exit_code = processor.run([])

    assert exit_code == 0
    parse.assert_called_once_with(Path("categories.md"))
    voter.assert_called_once_with(parse.return_value, settings)
    processor_cls.return_value.run_once.assert_called_once_with()


def test_run_returns_one_on_app_error(mocker, caplog):
    settings = mocker.patch("processor.get_settings").return_value
    settings.processor.category_file = Path("categories.md")
    mocker.patch("processor.parse_category_file")
    mocker.patch("processor.create_self_consistency_classifier")
    mocker.patch("processor.create_graph_client", side_effect=GraphError("token boom"))

    with caplog.at_level("ERROR"):
        exit_code = processor.run([])

    assert exit_code == 1
    assert "Processing failed" in caplog.text


def test_run_fails_loudly_when_the_processor_section_is_unconfigured(mocker):
    settings = mocker.patch("processor.get_settings").return_value
    settings.processor = None  # a deploy-time misconfiguration must not be a silent no-op

    with pytest.raises(ValueError, match="Processor is not configured"):
        processor.run([])


def test_run_returns_one_on_invalid_settings(mocker, caplog):
    mocker.patch("processor.get_settings", side_effect=ValidationError.from_exception_data("Settings", []))

    with caplog.at_level("ERROR"):
        exit_code = processor.run([])

    assert exit_code == 1
    assert "Processing failed" in caplog.text
