"""Processor entry point — dequeue → download → extract → classify → UPSERT (E6).

The consumer half of the two-job cloud pipeline (ADR-0012): a queue-triggered
Azure Container Apps job, invoked as ``python -m processor``, that classifies one
document per invocation. KEDA's ``azure-queue`` scaler spawns one replica per
message, so there is no polling loop — the job handles a single work item and
exits (ADR-0012).

For one E2 work item (:class:`~models.Message`) the processor: marks the
``documents`` row ``processing``; re-checks the Graph ``content_hash`` and skips
(letting the walker re-enqueue) on a mismatch; downloads the bytes via Graph
(E3, ADR-0015); extracts text by ``mime_type`` (E4), recording ``skipped`` for an
unsupported type; classifies with the self-consistency voter (#10, ADR-0005); and
UPSERTs the result via the E1 :class:`~writer.DatabaseWriter` — which never
overwrites a manual ``classification_override`` (ADR-0014) — while appending a
``processing_log`` audit row. A failure marks the row ``failed`` with an
``error_message``, bumps ``retry_count``, logs the attempt, and re-raises
(chained): the message is not deleted, so the queue redelivers it and its
``dequeueCount`` — never a message field — drives retry/poison shedding.

The classification core (``categories``, ``extraction``, ``classifier``,
``self_consistency``) and the seams (``graph_client``, ``message_queue``,
``writer``, ``db``) are reused as-is; this module owns only the per-message
orchestration. :class:`Processor` is the testable core (every boundary — Graph,
queue, session, voter, writer — is injected); :func:`run` is the system boundary
that wires them, catches domain failures once, and converts them into an exit
code.
"""

import argparse
import logging
import sys

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from categories import parse_category_file
from config import get_settings
from db import Document, DocumentStatus, ProcessingLog, get_sessionmaker
from errors import (
    AppError,
    ClassificationError,
    ExtractionError,
    GraphError,
    PersistenceError,
    UnsupportedFormatError,
)
from extraction import extract_text_from_bytes
from graph_client import GraphClient, create_graph_client
from message_queue import MessageQueue, ReceivedMessage, create_message_queue
from models import DocumentClassification, Message
from self_consistency import SelfConsistencyClassifier, Verdict, create_self_consistency_classifier
from writer import DatabaseWriter, Writer

logger = logging.getLogger(__name__)

# ``processing_log.status`` is a free-form string (not the ``DocumentStatus``
# enum): the per-attempt audit outcome recorded for each terminal path.
_LOG_COMPLETED = "completed"
_LOG_SKIPPED = "skipped"
_LOG_FAILED = "failed"

# Domain failures a classification attempt can raise. Caught together so the row
# is marked ``failed`` and the exception re-raised (chained, never swallowed).
# ``UnsupportedFormatError`` is a subclass of ``ExtractionError`` but is handled
# separately as a ``skipped`` outcome, so it must be caught *before* this tuple.
_ATTEMPT_FAILURES = (GraphError, ExtractionError, ClassificationError, PersistenceError)


