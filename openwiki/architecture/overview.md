---
type: Architecture
title: System Architecture Overview
description: Layer breakdown, component relationships, and design patterns in the classifier system
resource: /src
tags: [architecture, design, layers, components]
---

# Architecture Overview

The system supports **two deployment paths** that share a common **classification core** but differ in how they enumerate documents, manage state, and persist results.

## Deployment Paths

### Path v1: Local CLI

Local single-machine batch processing:

```
                    ┌──────────────────────────┐
                    │  main.py (CLI Entry)     │
                    │  - Parse CLI args        │
                    │  - Orchestrate flow      │
                    └──────────────┬───────────┘
                                   │
        ┌──────────────────────────┴──────────────────────────────┐
        │                                                            │
        ▼                                                            ▼
┌────────────────────────┐                        ┌──────────────────────────┐
│  Categories (A1)       │                        │  Sources (A3)            │
│  Parse Markdown file   │                        │  LocalFileSystemSource   │
│  → CategorySet         │                        │  → enumerate files       │
└────────────────────────┘                        └──────────────────────────┘
        │                                                        │
        │                                                        ▼
        │                                        ┌──────────────────────────┐
        │                                        │  Extraction (A2)         │
        │                                        │  TextExtractor strategy  │
        │                                        │  (PDF, DOCX)             │
        │                                        │  → plain text            │
        │                                        └────────────┬─────────────┘
        │                                                     │
        └──────────────────┬──────────────────────────────────┘
                           ▼
                ┌──────────────────────────────┐
                │  Self-Consistency (B2)       │
                │  Run inner classifier N times│
                │  → Verdict (category, conf)  │
                └─────────────┬────────────────┘
                              │
        ┌─────────────────────┴──────────────────┐
        │                                         │
        ▼                                         ▼
┌──────────────────────┐              ┌──────────────────────────┐
│ Classifier (B1)      │              │  CSV Writer (A4)         │
│ Single LLM call      │              │  Write results CSV       │
│ Structured output    │              │  → file                  │
└──────────────────────┘              └──────────────────────────┘
```

### Path v2: Cloud Pipeline

Distributed multi-job system with SharePoint integration and PostgreSQL persistence:

```
                    ┌──────────────────────────┐
                    │  Scheduler               │
                    │  (Azure Container Apps)  │
                    └──────────────┬───────────┘
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │  walker.py (Producer)    │
                    │  - Scheduled job         │
                    │  - Time-budgeted run     │
                    └──────────────┬───────────┘
        ┌───────────────────────────┴──────────────────────────┐
        │                                                        │
        ▼                                                        ▼
┌──────────────────────────┐                        ┌──────────────────────────┐
│  Graph Client            │                        │  Message Queue           │
│  - Delta walk SharePoint │                        │  (Azure Queue)           │
│  - Resumable pagination  │                        │  - Enqueue Message       │
│  - Content hash          │                        │  - One item per file     │
│  → (file, hash, delta)   │                        │  → work_items            │
└──────────────────────────┘                        └────────────┬─────────────┘
        │                                                       │
        │   ┌─────────────────────────────────────────────────┐│
        └───┤ Persist: SyncState (delta, resume), Document    ││
            │ (hash, status, classification)                   ││
            └─────────────────────────────────────────────────┘│
                                                                │
                                                    ┌───────────┘
                                                    │
                                                    ▼
                                    ┌──────────────────────────┐
                                    │  KEDA Queue Scaler       │
                                    │  Spawn one replica per   │
                                    │  queued message          │
                                    └───────────┬──────────────┘
                                                │
                                                ▼
                                    ┌──────────────────────────┐
                                    │ processor.py (Consumer)  │
                                    │ - Queue-triggered job    │
                                    │ - Handles one item       │
                                    └───────────┬──────────────┘
        ┌──────────────────────────────────────┼────────────────────────┐
        │                                       │                        │
        ▼                                       ▼                        ▼
┌──────────────────────┐              ┌──────────────────┐    ┌──────────────────┐
│ Dequeue Message      │              │ Download from    │    │ Categories (A1)  │
│ (from queue)         │              │ SharePoint via   │    │ Parse category   │
│                      │              │ Graph            │    │ definitions      │
└──────────────────────┘              └──────────────────┘    └──────────────────┘
        │                                      │                       │
        └──────────────────┬────────────────────┴───────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ Extraction (A2)                      │
        │ Extract text from downloaded bytes   │
        │ (PDF, DOCX, or unsupported)          │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ Self-Consistency Classifier (B2)     │
        │ Run inner classifier N times         │
        │ → Verdict (category, confidence)     │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ Database Writer                      │
        │ UPSERT Document result               │
        │ Append ProcessingLog audit row       │
        │ → PostgreSQL                         │
        └──────────────────┬───────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ Delete Message from Queue            │
        │ (success path only)                  │
        └──────────────────────────────────────┘
```

