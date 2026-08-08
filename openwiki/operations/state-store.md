---
type: Operations Guide
title: PostgreSQL State Store
description: Database schema, ORM models, and lifecycle of documents and sync state in the cloud pipeline
resource: /src/db.py
tags: [operations, database, postgresql, state, persistence]
---

# PostgreSQL State Store

The cloud pipeline persists all state to PostgreSQL via [SQLAlchemy ORM models](../../src/db.py). This page documents the schema, models, lifecycle, and key invariants.

## Overview

**Three tables:**
1. **SyncState** — Walker position for one SharePoint library (delta token, resume token, status)
2. **Document** — One row per file (identity, status, hashes, classification result)
3. **ProcessingLog** — Per-attempt audit trail (outcome, token/cost accounting)

**Design decision:** [ADR-0013](../../spec/adr/0013-postgresql-state-store.md)

## SyncState Model

Tracks the walker's position for one document library across scheduled runs, enabling resumable enumeration.

```python
class SyncState(Base):
    __tablename__ = "sync_state"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drive_id: Mapped[str] = mapped_column(String, unique=True)  # SharePoint drive ID
    delta_token: Mapped[str | None]  # @odata.deltaLink (set only on completion)
    resume_token: Mapped[str | None] # @odata.nextLink (set on interruption)
    status: Mapped[WalkStatus]       # idle, walking, interrupted, completed
    last_synced_at: Mapped[datetime | None]  # Last *completed* walk
    updated_at: Mapped[datetime]     # Every write (including interruption)
```

### Status Lifecycle

```
idle
  ↓
walking (start of enumeration)
  ├─→ interrupted (budget exhausted mid-pagination)
  │     ↓
  │   (resume_token set, updated_at bumped)
  │     ↓
  │   walking (next scheduled run, resume from token)
  │     ↓
  │   [cycle repeats until completion]
  │
  └─→ completed (enumeration finished)
        ↓
      (delta_token set, resume_token cleared,
       last_synced_at stamped, updated_at bumped)
        ↓
      idle (until next scheduled run)
```

### Resumption Logic

**Next enumeration start point** (in order of priority):

1. If `resume_token` is set: resume from `resume_token` (interrupted walk)
   - Walker calls Graph with `$skiptoken=<resume_token>`
   - Continues pagination from where the previous run left off
2. Else if `delta_token` is set: use `delta_token` (incremental walk)
   - Walker calls Graph with `$deltatoken=<delta_token>`
   - Enumerates only items changed since the last completed walk
3. Else: full enumeration (first run ever)
   - Walker calls Graph `/delta` without a token
   - Fetches all items in the drive

### Key Invariants

- **Uniqueness:** One `SyncState` row per `drive_id`; a single walker process enumerates one drive
- **Completion marker:** `delta_token` is set **only** when a walk completes; as long as a walk is in progress or interrupted, `delta_token` from the previous completion remains unchanged
- **Distinct timestamps:** `last_synced_at` (last *completed* walk) is separate from `updated_at` (every write); this distinguishes between completion and interruption
- **Atomicity:** Walker updates the row atomically; concurrent readers see consistent state

## Document Model

One row per file enumerated by the walker. Tracks content hash, processing status, and classification result.

```python
class Document(Base):
    __tablename__ = "documents"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sync_state_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sync_state.id"))
    drive_item_id: Mapped[str]       # OneDrive item ID
    file_name: Mapped[str]           # e.g., "invoice.pdf"
    folder_path: Mapped[str]         # e.g., "/Matters/Smith-2026-001"
    mime_type: Mapped[str | None]    # e.g., "application/pdf"
    content_hash: Mapped[str | None] # SHA-256 of file bytes
    previous_hash: Mapped[str | None] # SHA-256 before last change
    status: Mapped[DocumentStatus]    # queued, processing, completed, skipped, pending, failed
    category: Mapped[str | None]      # Classification result
    confidence: Mapped[Decimal | None] # 0.0–1.0
    classification_override: Mapped[str | None]  # Manual veto
    retry_count: Mapped[int]          # Increment on each failed attempt
    error_message: Mapped[str | None] # Last failure reason
    last_synced_at: Mapped[datetime | None]
    created_at: Mapped[datetime]      # Row creation timestamp
    updated_at: Mapped[datetime]      # Last write timestamp
    
    # Unique constraint
    __table_args__ = (
        UniqueConstraint("sync_state_id", "drive_item_id"),
    )
```

