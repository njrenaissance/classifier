---
type: Domain Concept
title: Text Extraction
description: Document format support, extractor implementations, and the Strategy pattern
resource: /src/extraction.py
tags: [extraction, pdf, docx, strategy, domain]
---

# Text Extraction (A2)

The classifier extracts plain text from documents before classification. Different formats (PDF, DOCX) require different libraries; the `TextExtractor` **Strategy pattern** allows new formats to be added with a single registration.

## Supported Formats

### PDF (`*.pdf`)

Uses `pypdf` to read PDF files page by page.

```python
class PdfTextExtractor:
    def extract(self, path: Path) -> str:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return _join_nonblank(pages)
```

**Behavior:**
- Reads all pages from the PDF
- Extracts text from each page (blank pages return empty strings)
- Joins non-blank lines with newlines
- Raises `ExtractionError` if the file is corrupt or unreadable

**Limitations:**
- Scanned/image-only PDFs return empty text (no OCR)
- Complex layouts may lose structure

### DOCX (`*.docx`)

Uses `python-docx` to extract structured content.

```python
class DocxTextExtractor:
    def extract(self, path: Path) -> str:
        document = Document(str(path))
        lines = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.extend(cell.text for cell in row.cells)
        return _join_nonblank(lines)
```

**Behavior:**
- Extracts all paragraphs in document order
- Extracts table cells as separate lines
- Joins non-blank lines with newlines
- Raises `ExtractionError` if the file is invalid or unreadable

**Limitations:**
- Ignores formatting, styles, and embedded images
- Returns plain text only

### Legacy DOC (`*.doc`)

**Status:** Intentionally deferred.

Binary `.doc` format (OLE-based, pre-OOXML) has no reliable pure-Python extraction library. See [ADR-0006](../../spec/adr/0006-text-extraction-per-format-libs.md) and [ADR-0009](../../spec/adr/0009-defer-legacy-doc-extraction.md) for the rationale.

**Current behavior:**
- `.doc` files are **skipped with a WARNING** during directory enumeration
- If a user points at a `.doc` file directly, it raises `UnsupportedFormatError`

### Other Formats

Any format without a registered extractor (e.g., `.txt`, `.xml`, `.ppt`) is:
- **Skipped with a WARNING** during directory enumeration
- **Rejected with `UnsupportedFormatError`** if pointed at directly

## The Strategy Pattern

Adding a new format requires only two steps:

1. **Implement** `TextExtractor` protocol:
```python
class MyFormatExtractor:
    def extract(self, path: Path) -> str:
        # Extract and return plain text
        ...
```

2. **Register** in the `_EXTRACTORS` dict:
```python
_EXTRACTORS: dict[str, TextExtractor] = {
    ".pdf": PdfTextExtractor(),
    ".docx": DocxTextExtractor(),
    ".myformat": MyFormatExtractor(),  # Add here
}
```

The dispatcher (`extract_text()`) automatically uses the registered extractor. No other changes needed.

## API: extract_text()

```python
from extraction import extract_text
from pathlib import Path

# Extract text from a supported file
text = extract_text(Path("document.pdf"))

# Or for an unsupported file
try:
    text = extract_text(Path("document.txt"))  # No extractor for .txt
except UnsupportedFormatError as e:
    print(f"Unsupported: {e}")

# Or if the file is corrupt
except ExtractionError as e:
    print(f"Failed to extract: {e}")
```

**Return value:** Plain text string (non-empty or empty if the document is image-only/blank)

**Error handling:**
- `UnsupportedFormatError` — File suffix has no registered extractor
- `ExtractionError` — File is missing, unreadable, or corrupt (chained from the underlying library error)

## Supported Suffixes Query

```python
from extraction import supported_suffixes

suffixes = supported_suffixes()  # frozenset({'.pdf', '.docx'})

if ".pdf" in suffixes:
    print("PDF extraction is supported")
```

This is used by the `DocumentSource` to decide which files to enumerate.

## Helper Function: _join_nonblank()

Both extractors use `_join_nonblank()` to clean up extracted text:

```python
def _join_nonblank(lines: list[str]) -> str:
    """Join non-blank lines with newlines."""
    return "\n".join(line for line in lines if line.strip())
```

This:
- Filters out blank lines
- Joins with newlines for readability
- Handles the case where a page/section has no content

## Error Chains

All extraction failures are chained (using `raise ... from`) so the root cause survives in the traceback:

```python
try:
    text = extract_text(Path("corrupt.pdf"))
except ExtractionError as e:
    # e.__cause__ is the original PyPdfError or OSError
    print(f"Root cause: {e.__cause__}")
```

## Testing & Mocking

In tests, you can mock extraction to avoid I/O:

```python
from unittest.mock import patch

with patch("extraction.extract_text") as mock_extract:
    mock_extract.return_value = "Extracted text for testing"
    # ... run your test
```

Or provide a fake extractor for a custom format:

```python
class FakeExtractor:
    def extract(self, path: Path) -> str:
        return "Test text"

# Patch the _EXTRACTORS dict for testing
```

## Integration Points

- **DocumentSource** — Uses `supported_suffixes()` to decide which files to enumerate
- **Classification pipeline** — Calls `extract_text()` for each document before classification
- **Error handling** — Extraction errors propagate to the CLI

## Performance Notes

- **Sequential extraction:** Documents are extracted one at a time (no parallelism in v1)
- **No caching:** Text is extracted fresh for each classification run (not cached between runs)
- **Memory:** Entire document text is loaded into memory for classification (target < 100 files)

See [../architecture/overview.md](../architecture/overview.md#a2-text-extraction) for the role of A2 in the system and [../workflows/classification-pipeline.md](../workflows/classification-pipeline.md#step-5-extract--classify-each-document-a2--b1--b2) for the pipeline context.
