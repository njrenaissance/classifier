---
type: Domain Concept
title: Text Extraction and Format Support
description: Strategy pattern for extracting plain text from documents; supported formats (PDF, DOCX, plain-text); dispatch mechanisms for local CLI (suffix-based) and cloud processor (MIME-type-based).
tags: [extraction, pdf, docx, plaintext, strategy, domain]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-28T19:49:26.700Z
sources:
  - id: openwiki-source-9cf71b091111a6758b69f192
    resource: repo://spec/adr/0009-defer-legacy-doc-extraction.md
  - id: openwiki-source-376a7a89ccf5732120735148
    resource: repo://spec/adr/0021-plain-text-extraction.md
  - id: openwiki-source-3ecb73aeca5d8558f557e1ab
    resource: repo://src/extraction.py
  - id: openwiki-source-02a88f838799ec6965ae776e
    resource: repo://tests/test_extraction.py
generated: { by: "openwiki/0.4.3", at: "2026-08-28T19:49:26.700Z" }
---

# Text Extraction

The classifier extracts plain text from documents before classification. Different formats require different libraries or decoding strategies; the **`TextExtractor` Strategy pattern** allows new formats to be added with a single registration, independent of deployment path.

The system provides two entry points: **`extract_text(path)`** for local CLI (suffix-based dispatch) and **`extract_text_from_bytes(data, mime_type)`** for the cloud pipeline (MIME-type dispatch).

## Supported Formats

### PDF (`*.pdf`)

Uses `pypdf` to extract text page by page.

```python
class PdfTextExtractor:
    def extract(self, stream: BinaryIO) -> str:
        reader = PdfReader(stream)
        pages = [page.extract_text() or "" for page in reader.pages]
        return _join_nonblank(pages)
```

**Behavior:**
- Reads all pages from the PDF sequentially
- Extracts text from each page (blank or image-only pages yield empty strings)
- Joins non-blank text fragments with newlines
- Raises `ExtractionError` if the file is corrupt or unreadable

**Limitations:**
- Scanned or image-only PDFs return empty text (no OCR support)
- Complex or nested layouts may lose structural detail

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
- Extracts table cells as additional lines
- Joins non-blank lines with newlines
- Raises `ExtractionError` if the file is invalid or unreadable

**Limitations:**
- Ignores formatting, styles, headers/footers, and embedded images
- Returns plain text only

### Plain-Text Formats (ADR-0021)

**Supported suffixes:** `.txt`, `.json`, `.yml`, `.yaml`, `.md`, `.csv`, `.xml`

**Supported MIME types:** `text/plain`, `application/json`, `application/yaml` (with aliases `application/x-yaml`, `text/yaml`), `text/markdown`, `text/csv`, `application/xml` (with alias `text/xml`), **plus a `text/*` catch-all** for any text subtype from SharePoint.

Plain-text formats use a single `PlainTextExtractor` strategy (ADR-0021) that decodes raw bytes without structural parsing:

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
- **Raw decode, not structural parsing:** JSON, YAML, XML are decoded as raw text. The classifier wants textual content, not structure; a single format-agnostic strategy serves all text formats.
- **Encoding resilience:** Tries UTF-8 with BOM stripping first; falls back to Latin-1 for any non-UTF-8 bytes.
  - UTF-8 BOM (byte-order mark) is transparently stripped by the `utf-8-sig` codec
  - Non-UTF-8 bytes with Latin-1 mappings produce a lossy but readable result; never a hard failure
  - Truly exotic encodings (e.g., SHIFT_JIS) may produce mojibake, but text is always extracted rather than rejected
- Empty files yield empty strings
- One shared instance (`_PLAIN_TEXT`) backs all text suffixes, ensuring efficiency and determinism
- Raises `ExtractionError` only if the file cannot be read from disk (OS error)

**Why raw decode, not structural parsing?**
- One strategy serves every text format, reducing complexity
- No per-format parser dependencies needed
- Classifier benefits from the text content, not structure
- See [ADR-0021](../../spec/adr/0021-plain-text-extraction.md) for full rationale

**Why never fail on encoding?**
- Real-world documents arrive with mixed encodings (legacy files, exports, user-generated content)
- For classification, lossy-but-present text beats hard failures
- Both UTF-8 BOM and Latin-1 fallback ensure extraction always yields text

### Legacy DOC (`*.doc`)

**Status:** Intentionally deferred.

Binary `.doc` format (OLE-based, pre-OOXML) has no reliable pure-Python extraction library. See [ADR-0006](../../spec/adr/0006-text-extraction-per-format-libs.md) and [ADR-0009](../../spec/adr/0009-defer-legacy-doc-extraction.md).