## Shared Classification Core

Both paths use the same four layers for parsing categories, extracting text, and classifying documents.

### Layer Breakdown

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

**Input:** File path or bytes + MIME type
**Output:** Plain text

- Strategy pattern: each format has a registered `TextExtractor` implementation
- **PDF**: `PdfTextExtractor` uses `pypdf`, extracts page by page
- **DOCX**: `DocxTextExtractor` uses `python-docx`, extracts paragraphs then tables
- **Plain text** (`.txt`, `.json`, `.yml`, `.yaml`, `.md`, `.csv`, `.xml`): `PlainTextExtractor` decodes bytes with UTF-8-sig → Latin-1 fallback (ADR-0021)
- **Legacy `.doc`**: Intentionally deferred (ADR-0006, ADR-0009)
- MIME-based dispatch for cloud pipeline (bytes + MIME type) includes a `text/*` catch-all for robustness
- Unsupported formats raise `UnsupportedFormatError` when pointed at directly; skipped with a `WARNING` during directory enumeration
- All extraction errors are chained from the underlying library exception

**Design decision:** [ADR-0006](../../spec/adr/0006-text-extraction-per-format-libs.md), [ADR-0009](../../spec/adr/0009-defer-legacy-doc-extraction.md), [ADR-0021](../../spec/adr/0021-plain-text-extraction.md)

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

## Cloud-Specific Components

### Walker: Scheduled SharePoint Enumeration

The producer half of the two-job cloud pipeline ([ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md), [ADR-0014](../../spec/adr/0014-sharepoint-delta-walker.md)):

- **Entry point:** `python -m walker` (scheduled Azure Container Apps job)
- **Input:** SharePoint drive ID, library subtree path, time budget
- **Output:** Enqueued `Message` work items (one per changed/new file)
- **Key features:**
  - **Resumable:** Time-budgeted; large first enumerations spread across scheduled slots
  - **Delta walk:** Uses Microsoft Graph `@odata.deltaLink` and `@odata.nextLink` for efficient change detection
  - **Idempotent:** Tracks content hash and document status to prevent duplicate work
  - **Scoped:** Root path filters SharePoint items at the Graph level ([ADR-0019](../../spec/adr/0019-config-driven-walk-scope.md))

**Design decisions:** [ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md), [ADR-0014](../../spec/adr/0014-sharepoint-delta-walker.md), [ADR-0019](../../spec/adr/0019-config-driven-walk-scope.md)

### Message Queue: Walker→Processor Decoupling

Azure Queue Storage abstraction ([ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md)):

- **Wire format:** Pydantic `Message` serialized to JSON
- **Semantics:** At-least-once delivery; processor deletes message on success, queue redelivers on timeout
- **Dequeue count:** Azure's redelivery counter used for poison-message detection
- **Protocol:** `QueueBackend` is injected, so unit tests fake the queue without touching Azure

**Design decision:** [ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md)

### Graph Client: SharePoint Download & Content Tracking

