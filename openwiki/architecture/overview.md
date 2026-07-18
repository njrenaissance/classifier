---
type: Architecture
title: System Architecture Overview
description: Layer breakdown, component relationships, and design patterns in the classifier system
resource: /src
tags: [architecture, design, layers, components]
---

# Architecture Overview

The classifier is organized into **four functional layers** that flow from document enumeration → extraction → classification → output:

```
┌──────────────────────────────────────────────────────────────────┐
│  CLI (C1)                                                          │
│  - Orchestrates sources, extraction, classification, output       │
│  - Wires configuration into components                           │
└──────────────────────────────────────────────────────────────────┘
                                 ▲
                                 │
      ┌──────────────────────────┴──────────────────────────────┐
      │                                                            │
      ▼                                                            ▼
┌─────────────────────────┐                    ┌──────────────────────────────┐
│ Sources (A3)            │                    │ Self-Consistency (B2)         │
│ DocumentSource protocol │                    │ & CSV Writer (A4)             │
│ - LocalFileSystemSource │                    │ - SelfConsistencyClassifier   │
│ - SharePoint (future)   │◄──────────────────►│ - ClassificationResult        │
└─────────────────────────┘                    │ - write_results_csv()         │
        │                                       └──────────────────────────────┘
        │                                                        ▲
        │                                                        │
        ▼                                                        │
┌──────────────────────────────┐                                 │
│ Extraction (A2)              │                                 │
│ - TextExtractor protocol     │                                 │
│ - PdfTextExtractor           │                                 │
│ - DocxTextExtractor          │                                 │
│ - extract_text()             ├────────────────┐                │
└──────────────────────────────┘                │                │
                                                ▼                │
                                        ┌──────────────────────┐ │
                                        │ Classifier Core (B1) │ │
                                        │ - Classifier class   │ │
                                        │ - classify()         │ │
                                        └──────────────────────┘ │
                                                │                │
                                                └────────────────┘
```

## Layer Breakdown

### A1: Category Parsing (`src/categories.py`)

**Input:** Markdown file with category definitions
**Output:** `CategorySet` (ordered categories + reserved `unknown`)

- Parses category Markdown format:
  - `## Name` — category heading
  - Prose before first bullet — optional description
  - Bullets (`-`/`*`/`+`) — few-shot examples
- Builds the runtime enum (`categories.names + ['unknown']`) used by the classifier's structured-output schema
- Raises `CategoryFileError` on parse failures

**Design decision:** [ADR-0001](../../spec/adr/0001-llm-based-classification.md)

### A2: Text Extraction (`src/extraction.py`)

**Input:** File path (PDF or DOCX)
**Output:** Plain text

- Strategy pattern: each format has a registered `TextExtractor` implementation
- **PDF**: `PdfTextExtractor` uses `pypdf`, extracts page by page
- **DOCX**: `DocxTextExtractor` uses `python-docx`, extracts paragraphs then tables
- **Legacy `.doc`**: Intentionally deferred (ADR-0006, ADR-0009)
- Unsupported formats raise `UnsupportedFormatError` when pointed at directly; skipped with a `WARNING` during directory enumeration
- All extraction errors are chained from the underlying library exception

**Design decision:** [ADR-0006](../../spec/adr/0006-text-extraction-per-format-libs.md), [ADR-0009](../../spec/adr/0009-defer-legacy-doc-extraction.md)

### A3: Document Sources (`src/sources.py`)

**Input:** Local file path or SharePoint location
**Output:** Iterable of document paths to classify

- `DocumentSource` protocol defines the contract
- `LocalFileSystemSource` implements the protocol:
  - Single file returns itself
  - Directory is walked recursively (symlinks not followed)
  - Unsupported files skipped with `WARNING`; invalid paths raise `SourceError`
- SharePoint source (future) will implement the same protocol, so the CLI depends only on `DocumentSource`, never on the source type

**Design decisions:** [ADR-0003](../../spec/adr/0003-cli-batch-interface.md), [ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md), [ADR-0010](../../spec/adr/0010-uniform-document-source.md)

### B1: Classification Core (`src/classifier.py`)

**Input:** Document text, category definitions
**Output:** Single category label (string)

- One API call per invocation
- Uses Claude Haiku 4.5 (ADR-0002) with structured output (ADR-0008)
- Builds a static **prompt-cache prefix** (the category block with definitions + few-shot examples + instructions) once, reused across all calls in a run
- Builds a JSON schema enum with the defined categories + `unknown` once
- The category block is byte-identical on every call, so the prompt-cache prefix remains valid
- Model cannot invent labels — the `enum` constraint ensures only defined labels are returned
- Temperature is configurable (default 0.4)

**Design decisions:** [ADR-0002](../../spec/adr/0002-model-haiku-4-5.md), [ADR-0008](../../spec/adr/0008-prompt-structured-output.md)

### B2: Self-Consistency & Confidence (`src/self_consistency.py`)

**Input:** Document text, inner `Classifier`
**Output:** `Verdict` (category + confidence score)

- Calls the inner `Classifier` N times (default 5) and counts the labels
- Confidence = agreement rate of the modal label (`modal_count / N`)
- **Tie-breaking rule**: If the top two labels have the same count, resolve to `unknown`
- **Threshold rule**: If confidence is at or below the configured threshold (default 0.6), resolve to `unknown`
- Temperature variation across the N runs comes from the inner `Classifier`'s temperature setting

**Design decision:** [ADR-0005](../../spec/adr/0005-confidence-self-consistency.md)

### A4: CSV Output (`src/writer.py`)

**Input:** Iterable of `ClassificationResult` (filename, category, confidence)
**Output:** CSV file (local filesystem)

- `ClassificationResult` dataclass owns the CSV shape; its `headers()` and `row()` methods drive the output format
- Writes columns: `filename`, `category`, `confidence` (2 decimals)
- Creates parent directories as needed
- Raises `OutputError` on write failures

**Design decision:** [ADR-0004](../../spec/adr/0004-csv-file-output.md)

## Design Patterns

### Strategy Pattern
- **Text extraction**: Each format has its own extractor (`PdfTextExtractor`, `DocxTextExtractor`), registered in `_EXTRACTORS` dict. Adding a format is one registration.
- **Document sources**: Future SharePoint source will implement `DocumentSource` alongside `LocalFileSystemSource`.

### Protocol (Structural Typing)
- `DocumentSource` — Any class with a `documents()` method that yields `Path` objects
- `TextExtractor` — Any class with an `extract(path: Path) -> str` method

### Dependency Injection
- `Classifier` and `SelfConsistencyClassifier` receive the Anthropic client and inner classifier, so the network boundary is fakeable in tests
- The CLI will inject `CategorySet`, extractors, and sources when wiring components together

### Prompt Caching
- The category block (static prefix) is built once and reused across all classification calls in a run
- Byte-identical rendering ensures the prompt-cache prefix remains valid for the Anthropic API

## Configuration & Startup

See [../operations/configuration.md](../operations/configuration.md) for environment variables and the config singleton pattern.

## Error Handling

Every error derives from `AppError`, so a caller can catch one failure mode without swallowing unrelated ones:

- `CategoryFileError` — Markdown parsing failed
- `SourceError` — Source path is missing or invalid
- `ExtractionError` / `UnsupportedFormatError` — Text extraction failed or format unsupported
- `ClassificationError` — API call or response parsing failed
- `OutputError` — CSV write failed

See [../operations/error-handling.md](../operations/error-handling.md) for details.

## Testing Strategy

See [../testing.md](../testing.md) for the testing approach (unit tests with mocked classifier and API responses).
