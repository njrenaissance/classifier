---
type: Domain Concept
title: Text Extraction
description: Document format support, extractor implementations, and the Strategy pattern for PDF, DOCX, and plain-text formats
resource: /src/extraction.py
tags: [extraction, pdf, docx, plaintext, strategy, domain]
---

# Text Extraction (A2)

The classifier extracts plain text from documents before classification. Different formats (PDF, DOCX, plain text) require different libraries or decoding strategies; the `TextExtractor` **Strategy pattern** allows new formats to be added with a single registration.

## Supported Formats

### PDF (`*.pdf`)

Uses `pypdf` to read PDF files page by page.

```python
class PdfTextExtractor:
    def extract(self, stream: BinaryIO) -> str:
        reader = PdfReader(stream)
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
    def extract(self, stream: BinaryIO) -> str:
        document = Document(stream)
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

### Plain Text Formats (ADR-0021)

**Supported suffixes:** `.txt`, `.json`, `.yml`, `.yaml`, `.md`, `.csv`, `.xml`

**Supported MIME types:** `text/plain`, `application/json`, `application/yaml` (with aliases `application/x-yaml`, `text/yaml`), `text/markdown`, `text/csv`, `application/xml` (with alias `text/xml`), **plus a `text/*` catch-all** for any text subtype from SharePoint.

Plain-text formats are decoded as raw text using a single `PlainTextExtractor` strategy (ADR-0021):

```python
class PlainTextExtractor:
    """Decode a text document's bytes to a string (raw, format-agnostic).
    
    Decodes utf-8-sig (transparently stripping BOM); on non-UTF-8 bytes
    falls back to latin-1, which maps every byte and never raises.
    """
    def extract(self, stream: BinaryIO) -> str:
        try:
            data = stream.read()
        except OSError as err:
            raise ExtractionError("Cannot read plain-text document") from err
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("latin-1")
```

**Behavior:**
- No structural parsing — JSON, YAML, XML are decoded as raw text (the classifier wants textual content)
- **Encoding strategy:** Tries UTF-8 with BOM stripping first; falls back to Latin-1 for any non-UTF-8 bytes
  - UTF-8 BOM (byte-order mark) is transparently stripped by `utf-8-sig` codec
  - Non-UTF-8 bytes with Latin-1 mappings produce a lossy but readable result, never a hard failure
  - Truly exotic encodings (e.g., SHIFT_JIS) may produce mojibake, but text is always extracted rather than rejected
- Empty files yield empty strings
- One shared instance backs all text suffixes (efficient, deterministic)
- Raises `ExtractionError` only if the file cannot be read from disk (OS error)

**Why raw decode, not structural parsing?**
- Keeps one strategy serving every text format
- Avoids per-format parser dependencies
- Classifier benefits from the text content, not structure
- See [ADR-0021](../../spec/adr/0021-plain-text-extraction.md) for full rationale and alternatives considered

**Why never fail on encoding?**
- Real-world documents arrive with mixed encodings (legacy files, exports)
- For classification, lossy-but-present text beats hard failures
- Both UTF-8 BOM and Latin-1 fallback ensure extraction always yields text

### Legacy DOC (`*.doc`)

**Status:** Intentionally deferred.

Binary `.doc` format (OLE-based, pre-OOXML) has no reliable pure-Python extraction library. See [ADR-0006](../../spec/adr/0006-text-extraction-per-format-libs.md) and [ADR-0009](../../spec/adr/0009-defer-legacy-doc-extraction.md) for the rationale.

**Current behavior:**
- `.doc` files are **skipped with a WARNING** during directory enumeration
- If a user points at a `.doc` file directly, it raises `UnsupportedFormatError`

### Other Formats

Any format without a registered extractor (e.g., `.ppt`, `.zip`, binary `.exe`) is:
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

# Extract text from any supported file
text = extract_text(Path("document.pdf"))
text = extract_text(Path("notes.txt"))
text = extract_text(Path("data.json"))

# Or if the file format is unsupported
try:
    text = extract_text(Path("archive.zip"))  # No extractor for .zip
except UnsupportedFormatError as e:
    print(f"Unsupported: {e}")

# Or if the file is corrupt
except ExtractionError as e:
    print(f"Failed to extract: {e}")
```

**Return value:** Plain text string (non-empty or empty if the document is empty or image-only/blank)

**Error handling:**
- `UnsupportedFormatError` — File suffix has no registered extractor (including legacy `.doc`)
- `ExtractionError` — File is missing, unreadable, or corrupt (chained from the underlying library error)

## Supported Suffixes Query

```python
from extraction import supported_suffixes

suffixes = supported_suffixes()
# frozenset({'.pdf', '.docx', '.txt', '.json', '.yml', '.yaml', '.md', '.csv', '.xml'})

if ".txt" in suffixes:
    print("Plain-text extraction is supported")
```

This is used by the `DocumentSource` to decide which files to enumerate.

## API: extract_text_from_bytes() — Cloud Pipeline

The cloud processor (ADR-0015) downloads file bytes into memory and extracts text using MIME type dispatch, not disk suffix:

```python
from extraction import extract_text_from_bytes

# Processor receives driveItem's bytes and MIME type from SharePoint
data: bytes = ...  # Downloaded bytes
mime_type: str = "text/plain; charset=utf-8"  # From driveItem's resource

text = extract_text_from_bytes(data, mime_type)
```

**Dispatch logic:**
1. Normalizes MIME type (lowercases, strips charset parameters)
2. Looks up suffix in `_MIME_TO_SUFFIX` (handles aliases like `application/x-yaml` → `.yaml`)
3. Falls back to plain-text extraction if MIME matches `text/*` catch-all (handles unexpected text subtypes from SharePoint)
4. Raises `UnsupportedFormatError` if no extractor is found

**Supported MIME types:**
- `application/pdf` (PDF)
- `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (DOCX)
- `text/plain`, `application/json`, `application/yaml`, `application/x-yaml`, `text/yaml`, `text/markdown`, `text/csv`, `application/xml`, `text/xml` (plain text)
- Any `text/*` subtype not explicitly listed (catch-all for robustness)

The cloud processor never needs to determine MIME from a local file suffix (that only happens for the local CLI's filesystem source).

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

<!-- openwiki: broken internal link [../architecture/overview.md#a2-text-extraction-srcetractionpy] heading anchor "a2-text-extraction-srcetractionpy" does not exist in "../architecture/overview.md". Fix the href or restore the target, then delete this comment. -->
See [../architecture/overview.md](../architecture/overview.md#a2-text-extraction-srcetractionpy) for the role of A2 in the system and [../workflows/classification-pipeline.md](../workflows/classification-pipeline.md) for the pipeline context.