Microsoft Graph integration ([ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md), [ADR-0015](../../spec/adr/0015-graph-authenticated-download.md)):

- **Auth:** App-only credentials (client ID/secret) or managed identity (production)
- **Delta walk:** Efficient change enumeration with resumable pagination
- **Content hash:** Stores SHA-256 hash of file bytes to detect changes and prevent duplicate classifications ([ADR-0017](../../spec/adr/0017-graph-content-hash-field.md))
- **Download:** Fetches file bytes via Graph for extraction (processor only)

**Design decisions:** [ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md), [ADR-0015](../../spec/adr/0015-graph-authenticated-download.md), [ADR-0017](../../spec/adr/0017-graph-content-hash-field.md)

### Processor: Queue-Triggered Classification

The consumer half of the two-job cloud pipeline ([ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md)):

- **Entry point:** `python -m processor` (Azure Container Apps queue-triggered job)
- **Input:** One `Message` from the queue
- **Output:** Classified result in PostgreSQL, deleted from queue on success
- **Scaling:** Stateless; KEDA spawns one replica per queued message, scales to zero when queue drains
- **Lifecycle:** Dequeue → hash re-check → download bytes → extract text → classify → UPSERT result → delete message

**Design decision:** [ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md)

### PostgreSQL State Store

Durable, queryable state for the cloud pipeline ([ADR-0013](../../spec/adr/0013-postgresql-state-store.md)):

**Three ORM models:**

1. **SyncState** — Walker position per document library
   - `delta_token` — Terminal `@odata.deltaLink` (set only on completion)
   - `resume_token` — Current `@odata.nextLink` (set on interruption)
   - `status` — `idle`, `walking`, `interrupted`, or `completed`
   - Next walk resumes from `resume_token` > `delta_token` > full enumeration

2. **Document** — One row per file
   - Identity: `drive_item_id`, `sync_state_id`
   - Content: `file_name`, `folder_path`, `mime_type`
   - Hashes: `content_hash`, `previous_hash` (detects changes)
   - Status: `queued`, `processing`, `completed`, `skipped`, `pending`, `failed`
   - Result: `category`, `confidence`, `classification_override` (manual veto, never overwritten)
   - Tracking: `retry_count`, `error_message`, `last_synced_at`

3. **ProcessingLog** — Per-attempt audit trail
   - Outcome: `status` (completed, skipped, failed)
   - Metrics: `input_tokens`, `output_tokens`, `total_cost`
   - Timestamp: When the attempt completed

**Schema migrations:** [Alembic](../../alembic/) manages schema versioning; `alembic upgrade head` runs once per deploy.

**Design decision:** [ADR-0013](../../spec/adr/0013-postgresql-state-store.md)

### Database Writer: Result Persistence

Abstraction over PostgreSQL UPSERT ([ADR-0013](../../spec/adr/0013-postgresql-state-store.md)):

- **Keyed on:** `(sync_state_id, drive_item_id)` unique pair
- **Semantics:** UPSERT the Document row, append ProcessingLog audit entry
- **Invariant:** Never overwrites a manual `classification_override`; the processor skips the classification if override is set
- **Isolation:** Multiple concurrent processors can safely UPSERT the same file; last-write-wins for result, audit trail is append-only

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

**Local path:**
- `CategoryFileError` — Markdown parsing failed
- `SourceError` — Source path is missing or invalid
- `ExtractionError` / `UnsupportedFormatError` — Text extraction failed or format unsupported
- `ClassificationError` — API call or response parsing failed
- `OutputError` — CSV write failed

**Cloud path:**
- `GraphError` — Microsoft Graph auth, delta walk, or download failed
- `QueueError` — Azure Queue transport or malformed message
- `PersistenceError` — PostgreSQL write failed
- Poison message handling via `dequeue_count` threshold

See [../operations/error-handling.md](../operations/error-handling.md) for details.

## Testing Strategy

See [../testing.md](../testing.md) for the testing approach (unit tests with mocked classifier and API responses).
