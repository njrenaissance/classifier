"""CSV results output (A4).

Persist classification results to a CSV file on the local filesystem with the
columns ``filename``, ``category``, ``confidence`` (ADR-0004). CSV is the only
output format in scope, so this is a single plain function rather than a
Strategy: :func:`write_results_csv` takes an iterable of
:class:`ClassificationResult` rows and a target path.

The row type is deliberately source-agnostic — ``filename`` is a plain string,
not a :class:`~pathlib.Path` — so a local-filesystem run and a SharePoint run
produce the **same** CSV shape for equivalent files (ADR-0004). The caller
decides what string the ``filename`` column holds.

Writing to the filesystem is an expected runtime failure: a target that cannot
be created or written is caught and re-raised as :class:`~errors.OutputError`,
chained (``raise ... from``) from the underlying ``OSError`` so the root cause
survives in the traceback.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from errors import OutputError

COLUMNS = ("filename", "category", "confidence")


@dataclass(frozen=True)
class ClassificationResult:
    """One document's classification outcome — a single CSV row."""

    filename: str  # source-agnostic name (local FS and SharePoint alike) — ADR-0004
    category: str  # a real category name or the reserved "unknown"
    confidence: float  # self-consistency agreement rate in [0.0, 1.0] — ADR-0005


def write_results_csv(results: Iterable[ClassificationResult], path: Path) -> None:
    """Write ``results`` to a CSV at ``path``, creating/overwriting the file.

    Rows are written in the order given (stable). Missing parent directories are
    created. ``confidence`` is formatted to two decimal places; ``filename`` and
    ``category`` are written verbatim with the :mod:`csv` module handling any
    quoting/escaping. Raises :class:`~errors.OutputError` if the path cannot be
    written.
    """
    rows = list(results)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows((result.filename, result.category, f"{result.confidence:.2f}") for result in rows)
    except OSError as err:
        raise OutputError(f"Cannot write results CSV: {path}") from err
