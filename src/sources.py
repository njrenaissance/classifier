"""Document sources (A3).

Enumerate the document files to classify. A :class:`DocumentSource` yields the
paths of supported documents behind a single interface; :class:`LocalFileSystemSource`
walks a local file or directory. The SharePoint source (D1) will implement the
same protocol, so the CLI (C1) depends only on :class:`DocumentSource`, never on
where the files come from (ADR-0003, ADR-0007, ADR-0010).

What counts as "supported" is *not* duplicated here: it is exactly the set of
suffixes with a registered text extractor (:func:`extraction.supported_suffixes`),
so registering a new format stays a single change in one place.

Enumeration never fails on an unsupported *type* — a file whose suffix has no
extractor is filtered out and logged as a ``WARNING`` (skipped-with-warning, per
``spec/spec.md``), whether it is one of many files in a directory or the single
path pointed at directly. A genuinely absent or non-file/non-directory path is a
different failure and is raised as :class:`~errors.SourceError`.
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from errors import SourceError
from extraction import supported_suffixes

logger = logging.getLogger(__name__)


class DocumentSource(Protocol):
    """A source of document paths to classify."""

    def documents(self) -> Iterable[Path]: ...


class LocalFileSystemSource:
    """Enumerate supported documents from a local file or directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def documents(self) -> Iterable[Path]:
        """Return the supported document paths under the source root.

        A single file yields itself; a directory is walked recursively. The
        result is sorted for deterministic, reproducible output. Unsupported
        files are skipped with a ``WARNING``; a missing or invalid root path
        raises :class:`~errors.SourceError`.
        """
        return [path for path in self._candidate_files() if self._is_supported(path)]

    def _candidate_files(self) -> list[Path]:
        """Every file under the root, sorted; validates that the root exists."""
        if self._root.is_file():
            return [self._root]
        if self._root.is_dir():
            return sorted(path for path in self._root.rglob("*") if path.is_file())
        raise SourceError(f"Source path is not a file or directory: {self._root}")

    def _is_supported(self, path: Path) -> bool:
        """Whether ``path`` has a registered extractor; warn-and-skip if not."""
        if path.suffix.lower() in supported_suffixes():
            return True
        logger.warning("Skipping unsupported file (no extractor for %r): %s", path.suffix, path)
        return False