**Current behavior:**
- `.doc` files are **rejected with `UnsupportedFormatError`** when pointed at directly
- During directory enumeration, `.doc` files are **skipped with a WARNING** (by `LocalFileSystemSource`)

**Rationale:** ADR-0006 chose per-format Python libraries (PDF, DOCX) to avoid external dependencies. ADR-0009 deferred `.doc` because no maintained pure-Python solution reliably handles the OLE binary format; the robust alternatives (Apache Tika, LibreOffice) each require heavyweight external runtimes. The `.doc` handler is tracked as a follow-up issue, to be chosen when there is real `.doc` volume to justify the dependency cost.

### Other Formats

Any format without a registered extractor (e.g., `.ppt`, `.zip`, binary `.exe`) is:
- **Skipped with a WARNING** during directory enumeration
- **Rejected with `UnsupportedFormatError`** if pointed at directly

## Error Handling

Extraction never swallows a failure. Errors are **always surfaced explicitly**:

- **`UnsupportedFormatError`** — File suffix (for `extract_text()`) or MIME type (for `extract_text_from_bytes()`) has no registered extractor. Includes legacy `.doc` and any unrecognized format. This is an `AppError` subclass, signaling an application-level contract violation (user pointing at an unsupported file, not a transient fault).

- **`ExtractionError`** — File is missing, unreadable, or corrupt (fails parsing or decoding). **Always chained** from the underlying library error using `raise ... from`, so the root cause survives in the traceback for debugging.

```python
# Example: chained extraction error
try:
    extract_text(Path("broken.pdf"))
except ExtractionError as e:
    print(f"Failed: {e}")
    print(f"Caused by: {e.__cause__}")  # Original pypdf error
```

## The Strategy Pattern

Adding a new format requires only two steps:

1. **Implement the `TextExtractor` protocol:**
   ```python
   class MyFormatExtractor:
       def extract(self, stream: BinaryIO) -> str:
           # Read from stream, extract and return plain text
           # Raise ExtractionError on failure
           ...
   ```

2. **Register in the `_EXTRACTORS` dict and suffix/MIME maps:**
   ```python
   _EXTRACTORS: dict[str, TextExtractor] = {
       ".pdf": PdfTextExtractor(),
       ".docx": DocxTextExtractor(),
       ".myformat": MyFormatExtractor(),  # Add here
   }
   
   _SUFFIX_TO_MIME: dict[str, str] = {
       ".pdf": "application/pdf",
       ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       ".myformat": "application/x-myformat",  # Add here
   }
   ```

The dispatcher (`extract_text()` and `extract_text_from_bytes()`) automatically uses the registered extractor. No other changes needed.

For aliases (multiple MIMEs → one suffix), add entries to `_MIME_TO_SUFFIX` after it is derived from `_SUFFIX_TO_MIME`:
```python
_MIME_TO_SUFFIX: dict[str, str] = {
    **{mime: suffix for suffix, mime in _SUFFIX_TO_MIME.items()},
    "application/x-myformat-legacy": ".myformat",  # Alias
}
```

## Dispatch Mechanisms

### Local CLI: Suffix-Based Dispatch (`extract_text()`)

The local CLI enumerates files from disk and extracts text by **file suffix**. This is fast and deterministic but requires inferring MIME type from the extension.

```python
def extract_text(path: Path) -> str:
    """Extract plain text from path, dispatching on its file suffix.
    
    Raises UnsupportedFormatError for an unregistered suffix
    and ExtractionError when a supported file cannot be read.
    """
    extractor = _EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        supported = ", ".join(sorted(supported_suffixes()))
        raise UnsupportedFormatError(f"No text extractor for {path.suffix!r} file: {path}")
    try:
        with path.open("rb") as stream:
            return extractor.extract(stream)
    except OSError as err:
        raise ExtractionError(f"Cannot read document file: {path}") from err
```

**Behavior:**
- Dispatch is case-insensitive (`.PDF` and `.pdf` both match)
- Opens the file and passes the binary stream to the appropriate extractor
- File-open errors (missing file, permission denied) are wrapped as `ExtractionError`

### Cloud Pipeline: MIME-Type Dispatch (`extract_text_from_bytes()`)

The cloud processor (ADR-0015) downloads file bytes into memory and extracts text using the **MIME type** from SharePoint's driveItem metadata, not disk suffix. This is more reliable (MIME type is authority on the source) but requires MIME-type registration.