### Status Lifecycle

```
┌──────────────────────────────────────────────┐
│ Walker finds file in SharePoint              │
└─────────────┬────────────────────────────────┘
              │
        ┌─────▼────────┐
        │ New file or  │
        │ hash changed?│
        └─────┬────────┘
              │ YES
              ▼
      ┌─────────────────┐
      │ INSERT Document │
      │ status=queued   │
      │ ENQUEUE Message │
      └─────────────────┘
              │
        ┌─────▼──────────────────┐
        │ Processor dequeues     │
        │ message and marks row  │
        │ status=processing      │
        └─────┬──────────────────┘
              │
        ┌─────┴────────────────────────────────────┐
        │                                           │
        ▼                                           ▼
    ┌─────────────┐                         ┌──────────────┐
    │ SUCCESS     │                         │ ERROR        │
    │ (classify)  │                         │ (graph/ext)  │
    └─────┬───────┘                         └────┬─────────┘
          │                                      │
          ▼                                      ▼
  ┌─────────────────┐                    ┌───────────────┐
  │ UPSERT Document │                    │ Mark FAILED   │
  │ status=completed│                    │ retry_count++ │
  │ category, conf  │                    │ error_message │
  └─────────────────┘                    └───────────────┘
          │                                      │
          ▼                                      ▼
    DELETE message                    Message stays in queue
   (success path)                    (redelivered by Azure)
                                            │
                                      ┌─────▼──────────┐
                                      │ If retry_count │
                                      │ exceeds max:   │
                                      │ POISON MESSAGE │
                                      └────────────────┘

┌──────────────────────────────────┐
│ Extraction unsupported (DOCX,    │
│ XLSX, etc.) or corrupt file      │
└────────────┬─────────────────────┘
             │
             ▼
      ┌─────────────────┐
      │ Mark SKIPPED    │
      │ (no retry)      │
      │ DELETE message  │
      └─────────────────┘

┌──────────────────────────────────┐
│ Operator manually requests       │
│ re-classification                │
└────────────┬─────────────────────┘
             │
             ▼
      ┌──────────────────┐
      │ Set status=pending│
      │ ENQUEUE Message  │
      │ (walker re-queues)
      └──────────────────┘
```

### Status Descriptions

| Status | Meaning | Who Sets | Transition |
| --- | --- | --- | --- |
| `queued` | File enqueued, awaiting processor | Walker | → `processing` on dequeue, → `pending` on manual request, → `queued` on hash change (re-queue) |
| `processing` | Processor acquired message, classifying | Processor | → `completed` on success, → `failed` on error, → `skipped` on unsupported format |
| `completed` | Successfully classified | Processor | (terminal; may transition back to `queued` if hash changes) |
| `skipped` | Unsupported format or corrupted file | Processor | (terminal for this version; walker re-queues if hash changes) |
| `pending` | Manual re-classification requested | Operator | → `queued` (walker enqueues) |
| `failed` | Classification attempt failed; awaiting retry | Processor | (requeue by walker, or operator intervention) |

### Hash Tracking

**Two hash fields:**

- **`content_hash`** — SHA-256 of current file bytes
- **`previous_hash`** — SHA-256 before the most recent change

**When walker finds a file:**

1. Calculate new hash
2. If new hash == existing `content_hash`: **skip** (no change, already classified)
3. If new hash != existing `content_hash` (or no `content_hash` yet):
   - Set `previous_hash = old content_hash`
   - Set `content_hash = new hash`
   - Set `status = queued`
   - Enqueue Message

**Use case:** Distinguishes between a file that has never been classified (no `content_hash`) and a file whose content changed after classification.

### Manual Override

**Field:** `classification_override` (optional string)

**Semantics:** Operator can manually set this field to override the automatic classification result. The processor checks this field before classification:

```python
if doc.classification_override:
    # Skip classification; use the override
    category = doc.classification_override
else:
    # Run normal classification
    category = self_consistency_classifier.classify(text)
```

