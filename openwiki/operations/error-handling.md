---
type: Operations Guide
title: Error Handling
description: Exception hierarchy, error types, and recovery strategies
resource: /src/errors.py
tags: [error-handling, exceptions, operations, reliability]
---

# Error Handling

The classifier uses a clear exception hierarchy rooted in `AppError`. Every application-specific failure derives from this base, so callers can catch and handle specific error modes.

## Exception Hierarchy

```
Exception (Python built-in)
└── AppError (src/errors.py)
    ├── CategoryFileError        (A1: parsing)
    ├── SourceError              (A3: enumeration)
    ├── ExtractionError          (A2: text extraction)
    │   └── UnsupportedFormatError
    ├── ClassificationError      (B1: API call)
    └── OutputError              (A4: CSV write)
```

## Error Types

### CategoryFileError (A1)

**When it occurs:**
- Category Markdown file is missing
- Category file is unreadable (permission, encoding)
- File is malformed (duplicate categories, no categories found)
- Duplicate category names (case-insensitive)

**Example:**
```python
from categories import parse_category_file
from errors import CategoryFileError
from pathlib import Path

try:
    categories = parse_category_file(Path("categories.md"))
except CategoryFileError as e:
    print(f"Failed to load categories: {e}")
    # "Category file defines no categories (expected '## Name' headings)."
    # "Duplicate category: invoice"
    # "Cannot read category file: categories.md"
```

**Recovery:**
- Fail the entire run (categories are non-negotiable)
- Don't proceed to document enumeration

### SourceError (A3)

**When it occurs:**
- Source path doesn't exist
- Source path is neither a file nor a directory
- Permission denied accessing the path
- Source is a symlink to a directory (treated as non-file/non-directory)

**Example:**
```python
from sources import LocalFileSystemSource
from errors import SourceError
from pathlib import Path

try:
    source = LocalFileSystemSource(Path("/nonexistent/"))
    docs = source.documents()
except SourceError as e:
    print(f"Source error: {e}")
    # "Source path is not a file or directory: /nonexistent/"
```

**Recovery:**
- Fail the entire run (no documents to classify)
- Don't proceed to extraction

### ExtractionError & UnsupportedFormatError (A2)

**ExtractionError — When it occurs:**
- File is missing (checked at extraction time, not enumeration)
- File is corrupt or unreadable
- Text extraction library raises an error (e.g., `PyPdfError`, `PackageNotFoundError`)

**Example:**
```python
from extraction import extract_text
from errors import ExtractionError
from pathlib import Path

try:
    text = extract_text(Path("corrupt.pdf"))
except ExtractionError as e:
    print(f"Extraction failed: {e}")
    # "Cannot extract text from PDF: corrupt.pdf"
    # Root cause is chained: e.__cause__ is the original error
```

**UnsupportedFormatError — When it occurs:**
- File suffix has no registered text extractor (e.g., `.txt`, `.exe`, `.doc`)
- This is a subclass of `ExtractionError`

**Example:**
```python
from extraction import extract_text
from errors import UnsupportedFormatError, ExtractionError
from pathlib import Path

try:
    text = extract_text(Path("document.txt"))
except UnsupportedFormatError as e:
    print(f"Format not supported: {e}")
except ExtractionError as e:
    print(f"Extraction failed: {e}")
```

**Note:** During directory enumeration, unsupported files are **skipped with a WARNING**, not rejected. Only when a file is pointed at directly is `UnsupportedFormatError` raised.

**Recovery:**
- **During enumeration:** File is silently skipped (warning logged)
- **When pointed at directly:** Fail that document's classification
- **For all failures:** Propagate to the CLI for decision (fail run or continue?)

**Error chaining:**
All extraction errors are chained from the underlying library exception:

```python
try:
    text = extract_text(Path("corrupt.pdf"))
except ExtractionError as e:
    # e.__cause__ is the original PyPdfError, OSError, etc.
    print(f"Root cause: {e.__cause__}")
```

### ClassificationError (B1)

**When it occurs:**
- Anthropic API call fails (timeout, quota exceeded, authentication error)
- API returns an unexpected response format
- Response parsing fails (malformed JSON, missing fields)

**Example:**
```python
from classifier import Classifier
from errors import ClassificationError

classifier = Classifier(categories, client, temperature=0.4)

try:
    label = classifier.classify("Document text...")
except ClassificationError as e:
    print(f"Classification failed: {e}")
    # "Failed to get response from Anthropic"
    # "Invalid response format"
```