```python
def extract_text_from_bytes(data: bytes, mime_type: str) -> str:
    """Extract plain text from in-memory bytes, dispatching on MIME type.
    
    The processor path: downloaded bytes are classified without touching disk.
    Raises UnsupportedFormatError for a MIME type with no registered extractor
    and ExtractionError when the bytes cannot be parsed.
    """
    # Normalize MIME type: strip parameters, lowercase
    normalized = mime_type.split(";", 1)[0].strip().lower()
    
    # Look up suffix in MIME→suffix map
    suffix = _MIME_TO_SUFFIX.get(normalized)
    
    # Fallback: any text/* MIME with no explicit entry → plain-text strategy
    if suffix is None and normalized.startswith("text/"):
        suffix = ".txt"
    
    # Dispatch to registered extractor
    extractor = _EXTRACTORS[suffix] if suffix is not None else None
    if extractor is None:
        supported = ", ".join(sorted(supported_mime_types()))
        raise UnsupportedFormatError(f"No text extractor for MIME type {mime_type!r}")
    
    return extractor.extract(BytesIO(data))
```

**Behavior:**
- MIME type parameters (e.g., `charset=utf-8`) are stripped before dispatch
- Dispatch is case-insensitive
- **`text/*` catch-all:** Any `text/` MIME type without an explicit entry (e.g., `text/x-custom`) is treated as plain text. This handles unforeseen text subtypes from SharePoint.
- Bytes are wrapped in a `BytesIO` stream for the extractor

**Example:**
```python
# Cloud processor receives driveItem from SharePoint
data: bytes = ...  # Downloaded file bytes
mime_type: str = "text/markdown; charset=utf-8"  # From driveItem metadata

# Extract without disk access; MIME type drives the dispatcher
text = extract_text_from_bytes(data, mime_type)  # Uses PlainTextExtractor
```

## API Reference

### `extract_text(path: Path) -> str`

Extract plain text from a local file, dispatching on suffix.

```python
from extraction import extract_text
from pathlib import Path

# Supported formats
text = extract_text(Path("document.pdf"))
text = extract_text(Path("notes.txt"))
text = extract_text(Path("data.json"))

# Unsupported format
try:
    text = extract_text(Path("archive.zip"))
except UnsupportedFormatError as e:
    print(f"Not supported: {e}")

# Corrupt or missing file
try:
    text = extract_text(Path("broken.pdf"))
except ExtractionError as e:
    print(f"Failed: {e}")
```

**Return value:** Plain text string (may be empty if the document is empty, image-only, or blank)

**Raises:**
- `UnsupportedFormatError` — Suffix has no registered extractor
- `ExtractionError` — File is missing, unreadable, or corrupt

### `extract_text_from_bytes(data: bytes, mime_type: str) -> str`

Extract plain text from in-memory bytes, dispatching on MIME type. Used by the cloud pipeline.

```python
from extraction import extract_text_from_bytes

# Explicit MIME type
data: bytes = ...  # Downloaded bytes
text = extract_text_from_bytes(data, "text/plain")
text = extract_text_from_bytes(data, "application/json")

# With charset parameter (stripped before dispatch)
text = extract_text_from_bytes(data, "text/csv; charset=utf-8")

# Catch-all: any text/* MIME type
text = extract_text_from_bytes(data, "text/x-custom")  # Plain-text strategy
```

**Return value:** Plain text string

**Raises:**
- `UnsupportedFormatError` — MIME type has no registered extractor (and is not `text/*`)
- `ExtractionError` — Data cannot be parsed or decoded

### `supported_suffixes() -> frozenset[str]`

Query which file suffixes have registered extractors. Used by `LocalFileSystemSource` to enumerate files.

```python
from extraction import supported_suffixes

suffixes = supported_suffixes()
# frozenset({'.pdf', '.docx', '.txt', '.json', '.yml', '.yaml', '.md', '.csv', '.xml'})

if ".txt" in suffixes:
    print("Plain-text extraction is supported")
```

### `supported_mime_types() -> frozenset[str]`

Query which MIME types have registered extractors.

```python
from extraction import supported_mime_types

mimes = supported_mime_types()
# frozenset({'application/pdf', 'application/vnd.openxmlformats...',
#            'text/plain', 'application/json', 'application/yaml',
#            'application/x-yaml', 'text/yaml', 'text/markdown', ...})
```

### `mime_type_for_suffix(suffix: str) -> str | None`

Return the canonical MIME type for a file suffix, or `None` if unsupported. Used to stamp local files' MIME type before the cloud processor extracts them.