**Invariant:** Processor never overwrites `classification_override`; once an operator sets it, that result persists even if the document is re-classified.

### Key Invariants

- **Unique identity:** `(sync_state_id, drive_item_id)` pair is unique; at most one Document row per file per library
- **Status rules:** Only one status at a time; transitions are well-defined (see diagram above)
- **Hash change detection:** Processor re-checks hash before download; if hash mismatches queued message, skip and let walker re-enqueue
- **Never overwrite override:** If `classification_override` is set, processor uses it and does not overwrite
- **Concurrent safety:** Multiple concurrent processors can UPSERT different documents safely; UPSERT on `(sync_state_id, drive_item_id)` key ensures last-write-wins for result
- **Audit trail:** ProcessingLog is append-only; every classification attempt is recorded

## ProcessingLog Model

Per-attempt audit trail for cost accounting, debugging, and compliance.

```python
class ProcessingLog(Base):
    __tablename__ = "processing_log"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"))
    status: Mapped[str]       # "completed", "skipped", or "failed"
    input_tokens: Mapped[int | None]  # LLM input tokens
    output_tokens: Mapped[int | None] # LLM output tokens
    total_cost: Mapped[Decimal | None] # Cost in USD (depends on model and inference provider)
    created_at: Mapped[datetime]      # When this attempt occurred
```

### Lifecycle

**One row per classification attempt** — every time the processor processes a message for a document:

```
Message dequeued for Document #42
  ↓
Processor starts classification
  ↓
After classification (success or error):
  INSERT ProcessingLog(
    document_id=42,
    status="completed" | "skipped" | "failed",
    input_tokens=1234,
    output_tokens=56,
    total_cost=0.00089,
    created_at=NOW()
  )
```

**Key property:** Append-only; rows are never updated or deleted. This ensures a complete audit trail of every attempt.

### Token and Cost Tracking

- **Input tokens:** Tokens sent to LLM (category definitions, document text)
- **Output tokens:** Tokens returned by LLM (structured response)
- **Total cost:** Calculated as `(input_tokens × input_rate + output_tokens × output_rate) × N` (where N is self-consistency votes)
- **Inference provider matters:** Anthropic vs Microsoft Foundry have different token rates; cost depends on `CLASSIFIER_PROVIDER` setting

## Schema Migrations

Database schema is versioned via [Alembic](../../alembic/). Each migration is a numbered Python file in `alembic/versions/`.

**Key files:**

- `alembic.ini` — Alembic configuration
- `alembic/env.py` — Migration runtime setup (imports config, uses SQLAlchemy engine)
- `alembic/versions/` — Individual migration scripts

**Common operations:**

```bash
# Run all pending migrations (deploy step)
alembic upgrade head

# Show current schema version
alembic current

# Generate a new migration after schema changes
alembic revision --autogenerate -m "Add new column"

# Rollback one migration
alembic downgrade -1
```

**Important:** The processor and walker assume the schema is up-to-date. Run `alembic upgrade head` once per deploy, before launching walker/processor jobs.

## Lazy Initialization

Database connections are lazily initialized to avoid opening a connection in the local CLI path:

```python
@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Lazily build and cache the SQLAlchemy engine."""
    settings = get_settings()
    return create_engine(settings.database_url)

@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Lazily build and cache the session factory."""
    engine = get_engine()
    return sessionmaker(bind=engine)
```

**Consequence:** The local CSV CLI (`main.py`) never opens a database connection; only walker and processor do.

## Concurrency & Isolation

**Multi-processor safety:**

- Multiple processor replicas can run concurrently
- Each holds a separate SQLAlchemy `Session`
- UPSERT on Document uses `(sync_state_id, drive_item_id)` unique key
- ProcessingLog is append-only

**Isolation level:** PostgreSQL default (READ COMMITTED) is sufficient; no need for higher isolation because:
- Document UPSERT is idempotent (keyed)
- ProcessingLog is insert-only
- Walker holds a session for the duration of the walk, updating SyncState once at start and once at end

## Related Pages

- [Cloud Pipeline Workflow](../workflows/cloud-pipeline.md) — How walker, processor, and database interact
- [Error Handling](error-handling.md) — Retry logic and error handling related to database failures
- [Deployment](deployment.md) — Schema migration as a deployment step
