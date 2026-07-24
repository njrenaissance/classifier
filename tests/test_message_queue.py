"""Tests for the Azure Queue Storage client (ADR-0012/0014).

The queue is an external boundary, so every case injects a fake
:class:`~message_queue.QueueBackend` — no real storage account is ever touched.
The factory cases patch the ``azure-storage-queue`` SDK to assert wiring without
constructing a live client.
"""

from datetime import UTC, datetime

import pytest
from azure.core.exceptions import ServiceRequestError

from config import QueueSettings, get_settings
from errors import QueueError
from message_queue import MessageQueue, ReceivedMessage, create_message_queue
from models import Message

pytestmark = pytest.mark.unit

_ENQUEUED_AT = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


def _message(**overrides):
    """A valid :class:`~models.Message` work item, with per-test field overrides."""
    fields = {
        "document_id": 1,
        "sync_state_id": 2,
        "drive_id": "drive-1",
        "drive_item_id": "item-42",
        "file_name": "brief.pdf",
        "mime_type": "application/pdf",
        "content_hash": "QXH",
        "enqueued_at": _ENQUEUED_AT,
    }
    return Message(**{**fields, **overrides})


def _raw(mocker, *, content, message_id="msg-1", pop_receipt="pop-1", dequeue_count=1):
    """A fake Azure ``QueueMessage`` as returned by ``receive_message``."""
    return mocker.Mock(content=content, id=message_id, pop_receipt=pop_receipt, dequeue_count=dequeue_count)


# --- enqueue ---------------------------------------------------------------


def test_enqueue_sends_the_message_as_pydantic_json(mocker):
    backend = mocker.Mock()
    message = _message()

    MessageQueue(backend).enqueue(message)

    backend.send_message.assert_called_once_with(message.model_dump_json())


def test_enqueue_wraps_transport_errors_as_chained_queue_error(mocker):
    backend = mocker.Mock()
    transport_error = ServiceRequestError("connection refused")
    backend.send_message.side_effect = transport_error

    with pytest.raises(QueueError) as excinfo:
        MessageQueue(backend).enqueue(_message())
    assert excinfo.value.__cause__ is transport_error


# --- receive ---------------------------------------------------------------


def test_receive_parses_the_message_and_carries_the_delete_handle(mocker):
    message = _message()
    backend = mocker.Mock()
    backend.receive_message.return_value = _raw(
        mocker, content=message.model_dump_json(), message_id="msg-9", pop_receipt="pop-9", dequeue_count=3
    )

    received = MessageQueue(backend).receive()

    assert received == ReceivedMessage(message=message, message_id="msg-9", pop_receipt="pop-9", dequeue_count=3)


def test_receive_returns_none_on_an_empty_queue(mocker):
    backend = mocker.Mock()
    backend.receive_message.return_value = None

    assert MessageQueue(backend).receive() is None


def test_receive_passes_the_visibility_timeout_through(mocker):
    backend = mocker.Mock()
    backend.receive_message.return_value = _raw(mocker, content=_message().model_dump_json())

    MessageQueue(backend).receive(visibility_timeout=120)

    backend.receive_message.assert_called_once_with(visibility_timeout=120)


def test_receive_wraps_a_malformed_body_as_chained_queue_error(mocker):
    backend = mocker.Mock()
    backend.receive_message.return_value = _raw(mocker, content='{"not": "a work item"}', message_id="poison-1")

    with pytest.raises(QueueError, match="poison-1") as excinfo:
        MessageQueue(backend).receive()
    assert excinfo.value.__cause__ is not None


def test_receive_wraps_transport_errors_as_chained_queue_error(mocker):
    backend = mocker.Mock()
    transport_error = ServiceRequestError("service unavailable")
    backend.receive_message.side_effect = transport_error

    with pytest.raises(QueueError) as excinfo:
        MessageQueue(backend).receive()
    assert excinfo.value.__cause__ is transport_error


# --- delete ----------------------------------------------------------------


def test_delete_removes_the_message_by_id_and_pop_receipt(mocker):
    backend = mocker.Mock()
    received = ReceivedMessage(message=_message(), message_id="msg-7", pop_receipt="pop-7", dequeue_count=1)

    MessageQueue(backend).delete(received)

    backend.delete_message.assert_called_once_with("msg-7", "pop-7")


def test_delete_wraps_transport_errors_as_chained_queue_error(mocker):
    backend = mocker.Mock()
    transport_error = ServiceRequestError("gone")
    backend.delete_message.side_effect = transport_error
    received = ReceivedMessage(message=_message(), message_id="msg-7", pop_receipt="pop-7", dequeue_count=1)

    with pytest.raises(QueueError) as excinfo:
        MessageQueue(backend).delete(received)
    assert excinfo.value.__cause__ is transport_error


# --- lifecycle -------------------------------------------------------------


def test_close_closes_the_underlying_backend(mocker):
    backend = mocker.Mock()

    MessageQueue(backend).close()

    backend.close.assert_called_once_with()


def test_context_manager_closes_on_exit(mocker):
    backend = mocker.Mock()
    queue = MessageQueue(backend)

    with queue as entered:
        assert entered is queue
    backend.close.assert_called_once_with()


# --- create_message_queue factory ------------------------------------------


def test_factory_builds_from_a_connection_string(mocker):
    queue_client = mocker.patch("azure.storage.queue.QueueClient")
    settings = QueueSettings(name="work-items", connection_string="DefaultEndpointsProtocol=https;AccountName=x")

    queue = create_message_queue(settings)

    assert isinstance(queue, MessageQueue)
    queue_client.from_connection_string.assert_called_once_with(
        "DefaultEndpointsProtocol=https;AccountName=x", "work-items"
    )


def test_factory_builds_from_managed_identity(mocker):
    queue_client = mocker.patch("azure.storage.queue.QueueClient")
    credential = mocker.patch("azure.identity.DefaultAzureCredential")
    settings = QueueSettings(
        name="work-items", account_url="https://acct.queue.core.windows.net", use_managed_identity=True
    )

    queue = create_message_queue(settings)

    assert isinstance(queue, MessageQueue)
    queue_client.assert_called_once_with(
        account_url="https://acct.queue.core.windows.net",
        queue_name="work-items",
        credential=credential.return_value,
    )
    queue_client.from_connection_string.assert_not_called()


def test_factory_defaults_to_the_configured_queue_settings(mocker, monkeypatch):
    monkeypatch.setenv("CLASSIFIER__QUEUE_NAME", "work-items")
    monkeypatch.setenv("CLASSIFIER__QUEUE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=x")
    get_settings.cache_clear()
    queue_client = mocker.patch("azure.storage.queue.QueueClient")

    queue = create_message_queue()

    assert isinstance(queue, MessageQueue)
    queue_client.from_connection_string.assert_called_once()
    get_settings.cache_clear()


def test_factory_raises_when_queue_is_unconfigured():
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Queue is not configured"):
        create_message_queue()
    get_settings.cache_clear()
