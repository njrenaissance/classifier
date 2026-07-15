"""Text extraction (A2).

Extracts plain text from documents for classification. Each supported format
has its own extractor (the Strategy pattern); :func:`extract_text` dispatches on
the file suffix through a registry, so adding a format is a single registration.

Supported now: PDF (via ``pypdf``) and DOCX (via ``python-docx``). Legacy binary
``.doc`` is intentionally deferred — ADR-0006 flags it as the weak link with no
reliable pure-Python story — and, like any other unrecognised suffix, is
rejected with :class:`~errors.UnsupportedFormatError` rather than skipped
silently.

Extraction never swallows a failure: a missing file or a corrupt document is
surfaced as :class:`~errors.ExtractionError`, chained (``raise ... from``) from
the underlying library error so the root cause survives in the traceback.
"""

from pathlib import Path
from typing import Protocol

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from errors import ExtractionError, UnsupportedFormatError


class TextExtractor(Protocol):
    """A strategy that turns one document file into plain text."""

    def extract(self, path: Path) -> str: ...


class PdfTextExtractor:
    """Extract text from a PDF page by page using ``pypdf``."""

    def extract(self, path: Path) -> str:
        try:
            reader = PdfReader(path)
            pages = [page.extract_text() or "" for page in reader.pages]
        except (PyPdfError, OSError, ValueError) as err:
            raise ExtractionError(f"Cannot extract text from PDF: {path}") from err
        return _join_nonblank(pages)


class DocxTextExtractor:
    """Extract text from a DOCX using ``python-docx`` (paragraphs then tables)."""

    def extract(self, path: Path) -> str:
        try:
            document = Document(str(path))
        except (PackageNotFoundError, OSError, ValueError) as err:
            raise ExtractionError(f"Cannot extract text from DOCX: {path}") from err
        lines = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.extend(cell.text for cell in row.cells)
        return _join_nonblank(lines)


_EXTRACTORS: dict[str, TextExtractor] = {
    ".pdf": PdfTextExtractor(),
    ".docx": DocxTextExtractor(),
}


def supported_suffixes() -> frozenset[str]:
    """The lower-cased file suffixes that have a registered extractor."""
    return frozenset(_EXTRACTORS)


def extract_text(path: Path) -> str:
    """Extract plain text from ``path``, dispatching on its file suffix.

    Raises :class:`~errors.UnsupportedFormatError` for an unregistered suffix
    (including legacy ``.doc``) and :class:`~errors.ExtractionError` when a
    supported file cannot be read.
    """
    extractor = _EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        supported = ", ".join(sorted(supported_suffixes()))
        raise UnsupportedFormatError(f"No text extractor for {path.suffix!r} file: {path} (supported: {supported})")
    return extractor.extract(path)


def _join_nonblank(parts: list[str]) -> str:
    """Join stripped, non-blank text fragments with single newlines."""
    return "\n".join(stripped for part in parts if (stripped := part.strip()))
