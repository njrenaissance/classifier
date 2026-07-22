"""Results output — the writer seam.

Two destinations sit behind this seam, selected by entry point (ADR-0013):

- :func:`write_results_csv` — the local CLI path (ADR-0004): classification
  results to a CSV file with columns ``filename``, ``category``, ``confidence``.
- :class:`DatabaseWriter` — the cloud path: UPSERT a result into the ``documents``
  table keyed on ``(sync_state_id, drive_item_id)``. :class:`Writer` is the
  Protocol the production writer satisfies.

The CSV row type is deliberately source-agnostic — ``filename`` is a plain string,
not a :class:`~pathlib.Path` — so a local-filesystem run and a SharePoint run
produce the **same** CSV shape for equivalent files (ADR-0004). The caller decides
what string the ``filename`` column holds.

Each destination treats its write as an expected runtime failure and translates
the driver error into a domain exception, chained (``raise ... from``) so the root
cause survives in the traceback: the CSV path raises :class:`~errors.OutputError`
from an ``OSError``; :class:`DatabaseWriter` raises
:class:`~errors.PersistenceError` from a SQLAlchemy error.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Protocol

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from classifier import MODEL
from db import Document
from errors import OutputError, PersistenceError
from models import DocumentClassification


@dataclass(frozen=True)
class ClassificationResult:
    """One document's classification outcome — a single CSV row.

    The row *is* the CSV shape: :meth:`headers` derives the column names from
    the field names (``filename``, ``category``, ``confidence`` — ADR-0004) and
    :meth:`row` renders one row in its CSV form, keeping the output format owned
    by the data model rather than the writer.
    """

    filename: str  # source-agnostic name (local FS and SharePoint alike) — ADR-0004
    category: str  # a real category name or the reserved "unknown"
    confidence: float  # self-consistency agreement rate in [0.0, 1.0] — ADR-0005

    @classmethod
    def headers(cls) -> tuple[str, ...]:
        """The CSV column names, in order — the dataclass field names."""
        return tuple(field.name for field in fields(cls))

    def row(self) -> tuple[str, str, str]:
        """This result as a CSV row; ``confidence`` formatted to two decimals."""
        return (self.filename, self.category, f"{self.confidence:.2f}")


def write_results_csv(results: Iterable[ClassificationResult], path: Path) -> None:
    """Write ``results`` to a CSV at ``path``, creating/overwriting the file.

    Rows are written in the order given (stable). Missing parent directories are
    created. ``confidence`` is formatted to two decimal places; ``filename`` and
    ``category`` are written verbatim with the :mod:`csv` module handling any
    quoting/escaping. Raises :class:`~errors.OutputError` if the path cannot be
    written.
    """
    rows = [result.row() for result in results]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(ClassificationResult.headers())
            writer.writerows(rows)
    except OSError as err:
        raise OutputError(f"Cannot write results CSV: {path}") from err


class Writer(Protocol):
    """The production writer seam: persist one classification result."""

    def write(self, record: DocumentClassification) -> None: ...


def build_document_upsert(record: DocumentClassification) -> Insert:
    """Build the ``documents`` UPSERT for ``record`` (PostgreSQL ``ON CONFLICT``).

    Inserts a new row, or on a ``(sync_state_id, drive_item_id)`` conflict updates
    the classification result and its timestamps. ``classified_by`` is stamped with
    ``classifier.MODEL`` (ADR-0002). The ``WHERE classification_override IS NULL``
    guard makes the update a no-op when a manual override exists, so the classifier
    **never** overwrites a human decision (ADR-0014).
    """
    statement = insert(Document).values(
        sync_state_id=record.sync_state_id,
        drive_item_id=record.drive_item_id,
        category=record.category,
        confidence=record.confidence,
        status=record.status,
        classified_by=MODEL,
        classified_at=func.now(),
        processed_at=func.now(),
    )
    return statement.on_conflict_do_update(
        index_elements=[Document.sync_state_id, Document.drive_item_id],
        set_={
            "category": statement.excluded.category,
            "confidence": statement.excluded.confidence,
            "status": statement.excluded.status,
            "classified_by": statement.excluded.classified_by,
            "classified_at": statement.excluded.classified_at,
            "processed_at": statement.excluded.processed_at,
            # ORM ``onupdate`` does not fire for a Core ON CONFLICT DO UPDATE, so
            # refresh the row's modification stamp explicitly on re-classification.
            "updated_at": func.now(),
        },
        where=Document.classification_override.is_(None),
    )


class DatabaseWriter:
    """Writer strategy that UPSERTs a result into ``documents`` (ADR-0013).

    The session is injected so the writer is trivial to unit-test and so the
    caller owns the connection lifecycle. A driver failure is an expected runtime
    failure: it is rolled back and re-raised as :class:`~errors.PersistenceError`,
    chained from the underlying SQLAlchemy error.

    **Transaction contract:** :meth:`write` is a self-contained single-write unit
    — it executes one UPSERT and commits it. A caller that needs several writes in
    one transaction (e.g. a processor recording a ``processing_log`` row atomically
    with the result) should issue those statements against the session directly
    and commit once, rather than composing multiple ``write`` calls.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def write(self, record: DocumentClassification) -> None:
        """UPSERT ``record`` into ``documents`` and commit."""
        try:
            self._session.execute(build_document_upsert(record))
            self._session.commit()
        except SQLAlchemyError as err:
            self._session.rollback()
            raise PersistenceError(f"Failed to persist classification for drive item {record.drive_item_id}") from err