**Recovery:**
- **For a single run:** Propagate to the CLI
- **For self-consistency voting:** All N runs must succeed; if any fails, the verdict fails
- **For the entire job:** Fail that document's classification or the entire run (CLI decides)

### OutputError (A4)

**When it occurs:**
- Target directory cannot be created
- Target file cannot be written (permission denied, disk full)
- Underlying `OSError` from `Path.open()` or `Path.mkdir()`

**Example:**
```python
from writer import write_results_csv
from errors import OutputError
from pathlib import Path

try:
    write_results_csv(results, Path("/root/results.csv"))
except OutputError as e:
    print(f"Cannot write CSV: {e}")
    # "Cannot write results CSV: /root/results.csv"
    # Root cause is chained
```

**Recovery:**
- Fail the entire run
- User cannot use the results if they weren't written

## Error Chains

All errors preserve the root cause via Python's `raise ... from` syntax:

```python
# In extraction.py
try:
    reader = PdfReader(path)
except PyPdfError as err:
    raise ExtractionError(f"Cannot extract text from PDF: {path}") from err
```

**For debugging:**
```python
from extraction import extract_text
from errors import ExtractionError

try:
    text = extract_text(Path("corrupt.pdf"))
except ExtractionError as e:
    print(f"Message: {e}")                  # "Cannot extract text from PDF: ..."
    print(f"Root cause: {e.__cause__}")    # Original PyPdfError
    print(f"Traceback:")
    import traceback
    traceback.print_exc()  # Full chained traceback
```

## Callers Should Never Raise

Bare `Exception`, `RuntimeError`, or `ValueError` are **never** raised in application code. If you encounter them, it's a bug.

```python
# WRONG:
raise ValueError("Invalid input")  # Never do this

# RIGHT:
from errors import CategoryFileError
raise CategoryFileError("Invalid category format")
```

## CLI Error Handling (Future)

When the CLI (C1) is implemented, it should:

1. **Catch `CategoryFileError` immediately** → Print error and exit
2. **Catch `SourceError` immediately** → Print error and exit
3. **For each document:**
   - Try to extract and classify
   - Catch `ExtractionError` → Log warning, skip document (or fail run?)
   - Catch `ClassificationError` → Log error, skip document (or fail run?)
4. **After all documents, catch `OutputError`** → Print error and exit

**Decision TBD:** Should a single document failure fail the entire run, or should the CLI skip failed documents and write partial results?

## Testing & Mocking

In tests, you can assert on error types:

```python
import pytest
from categories import parse_categories
from errors import CategoryFileError

def test_duplicate_categories():
    markdown = """
## Invoice
- example

## Invoice
- duplicate
"""
    with pytest.raises(CategoryFileError, match="Duplicate category"):
        parse_categories(markdown)

def test_missing_categories():
    markdown = "No headings here"
    with pytest.raises(CategoryFileError, match="no categories"):
        parse_categories(markdown)
```

## Logging

Errors are logged before being raised (where appropriate):

```python
# In sources.py
logger = logging.getLogger(__name__)

def _is_supported(self, path: Path) -> bool:
    if path.suffix.lower() in supported_suffixes():
        return True
    logger.warning("Skipping unsupported file (no extractor for %r): %s", 
                   path.suffix, path)
    return False
```

Configure logging to see warnings:

```python
import logging

logging.basicConfig(level=logging.WARNING)  # Show warnings
# or
logging.basicConfig(level=logging.DEBUG)    # Show all
```

## Best Practices

1. **Catch specific exceptions, not `Exception`**
   ```python
   # GOOD
   try:
       categories = parse_category_file(path)
   except CategoryFileError as e:
       handle_category_error(e)
   
   # BAD
   try:
       categories = parse_category_file(path)
   except Exception as e:
       handle_error(e)  # Too broad
   ```

2. **Preserve error chains**
   ```python
   # GOOD
   except SomeError as e:
       raise AppError("Context message") from e
   
   # BAD
   except SomeError as e:
       raise AppError("Context message")  # Lost the root cause
   ```

3. **Log before raising** (where appropriate)
   ```python
   logger.warning("Skipping unsupported file: %s", path)
   return False
   ```

See [../architecture/overview.md](../architecture/overview.md#error-handling) for the error handling design overview.
