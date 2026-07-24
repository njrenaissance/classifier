"""Azure Queue Storage client — the walker→processor work queue seam (ADR-0012/0014).

The two v2 jobs are decoupled by an Azure Queue: the **walker** enqueues one
:class:`~models.Message` per changed file, and the **processor** receives it,
classifies the document, and deletes it on success (ADR-0012). This module owns
that boundary so neither job touches the ``azure-storage-queue`` SDK directly.

Three responsibilities live here:

- **Send** — :meth:`MessageQueue.enqueue` serialises a :class:`~models.Message`
  to its Pydantic JSON wire form and puts it on the queue.
- **Receive** — :meth:`MessageQueue.receive` pulls one message, parses it back
  into a :class:`~models.Message`, and wraps it in a :class:`ReceivedMessage`
  that also carries the delete handle and Azure's ``dequeue_count`` (the retry
  count the processor uses for poison-message handling, ADR-0014).
- **Delete** — :meth:`MessageQueue.delete` removes a message once the processor
  has committed its result, so at-least-once delivery becomes effectively
  exactly-once at the ``documents`` UPSERT.

The underlying queue client is *injected* (:class:`QueueBackend`), so unit tests
fake it and no real storage account is needed; :func:`create_message_queue` is
the one place a live ``azure.storage.queue.QueueClient`` is built. Transport,
auth, and malformed-message failures are translated to
:class:`~errors.QueueError`, always chained, so a caller catches one domain type
instead of raw Azure or Pydantic exceptions.
"""

from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol

from azure.core.exceptions import AzureError
from pydantic import ValidationError

from config import QueueSettings, get_settings
from errors import QueueError
from models import Message


class QueueBackend(Protocol):
    """The minimal Azure Queue surface :class:`MessageQueue` depends on.

    Both ``azure.storage.queue.QueueClient`` and a test double satisfy it, so the
    wrapper never names the concrete SDK type — the network boundary stays fakeable.
    """

    def send_message(self, content: str) -> Any: ...

    def receive_message(self, *, visibility_timeout: int | None = None) -> Any | None: ...

    def delete_message(self, message: str, pop_receipt: str) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ReceivedMessage:
    """A message pulled from the queue, plus what the processor needs to finish it.

    ``message`` is the parsed work item; ``message_id``/``pop_receipt`` form the
    handle :meth:`MessageQueue.delete` uses to remove it after a successful
    classification; ``dequeue_count`` is Azure's redelivery counter, which the
    processor reads to detect and shed a poison message (ADR-0014).
    """

    message: Message
    message_id: str
    pop_receipt: str
    dequeue_count: int


class MessageQueue:
    """Send/receive/delete :class:`~models.Message` work items over an Azure Queue.

    The :class:`QueueBackend` is injected so the network boundary is fakeable in
    unit tests. Every SDK failure is raised as a chained :class:`~errors.QueueError`,
    and a message body that does not parse as a :class:`~models.Message` is treated
    the same way — a malformed item is a queue-boundary failure, not a silent skip.
    """

    def __init__(self, backend: QueueBackend) -> None:
        self._backend = backend

    def enqueue(self, message: Message) -> None:
        """Serialise ``message`` to JSON and put it on the queue.

        Idempotency is the *walker's* concern (it does not enqueue unchanged or
        in-flight files, ADR-0014); this method just performs the send and
        translates any transport failure into a chained :class:`~errors.QueueError`.
        """
        try:
            self._backend.send_message(message.model_dump_json())
        except AzureError as err:
            raise QueueError(f"Failed to enqueue message for drive item {message.drive_item_id}") from err

    def receive(self, *, visibility_timeout: int | None = None) -> ReceivedMessage | None:
        """Receive one message, or ``None`` when the queue is empty.

        ``visibility_timeout`` (seconds) hides the message from other receivers
        while the processor works; on success the processor calls :meth:`delete`,
        and if it crashes first the message reappears for another attempt. A body
        that is not a valid :class:`~models.Message` raises a chained
        :class:`~errors.QueueError` — the item stays on the queue and its
        ``dequeue_count`` climbs toward the poison threshold.
        """
        try:
            raw = self._backend.receive_message(visibility_timeout=visibility_timeout)
        except AzureError as err:
            raise QueueError("Failed to receive a message from the queue") from err
        if raw is None:
            return None
        try:
            message = Message.model_validate_json(raw.content)
        except ValidationError as err:
            raise QueueError(f"Queue message {raw.id} is not a valid work item") from err
        return ReceivedMessage(
            message=message,
            message_id=raw.id,
            pop_receipt=raw.pop_receipt,
            dequeue_count=raw.dequeue_count,
        )

    def delete(self, received: ReceivedMessage) -> None:
        """Delete a received message once its work is committed (ADR-0012)."""
        try:
            self._backend.delete_message(received.message_id, received.pop_receipt)
        except AzureError as err:
            raise QueueError(f"Failed to delete queue message {received.message_id}") from err

    def close(self) -> None:
        """Close the underlying queue client, releasing its connection pool."""
        self._backend.close()

    def __enter__(self) -> "MessageQueue":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _build_backend(settings: QueueSettings) -> QueueBackend:
    """Construct the live Azure queue client — connection string, else managed identity.

    ``azure.storage.queue`` (and ``azure.identity``) are imported lazily so a
    caller that injects its own backend into :class:`MessageQueue` never pays for
    them. :class:`QueueSettings` validation guarantees a name plus one auth mode.
    """
    from azure.storage.queue import QueueClient

    if settings.name is None:  # pragma: no cover - QueueSettings validation already guarantees this
        raise ValueError("Queue name is required.")
    if settings.connection_string is not None:
        return QueueClient.from_connection_string(settings.connection_string.get_secret_value(), settings.name)
    if settings.account_url is None:  # pragma: no cover - is_configured guarantees an account URL here
        raise ValueError("Queue managed-identity auth requires an account URL.")

    from azure.identity import DefaultAzureCredential

    return QueueClient(
        account_url=settings.account_url,
        queue_name=settings.name,
        credential=DefaultAzureCredential(),
    )


def create_message_queue(settings: QueueSettings | None = None) -> MessageQueue:
    """Build a :class:`MessageQueue` wired from :class:`QueueSettings`.

    The only place a real ``azure.storage.queue.QueueClient`` is constructed;
    everywhere else injects a :class:`QueueBackend` so tests need no storage
    account. Defaults to ``Settings.queue``, raising if the queue is unconfigured.
    """
    queue = settings or get_settings().queue
    if queue is None:
        raise ValueError("Queue is not configured; set the CLASSIFIER__QUEUE_* settings.")
    return MessageQueue(_build_backend(queue))