```python
from extraction import mime_type_for_suffix

mime_type_for_suffix(".pdf")      # → "application/pdf"
mime_type_for_suffix(".docx")     # → "application/vnd.openxmlformats-..."
mime_type_for_suffix(".txt")      # → "text/plain"
mime_type_for_suffix(".unknown")  # → None
mime_type_for_suffix(".PDF")      # → "application/pdf" (case-insensitive)
```

## Registry Structure

The suffix↔MIME mapping is structured to support many MIMEs → one suffix (aliases) and ensure the local CLI's canonical MIME types are consistent:

```python
# Canonical source of truth: suffix → canonical MIME
_SUFFIX_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".json": "application/json",
    ".yml": "application/yaml",
    ".yaml": "application/yaml",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xml": "application/xml",
}

# Derived map: MIME → suffix, augmented with aliases
# Inversion of _SUFFIX_TO_MIME, then alias entries added
_MIME_TO_SUFFIX: dict[str, str] = {
    **{mime: suffix for suffix, mime in _SUFFIX_TO_MIME.items()},
    "application/x-yaml": ".yaml",    # Alias for YAML
    "text/yaml": ".yaml",             # Alias for YAML
    "text/xml": ".xml",               # Alias for XML
}

# Extractors: suffix → strategy instance
_EXTRACTORS: dict[str, TextExtractor] = {
    ".pdf": PdfTextExtractor(),
    ".docx": DocxTextExtractor(),
    ".txt": _PLAIN_TEXT,              # Shared instance
    ".json": _PLAIN_TEXT,             # Shared instance
    ".yml": _PLAIN_TEXT,              # Shared instance
    ".yaml": _PLAIN_TEXT,             # Shared instance
    ".md": _PLAIN_TEXT,               # Shared instance
    ".csv": _PLAIN_TEXT,              # Shared instance
    ".xml": _PLAIN_TEXT,              # Shared instance
}

# Catch-all for any text/* MIME not in the map
_TEXT_MIME_PREFIX = "text/"
```

## Summary of Supported Formats

| Format | Suffix | MIME Type | Library/Strategy | Behavior |
|--------|--------|-----------|------------------|----------|
| PDF | `.pdf` | `application/pdf` | `pypdf` | Extract pages sequentially |
| DOCX | `.docx` | `application/vnd.openxmlformats-...` | `python-docx` | Extract paragraphs and tables |
| Plain Text | `.txt` | `text/plain` | Raw UTF-8/Latin-1 decode | Format-agnostic raw decode |
| JSON | `.json` | `application/json` | Raw UTF-8/Latin-1 decode | Raw text (no parsing) |
| YAML | `.yml`, `.yaml` | `application/yaml` | Raw UTF-8/Latin-1 decode | Raw text (no parsing) |
| Markdown | `.md` | `text/markdown` | Raw UTF-8/Latin-1 decode | Raw text (no parsing) |
| CSV | `.csv` | `text/csv` | Raw UTF-8/Latin-1 decode | Raw text (no parsing) |
| XML | `.xml` | `application/xml` | Raw UTF-8/Latin-1 decode | Raw text (no parsing) |

**Not supported:**
- `.doc` (legacy binary) — Deferred; rejected with `UnsupportedFormatError`
- Other formats — Rejected with `UnsupportedFormatError`

## Testing

The extraction module is well-tested across PDF, DOCX, and plain-text formats:

- **PDF extraction:** Hand-rolled minimal PDF construction in tests ensures the real `pypdf` path is exercised
- **DOCX extraction:** Paragraphs and tables are both extracted; blank paragraphs are filtered out
- **Plain-text resilience:** UTF-8 BOM stripping and Latin-1 fallback are tested; empty files, mixed encodings, and lossy decoding are all validated
- **Dispatch:** Both suffix-based and MIME-based dispatch are tested; case-insensitivity and parameter stripping are verified
- **Error handling:** Corrupt files, missing files, and unsupported formats all raise appropriate errors; error chaining is validated
- **Catch-all:** Unlisted `text/*` MIME types (e.g., `text/x-anything`) fall back to plain-text strategy

## References

- [ADR-0006](../../spec/adr/0006-text-extraction-per-format-libs.md) — Per-format Python libraries decision
- [ADR-0009](../../spec/adr/0009-defer-legacy-doc-extraction.md) — Legacy `.doc` deferral
- [ADR-0021](../../spec/adr/0021-plain-text-extraction.md) — Plain-text format extension
<!-- openwiki: broken internal link [../../spec/adr/0015-cloud-processor.md] file "../../spec/adr/0015-cloud-processor.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- [ADR-0015](../../spec/adr/0015-cloud-processor.md) — Cloud pipeline architecture (MIME-based dispatch)
- Source: `src/extraction.py`
- Tests: `tests/test_extraction.py`
