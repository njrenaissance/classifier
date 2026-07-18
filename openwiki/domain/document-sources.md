---
type: Domain Concept
title: Document Sources
description: Enumerating documents from local filesystem and SharePoint via the DocumentSource protocol
resource: /src/sources.py
tags: [sources, filesystem, sharepoint, protocol, domain]
---

# Document Sources (A3)

Document sources enumerate the files to classify. The `DocumentSource` **protocol** allows different backends (local filesystem, SharePoint, etc.) to be swapped without changing the CLI.

## The DocumentSource Protocol

A simple interface that any source must implement:

```python
class DocumentSource(Protocol):
    """A source of document paths to classify."""
    def documents(self) -> Iterable[Path]:
        ...
```

Any class with a `documents()` method that yields `Path` objects is a valid `DocumentSource`. The CLI depends only on this protocol, never on concrete implementations.

## Local Filesystem Source

Currently implemented:

```python
class LocalFileSystemSource:
    def __init__(self, root: Path) -> None:
        self._root = root
    
    def documents(self) -> Iterable[Path]:
        """Return supported documents under the source root."""
        # Returns sorted list, filters by supported formats
```

### Behavior

**Single file:**
```python
source = LocalFileSystemSource(Path("document.pdf"))
docs = source.documents()  # → [Path("document.pdf")]
```

**Directory:**
```python
source = LocalFileSystemSource(Path("./documents/"))
docs = source.documents()  # → All supported files, sorted, recursively
```

### Enumeration Rules

1. **Sorting:** Results are sorted lexicographically for deterministic, reproducible output
2. **Recursion:** Directories are walked recursively
3. **Symlinks:** Symlinked *subdirectories* are **not** descended (via `rglob(recurse_symlinks=False)`)
   - Symlinks *to files* are still enumerated like any other file
   - This prevents infinite loops from circular symlinks
4. **Format filtering:** Only files with registered text extractors are included
   - Unsupported files (e.g., `.txt`, `.exe`) log a `WARNING` and are skipped
5. **Validation:** Missing or invalid root paths raise `SourceError`

### Example

```
documents/
├── invoice_2024.pdf          ✓ Included (PDF supported)
├── contract.docx             ✓ Included (DOCX supported)
├── readme.txt                ✗ Skipped (WARNING: no extractor)
├── images/
│   └── photo.png             ✗ Skipped (WARNING: no extractor)
└── link_to_archive -> /mnt/  ✗ Symlink not descended
    └── old_files/            ✗ Never reached
```

Result:
```python
[
    Path("documents/contract.docx"),
    Path("documents/invoice_2024.pdf"),
]
```

(Sorted, only supported formats)

### Error Handling

```python
from errors import SourceError
from sources import LocalFileSystemSource
from pathlib import Path

try:
    source = LocalFileSystemSource(Path("/nonexistent/path/"))
    docs = source.documents()
except SourceError as e:
    print(f"Source error: {e}")
    # "Source path is not a file or directory: /nonexistent/path/"
```

**Errors:**
- Missing path → `SourceError`
- Path is a symlink to a directory (treated as non-file/non-directory) → `SourceError`
- Permission denied → `SourceError` (from `Path.is_file()` or `Path.is_dir()`)

**Warnings (non-fatal):**
- Unsupported file type → Log `WARNING`, skip file, continue enumeration

### Implementation Details

The source uses two internal methods:

```python
def _candidate_files(self) -> list[Path]:
    """Every file under the root, sorted; validates the root exists."""
    if self._root.is_file():
        return [self._root]
    if self._root.is_dir():
        return sorted(path for path in self._root.rglob("*") if path.is_file())
    raise SourceError(f"Source path is not a file or directory: {self._root}")

def _is_supported(self, path: Path) -> bool:
    """Whether path has a registered extractor; warn-and-skip if not."""
    if path.suffix.lower() in supported_suffixes():
        return True
    logger.warning("Skipping unsupported file (no extractor for %r): %s", 
                   path.suffix, path)
    return False
```

## SharePoint Source (Future)

**Status:** Not yet implemented; deferred to phase 2.

The SharePoint source will:
- Implement the same `DocumentSource` protocol
- Use Microsoft Graph API (app-only auth via client credentials)
- Enumerate files from a SharePoint site or library
- Return the same `Iterable[Path]` contract

**Benefits:**
- CLI depends only on `DocumentSource` — no changes needed when SharePoint is added
- Local filesystem and SharePoint runs produce the same CSV output (same `filename` column format)

See [ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md) and [ADR-0010](../../spec/adr/0010-uniform-document-source.md) for the design rationale.

## Integration in the Pipeline

The CLI will:

1. **Instantiate** a source (e.g., `LocalFileSystemSource`)
2. **Enumerate** documents via `source.documents()`
3. **Process** each document (extract, classify, collect result)
4. **Write** results to CSV

```python
from sources import LocalFileSystemSource
from pathlib import Path

source = LocalFileSystemSource(Path("./documents/"))

for document_path in source.documents():
    # Extract and classify
    ...
```

## Testing & Mocking

In tests, you can create a fake source:

```python
class FakeSource:
    def __init__(self, paths: list[Path]):
        self._paths = paths
    
    def documents(self) -> Iterable[Path]:
        return self._paths

# Use in test
source = FakeSource([Path("test1.pdf"), Path("test2.docx")])
docs = source.documents()  # → [Path("test1.pdf"), Path("test2.docx")]
```

## Design Rationale

**Why a protocol?**
- Decouples the CLI from concrete source implementations
- Adding SharePoint doesn't require CLI changes

**Why warn-and-skip unsupported files?**
- Users can classify a mixed-format directory without preprocessing
- Unsupported files are logged but don't block the run

**Why reject unsupported files when pointed at directly?**
- Explicit error if the user expects a file to be processed but it's not supported
- Prevents silent failures when a user names the target file directly

**Why no symlink recursion?**
- Prevents infinite loops from circular symlinks
- Symlinks *to files* are still processed, so users can organize with links
- Directory traversal stays predictable and safe

See [../architecture/overview.md](../architecture/overview.md#a3-document-sources) for the role of A3 in the system and [../workflows/classification-pipeline.md](../workflows/classification-pipeline.md#step-4-enumerate-documents-a3) for the pipeline context.