class Processor:
    """Classifies one queued document per invocation (E6, ADR-0012).

    Every boundary is injected so the core is trivially unit-testable: the queue
    (dequeue/delete), Graph (hash re-check + download), the self-consistency voter,
    the SQLAlchemy session, and the result writer. The session's lifecycle is
    owned by the caller (see :func:`run`).
    """

    def __init__(
        self,
        session: Session,
        graph: GraphClient,
        queue: MessageQueue,
        voter: SelfConsistencyClassifier,
        writer: Writer,
    ) -> None:
        self._session = session
        self._graph = graph
        self._queue = queue
        self._voter = voter
        self._writer = writer

    def run_once(self) -> None:
        """Receive one work item, process it, and delete it on a terminal outcome.

        An empty queue is a no-op. A failure re-raises before the delete, so the
        message stays on the queue and is redelivered (its ``dequeueCount`` drives
        retry/poison shedding, ADR-0014); every non-failure outcome deletes the
        message so it is not reprocessed.
        """
        received = self._queue.receive()
        if received is None:
            logger.info("Queue empty; nothing to process")
            return
        self.process(received)
        self._queue.delete(received)

    def process(self, received: ReceivedMessage) -> None:
        """Run the pipeline for one work item, recording its terminal outcome.

        A row carrying a manual ``classification_override`` is left untouched (the
        human label stands); otherwise the row is marked ``processing`` and handed
        to :meth:`_run_pipeline`.
        """
        message = received.message
        document = self._load_document(message)
        if document.classification_override is not None:
            self._record_override(document, received)
            return
        document.status = DocumentStatus.processing
        self._commit()
        self._run_pipeline(document, message, received)

    def _run_pipeline(self, document: Document, message: Message, received: ReceivedMessage) -> None:
        """Re-check the hash, then download → extract → classify, recording the outcome."""
        try:
            current_hash = self._graph.fetch_content_hash(message.drive_id, message.drive_item_id)
            if current_hash != message.content_hash:
                self._record_skipped(document, received, "content_hash mismatch; walker will re-enqueue")
                return
            data = self._graph.download(message.drive_id, message.drive_item_id)
            text = extract_text_from_bytes(data, message.mime_type)
            verdict = self._voter.classify(text)
        except UnsupportedFormatError as err:
            self._record_skipped(document, received, f"unsupported mime type {message.mime_type!r}: {err}")
            return
        except _ATTEMPT_FAILURES as err:
            self._record_failure(document, received, err)
            raise
        self._record_success(document, message, received, verdict)

    def _load_document(self, message: Message) -> Document:
        """Load the ``documents`` row the walker created for this work item."""
        document = self._session.get(Document, message.document_id)
        if document is None:
            raise PersistenceError(f"No documents row for id {message.document_id}")
        return document

    def _record_success(
        self, document: Document, message: Message, received: ReceivedMessage, verdict: Verdict
    ) -> None:
        """UPSERT the classification result and append a ``completed`` audit row."""
        self._writer.write(
            DocumentClassification(
                sync_state_id=message.sync_state_id,
                drive_item_id=message.drive_item_id,
                category=verdict.category,
                confidence=verdict.confidence,
                status=DocumentStatus.completed,
            )
        )
        self._session.add(
            ProcessingLog(
                document_id=document.id,
                attempt=received.dequeue_count,
                status=_LOG_COMPLETED,
                category=verdict.category,
                confidence=verdict.confidence,
            )
        )
        self._commit()
        logger.info(
            "Classified document id=%s category=%s confidence=%.2f",
            document.id,
            verdict.category,
            verdict.confidence,
        )

    def _record_override(self, document: Document, received: ReceivedMessage) -> None:
        """Leave a human-labelled row's classification intact; mark it complete."""
        document.status = DocumentStatus.completed
        self._add_log(document, received, _LOG_SKIPPED, "classification_override present")
        self._commit()
        logger.info("Skipped document id=%s: classification_override present", document.id)

    def _record_skipped(self, document: Document, received: ReceivedMessage, reason: str) -> None:
        """Mark the row ``skipped`` and append a ``skipped`` audit row with the reason."""
        document.status = DocumentStatus.skipped
        self._add_log(document, received, _LOG_SKIPPED, reason)
        self._commit()
        logger.info("Skipped document id=%s: %s", document.id, reason)

    def _record_failure(self, document: Document, received: ReceivedMessage, error: Exception) -> None:
        """Mark the row ``failed``, stamp the error, bump ``retry_count``, and log the attempt."""
        document.status = DocumentStatus.failed
        document.error_message = str(error)
        document.retry_count += 1
        self._add_log(document, received, _LOG_FAILED, str(error))
        self._commit()
        logger.warning("Document id=%s failed on attempt %s: %s", document.id, received.dequeue_count, error)

    def _add_log(self, document: Document, received: ReceivedMessage, status: str, error: str) -> None:
        """Stage one non-success ``processing_log`` row for this attempt (skip/failure)."""
        self._session.add(
            ProcessingLog(
                document_id=document.id,
                attempt=received.dequeue_count,
                status=status,
                error=error,
            )
        )

    def _commit(self) -> None:
        """Commit staged changes, translating a driver failure into a domain error."""
        try:
            self._session.commit()
        except SQLAlchemyError as err:
            self._session.rollback()
            raise PersistenceError("Failed to commit processor state change") from err


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (the work item comes from the queue, not argv)."""
    return argparse.ArgumentParser(
        prog="processor",
        description="Classify one queued document: dequeue, download, extract, classify, and UPSERT the result.",
    )


def configure_logging() -> None:
    """Configure stdlib logging once, at the application entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run(argv: list[str]) -> int:
    """Parse ``argv``, process one work item, and return an exit code.

    Configuration errors (an unconfigured section) are deploy-time programmer
    errors and are left to fail loudly; only domain failures (:class:`AppError`)
    and invalid settings (:class:`ValidationError`) are converted here into a
    clean, logged exit code — the single system boundary (see error-handling
    standard). A failed attempt logs and returns ``1`` with the message left on
    the queue, so it is redelivered.
    """
    build_parser().parse_args(argv)
    try:
        settings = get_settings()
        processor = settings.processor
        if processor is None or processor.category_file is None:
            raise ValueError("Processor is not configured; set CLASSIFIER__PROCESSOR_CATEGORY_FILE.")
        categories = parse_category_file(processor.category_file)
        voter = create_self_consistency_classifier(categories, settings)
        with create_graph_client() as graph, create_message_queue() as queue, get_sessionmaker()() as session:
            Processor(session, graph, queue, voter, DatabaseWriter(session)).run_once()
    except (AppError, ValidationError):
        logger.exception("Processing failed")
        return 1
    return 0


def main() -> None:
    """CLI entry point: configure logging, run, and exit with the status code."""
    configure_logging()
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
